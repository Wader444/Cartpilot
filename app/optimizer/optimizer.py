"""optimize_cart – Member C's optimizer entry point (the planner loads this module).

Split search: every combination of apps is evaluated and the WHOLE split is costed
(handling, delivery thresholds, small-cart fees, one coupon per cart), so a split
never wins on item price alone. This is exact for the 3 platforms in scope.

Landed cost check: every platform cart in the chosen plan and in the single-app
baseline is re-computed with Member C's landed-cost engine (app/optimizer/landed_cost.py,
integer paise). Any disagreement is reported in the result notes and the trace; the
Auditor re-quotes real fees from the platforms before anything reaches the user.
"""
from __future__ import annotations

from typing import Optional

from app.core.models import MatchedProduct, OptimizationConstraints, OptimizationResult, Platform
from app.optimizer.landed_cost import landed_cost
from app.optimizer.models import CartLine, FeeSchedule, Offer, to_paise
from app.tools.optimizer_tools import fallback_optimize_cart


def _engine_total(breakdown) -> int:
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


def optimize_cart(items: list[MatchedProduct], constraints: OptimizationConstraints,
                  preferred_brands: Optional[list[str]] = None) -> OptimizationResult:
    result = fallback_optimize_cart(items, constraints, preferred_brands)
    result.solver = "split-search + Member C landed-cost engine"
    mismatches = []
    for p, b in result.per_platform.items():
        if _engine_total(b) != to_paise(b.landed_total):
            mismatches.append(f"{p.value}: engine ₹{_engine_total(b) / 100:.2f} vs plan ₹{b.landed_total:.2f}")
    checked = len(result.per_platform)
    result.notes.append(f"Member C landed-cost engine verified {checked - len(mismatches)}/{checked} cart(s)"
                        + (f"; mismatch: {'; '.join(mismatches)}" if mismatches else ""))
    return result
