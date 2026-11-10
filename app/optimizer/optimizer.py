"""optimize_cart – Member C's optimizer entry point (the planner loads this module).

1. Exhaustive split search (every combination of apps, whole split costed, then single-item
   improvement moves) – always runs; it is the baseline and the safety net.
2. OR-Tools CP-SAT model of the same problem (app/optimizer/cpsat.py) – used when the
   `ortools` package is installed (single thread, so it is deterministic). If it finds a
   strictly cheaper plan, that plan is used; if it reaches the same cost, the search plan is
   kept and recorded as confirmed optimal; a worse plan is ignored and the note says so.
3. Every chosen platform cart is re-checked with Member C's landed-cost engine
   (app/optimizer/landed_cost.py, integer paise); disagreements are reported in the notes.
The Auditor still re-quotes real fees from the platforms before anything reaches the user.
"""
from __future__ import annotations

from typing import Optional

from app.core.guardrails import r2
from app.core.models import (
    CartItem,
    MatchBand,
    MatchCandidate,
    MatchedProduct,
    OptimizationConstraints,
    OptimizationResult,
    Platform,
    PlatformBreakdown,
)
from app.core.units import packs_needed
from app.optimizer import cpsat
from app.optimizer.landed_cost import landed_cost
from app.optimizer.models import CartLine, FeeSchedule, Offer, to_paise
from app.tools.optimizer_tools import _usable, fallback_optimize_cart, landed


def _engine_total(breakdown: PlatformBreakdown) -> int:
    """Re-compute one platform cart with Member C's engine (paise)."""
    cart = [CartLine(sku_id=i.listing.product_id, price=to_paise(i.listing.price), qty=i.packs,
                     quantity_value=i.listing.product.quantity_value, quantity_unit=i.listing.product.quantity_unit)
            for i in breakdown.items]
    fees = FeeSchedule(handling=to_paise(breakdown.handling), delivery=to_paise(breakdown.delivery),
                       small_cart_fee=to_paise(breakdown.small_cart),
                       small_cart_threshold=10 ** 12 if breakdown.small_cart else 0,
                       surge=to_paise(breakdown.surge))
    # one coupon per cart (how the apps work): pass only the coupon the optimizer chose
    offers = [Offer(label=breakdown.offer_id, value=to_paise(breakdown.offer_discount))] if breakdown.offer_id else []
    return landed_cost(cart, fees, offers).landed_total


def _allowed(c: OptimizationConstraints) -> list[Platform]:
    return [p for p in Platform if p not in c.excluded_platforms
            and not (c.minutes_to_deadline is not None and p in c.platform_eta
                     and c.platform_eta[p] > c.minutes_to_deadline)]


def _result_from(items: list[MatchedProduct], assign: dict[int, MatchCandidate], c: OptimizationConstraints,
                 base: OptimizationResult, solver: str) -> OptimizationResult:
    per: dict[Platform, list[CartItem]] = {}
    for i, cand in assign.items():
        it = items[i].request_item
        p = cand.listing.product
        packs = packs_needed(it.quantity, it.unit, p.quantity_value, p.quantity_unit)
        per.setdefault(cand.listing.platform, []).append(CartItem(
            platform=cand.listing.platform, request_item=it.name, listing=cand.listing, packs=packs,
            line_total=r2(cand.listing.price * packs), is_substitute=cand.is_substitute,
            match_confidence=cand.confidence, requires_confirmation=cand.band == MatchBand.CONFIRM))
    breakdown = {}
    for p, lines in per.items():
        it_total = r2(sum(l.line_total for l in lines))
        lt, parts = landed(p, it_total, c)
        breakdown[p] = PlatformBreakdown(platform=p, item_total=it_total, handling=parts["handling"],
                                         delivery=parts["delivery"], small_cart=parts["small_cart"],
                                         surge=parts["surge"], offer_discount=parts["offer_discount"],
                                         offer_id=parts["offer_id"], landed_total=lt,
                                         eta_mins=c.platform_eta.get(p), items=lines)
    total = r2(sum(b.landed_total for b in breakdown.values()))
    return base.model_copy(update={
        "assignments": [l for lines in per.values() for l in lines], "per_platform": breakdown,
        "total_landed": total,
        "savings_vs_single": r2(base.best_single_total - total) if base.best_single_total is not None else None,
        "within_budget": c.budget is None or total <= c.budget + 0.01, "solver": solver,
        "notes": list(base.notes)})


def optimize_cart(items: list[MatchedProduct], constraints: OptimizationConstraints,
                  preferred_brands: Optional[list[str]] = None) -> OptimizationResult:
    search = fallback_optimize_cart(items, constraints, preferred_brands)
    result = search.model_copy(update={"solver": "split search", "notes": list(search.notes)})

    if search.feasible and search.per_platform:
        pref = {b.lower() for b in (preferred_brands or [])}
        allowed = _allowed(constraints)
        usable = {i: [x for x in _usable(m, pref) if x.listing.platform in allowed] for i, m in enumerate(items)}
        try:
            assign = cpsat.solve(items, usable, allowed, constraints)
        except Exception as e:  # never let the solver break planning
            assign = None
            result.notes.append(f"OR-Tools CP-SAT failed ({type(e).__name__}); split search used")
        if assign is not None:
            candidate = _result_from(items, assign, constraints, search, "OR-Tools CP-SAT")
            if candidate.total_landed < search.total_landed - 0.01:      # strictly cheaper -> use it
                result = candidate
                result.notes.append(f"OR-Tools CP-SAT found a cheaper plan ₹{candidate.total_landed:.2f} "
                                    f"(split search ₹{search.total_landed:.2f})")
            elif candidate.total_landed <= search.total_landed + 0.01:   # same cost -> keep the search plan
                result.solver = "OR-Tools CP-SAT (confirmed optimum)"
                result.notes.append(f"OR-Tools CP-SAT confirmed the optimum ₹{search.total_landed:.2f}")
            else:
                result.notes.append(f"OR-Tools plan ₹{candidate.total_landed:.2f} was worse than the split search "
                                    f"₹{search.total_landed:.2f}; split search used")
        elif not any("CP-SAT failed" in n for n in result.notes):
            result.notes.append("OR-Tools not installed – split search used")

    mismatches = [f"{p.value}: engine ₹{_engine_total(b) / 100:.2f} vs plan ₹{b.landed_total:.2f}"
                  for p, b in result.per_platform.items() if _engine_total(b) != to_paise(b.landed_total)]
    checked = len(result.per_platform)
    result.notes.append(f"Member C landed-cost engine verified {checked - len(mismatches)}/{checked} cart(s)"
                        + (f"; mismatch: {'; '.join(mismatches)}" if mismatches else ""))
    return result
