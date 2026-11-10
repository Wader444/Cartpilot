"""Cart split as a constraint-optimisation problem, solved with Google OR-Tools CP-SAT.

Decision variables
  x[i,c]  item i is bought as candidate listing c        (exactly one per item)
  y[p]    platform p is used                              (x[i,c] <= y[p(c)])
  f[p]    platform p's cart reaches free delivery         (total_p >= threshold * f[p])
  s[p]    platform p pays the small-cart fee              (total_p >= small_threshold * (y[p] - s[p]))
  z[p,o]  coupon o is applied on platform p               (at most one coupon per cart, min cart value)
  d[p,o]  discount amount of that coupon                  (flat value, or percent of the cart up to a cap)
Objective: minimise  Σ items + handling + surge + delivery + small-cart fees − discounts   (integer paise)

Platforms excluded by the user or by the deadline (ETA) are not offered to the model.
Bank/payment-method offers are never auto-applied (same rule as the rest of the system).
"""
from __future__ import annotations

from typing import Optional

from app.core.models import MatchCandidate, MatchedProduct, OptimizationConstraints, Platform
from app.core.units import packs_needed


def _paise(x: float) -> int:
    return int(round(float(x) * 100))


def solve(items: list[MatchedProduct], usable: dict[int, list[MatchCandidate]], allowed: list[Platform],
          c: OptimizationConstraints, time_limit_s: float = 5.0) -> Optional[dict[int, MatchCandidate]]:
    """Return {item index: chosen candidate}, or None if OR-Tools is unavailable / no solution."""
    try:
        from ortools.sat.python import cp_model
    except ImportError:
        return None

    m = cp_model.CpModel()
    new_bool = getattr(m, "new_bool_var", None) or m.NewBoolVar
    new_int = getattr(m, "new_int_var", None) or m.NewIntVar
    add = getattr(m, "add", None) or m.Add
    minimize = getattr(m, "minimize", None) or m.Minimize

    active = [i for i, cs in usable.items() if cs]
    line = {}  # (i, k) -> paise
    for i in active:
        it = items[i].request_item
        for k, cand in enumerate(usable[i]):
            p = cand.listing.product
            line[(i, k)] = _paise(cand.listing.price) * packs_needed(it.quantity, it.unit, p.quantity_value, p.quantity_unit)
    ub = sum(max(line[(i, k)] for k in range(len(usable[i]))) for i in active) + 1

    x = {(i, k): new_bool(f"x_{i}_{k}") for i in active for k in range(len(usable[i]))}
    y = {p: new_bool(f"y_{p.value}") for p in allowed}
    tot = {p: new_int(0, ub, f"tot_{p.value}") for p in allowed}
    for i in active:
        add(sum(x[(i, k)] for k in range(len(usable[i]))) == 1)
        for k, cand in enumerate(usable[i]):
            add(x[(i, k)] <= y[cand.listing.platform])
    for p in allowed:
        terms = [line[(i, k)] * x[(i, k)] for i in active for k, cand in enumerate(usable[i])
                 if cand.listing.platform == p]
        add(tot[p] == (sum(terms) if terms else 0))
        if terms:
            add(y[p] <= sum(x[(i, k)] for i in active for k, cand in enumerate(usable[i])
                            if cand.listing.platform == p))
        else:
            add(y[p] == 0)

    cost = []
    for p in allowed:
        sched = c.fee_schedules.get(p)
        if sched is None:          # no verified fee schedule -> platform cannot be used
            add(y[p] == 0)
            continue
        cost.append(tot[p])
        cost.append((_paise(sched.handling_fee) + _paise(sched.surge_fee)) * y[p])
        # delivery: charged unless the cart reaches the free-delivery threshold
        dfee = _paise(sched.delivery_fee)
        if dfee:
            if sched.free_delivery_threshold is not None:
                f = new_bool(f"free_{p.value}")
                add(f <= y[p])
                add(tot[p] >= _paise(sched.free_delivery_threshold) * f)
                cost.append(dfee * y[p] - dfee * f)
            else:
                cost.append(dfee * y[p])
        # small-cart fee: charged when 0 < total < threshold
        sfee = _paise(sched.small_cart_fee)
        if sfee and sched.small_cart_threshold is not None:
            s = new_bool(f"small_{p.value}")
            add(s <= y[p])
            add(tot[p] >= _paise(sched.small_cart_threshold) * y[p] - _paise(sched.small_cart_threshold) * s)
            cost.append(sfee * s)
        # one coupon per cart
        zs = []
        for j, o in enumerate(c.offers.get(p, [])):
            if o.requires_payment_method:
                continue
            z = new_bool(f"z_{p.value}_{j}")
            d = new_int(0, ub, f"d_{p.value}_{j}")
            add(tot[p] >= _paise(o.min_cart_value) * z)
            if o.discount_type == "flat":
                add(d == _paise(o.value) * z)
            else:
                add(d * 10000 <= int(round(o.value * 100)) * tot[p])   # d <= value% of the cart, in paise
                add(d <= (_paise(o.max_discount) if o.max_discount is not None else ub) * z)
            zs.append(z)
            cost.append(-d)
        if zs:
            add(sum(zs) <= y[p])

    minimize(sum(cost))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 1          # deterministic: same input -> same plan
    status = (getattr(solver, "solve", None) or solver.Solve)(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    value = getattr(solver, "value", None) or solver.Value
    chosen = {}
    for i in active:
        for k, cand in enumerate(usable[i]):
            if value(x[(i, k)]):
                chosen[i] = cand
                break
    return chosen if len(chosen) == len(active) else None
