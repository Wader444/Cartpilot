"""Optimizer tools – interface to Member C's landed-cost engine + OR-Tools optimizer.

Interface (Member C implements in CARTPILOT_OPTIMIZER_MODULE, default
`app.optimizer.optimizer`):

    optimize_cart(items: list[MatchedProduct],
                  constraints: OptimizationConstraints) -> OptimizationResult

Until then this module provides an exact fallback for 3 platforms: enumerate
every subset of platforms (7), assign items cheapest-first, then improve with
single-item moves. It costs the WHOLE split (per-platform fees, small-cart
fees, offer thresholds) so a split never wins just on item price.

Optimizer figures are planning estimates from the platforms' own fee
schedules. The Auditor re-quotes fees and offers on the final carts, and only
those re-quoted figures appear in the proposal.
"""
from __future__ import annotations

import importlib
from itertools import combinations
from typing import Callable, Optional

from app.core.guardrails import r2
from app.core.models import (
    CartItem,
    FeeSchedule,
    MatchBand,
    MatchCandidate,
    MatchedProduct,
    Offer,
    OptimizationConstraints,
    OptimizationResult,
    Platform,
    PlatformBreakdown,
)
from app.core.trace import ToolCallError, TraceRecorder
from app.core.units import packs_needed


# --------------------------------------------------------------------------- #
# Landed cost (mirrors the contract: item_total + fees - best offer)
# --------------------------------------------------------------------------- #
def fees_for(schedule: FeeSchedule, item_total: float) -> dict[str, float]:
    delivery = 0.0 if (schedule.free_delivery_threshold is not None
                       and item_total >= schedule.free_delivery_threshold) else schedule.delivery_fee
    small = schedule.small_cart_fee if (schedule.small_cart_threshold is not None
                                        and 0 < item_total < schedule.small_cart_threshold) else 0.0
    return {"handling": float(schedule.handling_fee), "delivery": float(delivery),
            "small_cart": float(small), "surge": float(schedule.surge_fee)}


def best_offer(offers: list[Offer], item_total: float) -> tuple[float, Optional[str]]:
    """One coupon per cart; bank/payment-method offers are never auto-applied."""
    best, best_id = 0.0, None
    for o in offers:
        if o.requires_payment_method or item_total < o.min_cart_value:
            continue
        amt = o.value if o.discount_type == "flat" else item_total * o.value / 100
        if o.max_discount is not None:
            amt = min(amt, o.max_discount)
        if amt > best:
            best, best_id = r2(amt), o.offer_id
    return best, best_id


def landed(platform: Platform, item_total: float, c: OptimizationConstraints) -> tuple[float, dict]:
    sched = c.fee_schedules.get(platform, FeeSchedule())
    f = fees_for(sched, item_total)
    disc, oid = best_offer(c.offers.get(platform, []), item_total)
    total = item_total + sum(f.values()) - disc
    return r2(total), {**f, "offer_discount": disc, "offer_id": oid}


# --------------------------------------------------------------------------- #
# Fallback optimiser
# --------------------------------------------------------------------------- #
BRAND_PREMIUM = 0.10  # pay at most 10 % more per item to keep a brand the user approved before


def _line(m: MatchedProduct, c: MatchCandidate) -> float:
    it = m.request_item
    packs = packs_needed(it.quantity, it.unit, c.listing.product.quantity_value, c.listing.product.quantity_unit)
    return r2(c.listing.price * packs)


def _usable(m: MatchedProduct, preferred_brands: set[str]) -> list[MatchCandidate]:
    cands = [c for c in m.candidates if c.listing.in_stock and c.band != MatchBand.ASK]
    autos = [c for c in cands if c.band == MatchBand.AUTO]
    cands = autos or cands
    if m.request_item.brand:  # brand named in this request: keep it if available
        named = [c for c in cands if c.listing.product.brand.lower() == m.request_item.brand.lower()]
        if named:
            return named
    pref = [c for c in cands if c.listing.product.brand.lower() in preferred_brands]
    if pref and cands:
        cheapest = min(_line(m, c) for c in cands)
        if min(_line(m, c) for c in pref) <= cheapest * (1 + BRAND_PREMIUM):
            return pref  # soft preference: only when it costs little extra
    return cands


def fallback_optimize_cart(items: list[MatchedProduct], c: OptimizationConstraints,
                           preferred_brands: Optional[list[str]] = None) -> OptimizationResult:
    pref = {b.lower() for b in (preferred_brands or [])}
    allowed = [p for p in Platform if p not in c.excluded_platforms
               and not (c.minutes_to_deadline is not None and p in c.platform_eta
                        and c.platform_eta[p] > c.minutes_to_deadline)]
    notes: list[str] = []
    excluded_eta = [p.value for p in Platform if p not in allowed and p not in c.excluded_platforms]
    if excluded_eta:
        notes.append(f"Excluded for ETA beyond deadline: {', '.join(excluded_eta)}")

    usable = {i: [x for x in _usable(m, pref) if x.listing.platform in allowed] for i, m in enumerate(items)}
    unassigned = [items[i].request_item.name for i, cs in usable.items() if not cs]
    active = [i for i, cs in usable.items() if cs]

    def total_of(assign: dict[int, MatchCandidate]) -> float:
        per: dict[Platform, float] = {}
        for i, cand in assign.items():
            per[cand.listing.platform] = per.get(cand.listing.platform, 0) + _line(items[i], cand)
        return r2(sum(landed(p, r2(v), c)[0] for p, v in per.items()))

    def solve(subset: tuple[Platform, ...]) -> Optional[dict[int, MatchCandidate]]:
        assign: dict[int, MatchCandidate] = {}
        for i in active:
            opts = [x for x in usable[i] if x.listing.platform in subset]
            if not opts:
                return None
            assign[i] = min(opts, key=lambda x: (_line(items[i], x), -x.confidence))
        # local search: single-item moves while they reduce the landed total
        for _ in range(50):
            base = total_of(assign)
            best_move, best_total = None, base
            for i in active:
                for x in usable[i]:
                    if x.listing.platform not in subset or x is assign[i]:
                        continue
                    trial = dict(assign)
                    trial[i] = x
                    t = total_of(trial)
                    if t < best_total - 0.009:
                        best_move, best_total = (i, x), t
            if best_move is None:
                break
            assign[best_move[0]] = best_move[1]
        return assign

    best: Optional[tuple[tuple, int, dict]] = None
    best_single: Optional[tuple[float, Platform, dict]] = None
    for k in range(1, len(allowed) + 1):
        for subset in combinations(allowed, k):
            assign = solve(subset)
            if assign is None:
                continue
            t = total_of(assign)
            used = {a.listing.platform for a in assign.values()}
            n_used = len(used)
            not_preferred = len(used - set(c.preferred_platforms)) if c.preferred_platforms else 0
            if k == 1 and (best_single is None or t < best_single[0]):
                best_single = (t, subset[0], assign)
            key = (t, n_used, not_preferred)  # ties go to fewer apps, then to the user's preferred apps
            if best is None or key < best[0]:
                best = (key, n_used, assign)

    if best is None:
        return OptimizationResult(feasible=False, unassigned_items=[m.request_item.name for m in items],
                                  solver="fallback-subset-enumeration",
                                  notes=notes + ["No platform combination can supply the basket"])

    def to_item(i: int, cand: MatchCandidate) -> CartItem:
        m = items[i]
        packs = packs_needed(m.request_item.quantity, m.request_item.unit,
                             cand.listing.product.quantity_value, cand.listing.product.quantity_unit)
        return CartItem(platform=cand.listing.platform, request_item=m.request_item.name, listing=cand.listing,
                        packs=packs, line_total=r2(cand.listing.price * packs), is_substitute=cand.is_substitute,
                        match_confidence=cand.confidence, requires_confirmation=cand.band == MatchBand.CONFIRM)

    (total, _, _), _, assign = best
    per: dict[Platform, list[CartItem]] = {}
    for i, cand in assign.items():
        per.setdefault(cand.listing.platform, []).append(to_item(i, cand))
    breakdown = {}
    for p, lines in per.items():
        it = r2(sum(l.line_total for l in lines))
        lt, parts = landed(p, it, c)
        breakdown[p] = PlatformBreakdown(platform=p, item_total=it, handling=parts["handling"],
                                         delivery=parts["delivery"], small_cart=parts["small_cart"],
                                         surge=parts["surge"], offer_discount=parts["offer_discount"],
                                         offer_id=parts["offer_id"], landed_total=lt,
                                         eta_mins=c.platform_eta.get(p), items=lines)
    within = c.budget is None or total <= c.budget + 0.01
    if not within:
        notes.append(f"Cheapest valid plan ₹{total:.2f} is above the budget ₹{c.budget:.2f}")
    return OptimizationResult(
        assignments=[l for lines in per.values() for l in lines], per_platform=breakdown, total_landed=total,
        best_single_platform=best_single[1] if best_single else None,
        best_single_total=best_single[0] if best_single else None,
        best_single_items=[to_item(i, x) for i, x in best_single[2].items()] if best_single else [],
        savings_vs_single=r2(best_single[0] - total) if best_single else None,
        feasible=True, within_budget=within, unassigned_items=unassigned,
        solver="fallback-subset-enumeration", notes=notes)


# --------------------------------------------------------------------------- #
# Tool wrapper
# --------------------------------------------------------------------------- #
def _load(module_path: str) -> tuple[Optional[Callable], str]:
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, "optimize_cart"), module_path
    except Exception:
        return None, "fallback"


class OptimizerTools:
    def __init__(self, recorder: TraceRecorder, module_path: str = "app.optimizer.optimizer"):
        self.rec = recorder
        self._fn, self.source = _load(module_path)

    def optimize_cart(self, items: list[MatchedProduct], constraints: OptimizationConstraints,
                      preferred_brands: Optional[list[str]] = None, reason: str = "") -> Optional[OptimizationResult]:
        def run():
            if self._fn is not None:
                try:
                    import inspect

                    if len(inspect.signature(self._fn).parameters) >= 3:  # optimizer accepts brand preferences
                        return self._fn(items, constraints, preferred_brands)
                    return self._fn(items, constraints)
                except Exception as e:  # keep the demo alive: fall back, but say so in the trace
                    r = fallback_optimize_cart(items, constraints, preferred_brands)
                    r.notes.append(f"{self.source} failed ({type(e).__name__}); used fallback optimizer")
                    return r
            return fallback_optimize_cart(items, constraints, preferred_brands)

        try:
            result, _ = self.rec.call(
                "optimize_cart",
                {"items": [m.request_item.name for m in items], "budget": constraints.budget,
                 "minutes_to_deadline": constraints.minutes_to_deadline,
                 "excluded_platforms": [p.value for p in constraints.excluded_platforms],
                 "implementation": self.source},
                run, reason or "Find the cheapest valid split across platforms", max_retries=0,
                postprocess=lambda r, a: r if isinstance(r, OptimizationResult) else OptimizationResult.model_validate(r))
            return result
        except ToolCallError:
            return None
