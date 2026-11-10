"""Auditor Agent – independent verification immediately before a proposal.

It re-queries the platforms (it never reuses the planner's numbers) and checks:
  * price and stock of every chosen listing
  * fees on the actual cart vs the fees the optimizer planned with
  * that the planned offer still applies
  * ETA vs the deadline
  * unit-price oddities (bigger pack costs more per kg/L, quantity not convertible)
  * suspicious discounts against price history (inflated MRP)
  * budget
Anything it cannot check is reported as UNVERIFIED – never assumed fine.
"""
from __future__ import annotations

import importlib
from statistics import mean
from typing import Callable, Optional

from app.core.config import Settings, get_settings
from app.core.guardrails import r2, within_budget
from app.core.models import (
    AuditReport,
    AuditWarning,
    CartItem,
    CartLine,
    Listing,
    MatchBand,
    MatchedProduct,
    OptimizationResult,
    Platform,
    PlatformBreakdown,
    ShoppingRequest,
    WarningSeverity,
)
from app.core.trace import TraceRecorder
from app.core.units import same_dimension, to_base, unit_price
from app.tools.optimizer_tools import fees_for
from app.tools.platform_tools import PlatformTools


def load_price_history(module_path: str) -> Optional[Callable]:
    try:
        return getattr(importlib.import_module(module_path), "get_price_history")
    except Exception:
        return None


class AuditorAgent:
    def __init__(self, tools: PlatformTools, recorder: TraceRecorder, settings: Optional[Settings] = None,
                 price_history: Optional[Callable] = None):
        self.tools = tools
        self.rec = recorder
        self.settings = settings or get_settings()
        self.price_history = price_history or load_price_history(self.settings.price_history_module)

    # ------------------------------------------------------------------ public
    def audit(self, request: ShoppingRequest, opt: OptimizationResult, matches: list[MatchedProduct],
              minutes_to_deadline: Optional[int], schedules: Optional[dict] = None) -> AuditReport:
        report = AuditReport()
        self._schedules = schedules or {}
        cache: dict[tuple[Platform, str], Optional[list[Listing]]] = {}

        for platform, planned in opt.per_platform.items():
            self._audit_cart(platform, planned, request, minutes_to_deadline, report, cache)
            self._unit_price_checks(planned, matches, report)
            self._discount_checks(planned, report)

        self._audit_baseline(opt, request, report, cache)

        total = r2(sum(self._verified_landed(p, report) or 0 for p in opt.per_platform))
        if request.budget is not None and opt.per_platform and not within_budget(total, request.budget):
            report.warnings.append(AuditWarning(
                code="OVER_BUDGET", severity=WarningSeverity.CRITICAL,
                message=f"Verified total ₹{total:.2f} is above your budget ₹{request.budget:.2f}. "
                        f"Remove items or raise the budget – I will not place a plan above budget for approval.",
                details={"total": total, "budget": request.budget}))

        self.rec.note("auditor_summary", "Auditor finished independent re-verification",
                      output={"warnings": [w.code for w in report.warnings],
                              "replan_triggers": [w.code for w in report.warnings if w.triggers_replan],
                              "unverified": report.unverified})
        return report

    # ------------------------------------------------------------------ cart checks
    @staticmethod
    def _query(item: CartItem) -> str:
        p = item.listing.product
        if p.generic_name:
            return p.generic_name
        return p.name if item.is_substitute else item.request_item

    def _recheck(self, platform: Platform, item: CartItem, cache) -> Optional[Listing]:
        key = (platform, self._query(item))
        if key not in cache:
            cache[key] = self.tools.search_products(platform, key[1],
                                                    reason=f"Auditor re-checks price/stock of '{key[1]}'")
        results = cache[key]
        if results is None:
            return None
        return next((l for l in results if l.product_id == item.listing.product_id), None)

    def _audit_cart(self, platform: Platform, planned: PlatformBreakdown, request: ShoppingRequest,
                    minutes: Optional[int], report: AuditReport, cache) -> None:
        lines: list[CartLine] = []
        for item in planned.items:
            fresh = self._recheck(platform, item, cache)
            pid = item.listing.product_id
            if fresh is None:
                if cache.get((platform, self._query(item))) is None:
                    report.unverified.append(f"{platform.value}:{pid}")
                    report.warnings.append(AuditWarning(
                        code="UNVERIFIED", severity=WarningSeverity.CRITICAL, platform=platform, product_id=pid,
                        message=f"Could not re-check {item.listing.product.name} on {platform.value} (platform error)",
                        triggers_replan=True))
                else:
                    report.warnings.append(AuditWarning(
                        code="OUT_OF_STOCK", severity=WarningSeverity.WARNING, platform=platform, product_id=pid,
                        message=f"{item.listing.product.name} is no longer listed on {platform.value}",
                        triggers_replan=True))
                continue
            report.verified_listings[fresh.key] = fresh
            if not fresh.in_stock:
                report.warnings.append(AuditWarning(
                    code="OUT_OF_STOCK", platform=platform, product_id=pid, triggers_replan=True,
                    message=f"{fresh.product.name} went out of stock on {platform.value} since planning"))
            elif abs(fresh.price - item.listing.price) > 0.009:
                report.warnings.append(AuditWarning(
                    code="PRICE_CHANGED", platform=platform, product_id=pid, triggers_replan=True,
                    message=f"{fresh.product.name} on {platform.value}: ₹{item.listing.price:.0f} → ₹{fresh.price:.0f}",
                    details={"planned": item.listing.price, "now": fresh.price}))
            lines.append(CartLine(product_id=pid, quantity=item.packs))

        if not lines:
            return
        verified_total = r2(sum(report.verified_listings[f"{platform.value}:{l.product_id}"].price * l.quantity
                                for l in lines if f"{platform.value}:{l.product_id}" in report.verified_listings))

        fees = self.tools.get_fees(platform, lines, reason="Auditor re-quotes fees on the actual cart")
        if fees is None:
            report.unverified.append(f"{platform.value}:fees")
            report.warnings.append(AuditWarning(code="UNVERIFIED", severity=WarningSeverity.CRITICAL,
                                                platform=platform, triggers_replan=True,
                                                message=f"Could not verify fees on {platform.value}"))
        else:
            report.verified_fees[platform] = fees
            sched = self._schedules.get(platform)
            if sched is not None:  # same cart value, planning-time schedule -> isolates real fee changes
                exp = fees_for(sched, fees.cart_value)
                planned_fees = sum(exp.values())
                planned = planned.model_copy(update=exp)
            else:
                planned_fees = planned.handling + planned.delivery + planned.small_cart + planned.surge
            jump = fees.total - planned_fees
            limit = max(self.settings.fee_anomaly_abs, planned_fees * self.settings.fee_anomaly_pct)
            if jump > limit:
                comps = {k: (getattr(planned, k), getattr(fees, k)) for k in ("handling", "delivery", "small_cart", "surge")
                         if abs(getattr(planned, k) - getattr(fees, k)) > 0.009}
                report.warnings.append(AuditWarning(
                    code="FEE_ANOMALY", severity=WarningSeverity.WARNING, platform=platform, triggers_replan=True,
                    message=f"Fees on {platform.value} jumped ₹{planned_fees:.0f} → ₹{fees.total:.0f} since planning "
                            f"({', '.join(f'{k} ₹{a:.0f}→₹{b:.0f}' for k, (a, b) in comps.items())})",
                    details={"planned_fees": planned_fees, "now": fees.total, "components": comps}))

        offers = self.tools.get_offers(platform, lines, verified_total, reason="Auditor checks the offer still applies")
        if offers is None:
            report.unverified.append(f"{platform.value}:offers")
        else:
            report.verified_offers[platform] = offers
            if planned.offer_id and not any(o.offer_id == planned.offer_id and o.applicable for o in offers):
                report.warnings.append(AuditWarning(
                    code="OFFER_INVALID", platform=platform, triggers_replan=True,
                    message=f"Offer {planned.offer_id} no longer applies on {platform.value}"))

        eta = self.tools.get_eta(platform, lines, request.pincode, minutes, reason="Auditor re-checks delivery ETA")
        if eta is None:
            report.unverified.append(f"{platform.value}:eta")
            report.warnings.append(AuditWarning(code="UNVERIFIED", severity=WarningSeverity.WARNING, platform=platform,
                                                message=f"Could not verify the ETA on {platform.value}"))
        else:
            report.verified_etas[platform] = eta
            if eta.deadline_met is False:
                report.warnings.append(AuditWarning(
                    code="ETA_MISS", platform=platform, triggers_replan=True,
                    message=f"{platform.value} now quotes {eta.eta_mins} min – misses your {request.deadline} deadline"))

    def _verified_landed(self, platform: Platform, report: AuditReport) -> Optional[float]:
        fees = report.verified_fees.get(platform)
        if fees is None:
            return None
        disc = max([o.discount_amount for o in report.verified_offers.get(platform, []) if o.applicable] or [0])
        return r2(fees.cart_value + fees.total - disc)

    # ------------------------------------------------------------------ baseline
    def _audit_baseline(self, opt: OptimizationResult, request: ShoppingRequest, report: AuditReport, cache) -> None:
        if not opt.best_single_platform or not opt.best_single_items:
            return
        p = opt.best_single_platform
        lines = []
        for item in opt.best_single_items:
            fresh = self._recheck(p, item, cache)
            if fresh is None or not fresh.in_stock:
                report.warnings.append(AuditWarning(
                    code="UNVERIFIED", severity=WarningSeverity.INFO,
                    message="Single-app comparison changed during verification – savings not shown"))
                return
            report.verified_listings.setdefault(fresh.key, fresh)
            lines.append(CartLine(product_id=item.listing.product_id, quantity=item.packs))
        value = r2(sum(report.verified_listings[f"{p.value}:{l.product_id}"].price * l.quantity for l in lines))
        fees = self.tools.get_fees(p, lines, reason=f"Quote the best single-app basket on {p.value} for comparison")
        offers = self.tools.get_offers(p, lines, value, reason="Offers on the single-app comparison basket")
        if fees is None or offers is None:
            report.unverified.append("baseline")
            return
        report.baseline_fees, report.baseline_offers = fees, offers

    # ------------------------------------------------------------------ product checks
    def _unit_price_checks(self, planned: PlatformBreakdown, matches: list[MatchedProduct], report: AuditReport) -> None:
        by_item = {m.request_item.name: m for m in matches}
        for item in planned.items:
            m = by_item.get(item.request_item)
            prod = item.listing.product
            if m and not m.request_item.quantity_assumed and not same_dimension(m.request_item.unit, prod.quantity_unit):
                report.warnings.append(AuditWarning(
                    code="UNIT_PRICE", severity=WarningSeverity.INFO, platform=item.platform, product_id=prod.sku_id,
                    message=f"You asked for {m.request_item.quantity:g} {m.request_item.unit} of {m.request_item.name}; "
                            f"it is sold as {prod.quantity_value:g} {prod.quantity_unit} – please check the quantity"))
            if not m:
                continue
            same_platform = [c.listing for c in m.candidates if c.listing.platform == item.platform
                             and c.listing.in_stock and c.band == MatchBand.AUTO
                             and same_dimension(c.listing.product.quantity_unit, prod.quantity_unit)
                             and c.listing.product.brand == prod.brand]
            mine, label = unit_price(item.listing.price, prod.quantity_value, prod.quantity_unit)
            for other in same_platform:
                op = other.product
                if op.sku_id == prod.sku_id:
                    continue
                theirs, _ = unit_price(other.price, op.quantity_value, op.quantity_unit)
                bigger = to_base(prod.quantity_value, prod.quantity_unit)[1] > to_base(op.quantity_value, op.quantity_unit)[1]
                if bigger and mine > theirs + 0.01:
                    report.warnings.append(AuditWarning(
                        code="UNIT_PRICE", severity=WarningSeverity.INFO, platform=item.platform, product_id=prod.sku_id,
                        message=f"Bulk trap: {prod.name} costs ₹{mine}/{label}, more than the smaller "
                                f"{op.name} at ₹{theirs}/{label}"))

    def _discount_checks(self, planned: PlatformBreakdown, report: AuditReport) -> None:
        for item in planned.items:
            l = report.verified_listings.get(item.listing.key, item.listing)
            if l.mrp <= 0 or l.price >= l.mrp:
                continue
            claimed = 1 - l.price / l.mrp
            if claimed < 0.20:
                continue
            hist = self.price_history(l.platform, l.product_id) if self.price_history else []
            self.rec.note("get_price_history", f"Check the {claimed:.0%} 'discount' on {l.product.name}",
                          input={"platform": l.platform.value, "product_id": l.product_id},
                          output={"points": len(hist or []), "avg": r2(mean(h["price"] for h in hist)) if hist else None})
            if not hist:
                report.unverified.append(f"{l.key}:discount")
                report.warnings.append(AuditWarning(
                    code="UNVERIFIED", severity=WarningSeverity.INFO, platform=l.platform, product_id=l.product_id,
                    message=f"{l.product.name} shows {claimed:.0%} off MRP; no price history to verify it"))
                continue
            avg = mean(h["price"] for h in hist)
            if l.price >= avg * 0.97:
                report.warnings.append(AuditWarning(
                    code="SUSPICIOUS_DISCOUNT", platform=l.platform, product_id=l.product_id,
                    message=f"{l.product.name}: '{claimed:.0%} off' MRP ₹{l.mrp:.0f}, but it has sold around "
                            f"₹{avg:.0f} for 4 weeks – the discount looks inflated",
                    details={"mrp": l.mrp, "price": l.price, "avg_4w": r2(avg)}))
