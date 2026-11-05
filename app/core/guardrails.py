"""Guardrails: budget cap, approval/payment safety, diet + brand rules, and the
"zero invented numbers" check that every proposal must pass.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Optional

from app.core.models import (
    DIET_ALLOWED_TAGS,
    ActionStatus,
    AgentAction,
    DietPreference,
    Listing,
    Platform,
    ShoppingProposal,
)

MONEY_TOL = 0.011  # rupees; rounding tolerance


class GuardrailViolation(Exception):
    """Raised when a safety rule would be broken. Never caught silently."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


def r2(x: float) -> float:
    return round(float(x) + 1e-9, 2)


# --------------------------------------------------------------------------- #
# 1. Budget cap
# --------------------------------------------------------------------------- #
def within_budget(total: float, budget: Optional[float]) -> bool:
    return budget is None or total <= budget + MONEY_TOL


def enforce_budget(total: float, budget: Optional[float]) -> None:
    if not within_budget(total, budget):
        raise GuardrailViolation("BUDGET_CAP", f"Landed total ₹{total:.2f} exceeds budget ₹{budget:.2f}")


# --------------------------------------------------------------------------- #
# 2. Diet + brand rules
# --------------------------------------------------------------------------- #
def diet_allows(diet: Optional[DietPreference], listing: Listing) -> bool:
    if diet is None:
        return True
    return listing.product.diet_tag in DIET_ALLOWED_TAGS[diet]


def brand_avoided(listing: Listing, avoid_brands: Iterable[str]) -> bool:
    brand = listing.product.brand.lower()
    return any(b.strip().lower() == brand for b in avoid_brands if b.strip())


def never_substitute_rule(item_name: str, rules: Iterable[str],
                          known_brands: Iterable[str] = ()) -> tuple[bool, Optional[str]]:
    """Return (locked, required_brand) for an item.

    Rules look like "milk" (no substitutes for milk) or "amul milk" (only Amul milk).
    """
    item = " ".join(item_name.lower().split())
    brands = {b.lower() for b in known_brands}
    for rule in rules:
        rule_l = " ".join(rule.lower().split())
        if rule_l == item:
            return True, None
        if rule_l.endswith(" " + item):
            brand_part = rule_l[: -len(item)].strip()
            if not brands or brand_part in brands:
                return True, brand_part
    return False, None


# --------------------------------------------------------------------------- #
# 3. Payment / order safety
# --------------------------------------------------------------------------- #
_FORBIDDEN = re.compile(r"(^|_)(pay|payment|checkout|place_order|submit_order|charge|purchase)($|_)", re.I)


def forbidden_capabilities(obj: Any) -> list[str]:
    """Names on an object that look like payment / ordering capabilities."""
    return [n for n in dir(obj) if not n.startswith("__") and _FORBIDDEN.search(n)]


def assert_no_payment_capability(obj: Any) -> None:
    bad = forbidden_capabilities(obj)
    if bad:
        raise GuardrailViolation("PAYMENT_CAPABILITY", f"{type(obj).__name__} exposes {bad}")


# --------------------------------------------------------------------------- #
# 4. Zero invented numbers
# --------------------------------------------------------------------------- #
def _ok_actions(trace: list[AgentAction], tool: str) -> list[AgentAction]:
    return [a for a in trace if a.tool == tool and a.status == ActionStatus.SUCCESS and a.output is not None]


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= MONEY_TOL


def verify_proposal_numbers(proposal: ShoppingProposal, trace: list[AgentAction]) -> list[str]:
    """Check every number in a proposal against tool outputs in the trace.

    Allowed sources: a platform tool output (search_products, get_fees,
    get_offers, get_eta) or arithmetic over those outputs. Returns a list of
    violations (empty = clean).
    """
    violations: list[str] = []

    # index tool outputs
    prices: set[tuple[str, str, float]] = set()
    for a in _ok_actions(trace, "search_products"):
        for l in a.output or []:
            prices.add((l["platform"], l["product_id"], r2(l["price"])))
    fees: list[dict] = [a.output for a in _ok_actions(trace, "get_fees")]
    offers: list[tuple[str, float, list[dict]]] = []
    for a in _ok_actions(trace, "get_offers"):
        offers.append((a.input.get("platform"), r2(a.input.get("cart_value", -1)), a.output or []))
    etas = {(a.output["platform"], int(a.output["eta_mins"])) for a in _ok_actions(trace, "get_eta")}

    def fee_match(platform: str, cart_value: float, h: float, d: float, s: float, sg: float) -> bool:
        return any(f["platform"] == platform and _close(f["cart_value"], cart_value)
                   and _close(f["handling"], h) and _close(f["delivery"], d)
                   and _close(f["small_cart"], s) and _close(f["surge"], sg) for f in fees)

    def offer_match(platform: str, cart_value: float, discount: float, offer_id: Optional[str]) -> bool:
        for p, cv, offs in offers:
            if p != platform or not _close(cv, cart_value):
                continue
            if offer_id is None and _close(discount, 0):
                return True
            if any(o["offer_id"] == offer_id and o["applicable"] and _close(o["discount_amount"], discount)
                   for o in offs):
                return True
        return offer_id is None and _close(discount, 0)

    total = 0.0
    for cart in proposal.carts:
        p = cart.platform.value
        item_sum = 0.0
        for it in cart.items:
            key = (p, it.listing.product_id, r2(it.listing.price))
            if key not in prices:
                violations.append(f"price {it.listing.price} for {p}:{it.listing.product_id} not in any search_products output")
            if it.unit_price is not None:
                from app.core.units import unit_price as _up

                if not _close(it.unit_price, _up(it.listing.price, it.listing.product.quantity_value,
                                                 it.listing.product.quantity_unit)[0]):
                    violations.append(f"unit_price {it.unit_price} not derived from price for {it.listing.product_id}")
            if not _close(it.line_total, it.listing.price * it.packs):
                violations.append(f"line_total {it.line_total} != price x packs for {it.listing.product_id}")
            item_sum += it.line_total
        if not _close(cart.item_total, item_sum):
            violations.append(f"{p} item_total {cart.item_total} != sum of lines {r2(item_sum)}")
        if not fee_match(p, cart.item_total, cart.handling, cart.delivery, cart.small_cart, cart.surge):
            violations.append(f"{p} fees not found in any get_fees output for cart value {cart.item_total}")
        if not offer_match(p, cart.item_total, cart.offer_discount, cart.offer_id):
            violations.append(f"{p} offer discount {cart.offer_discount} not found in get_offers output")
        expected = cart.item_total + cart.handling + cart.delivery + cart.small_cart + cart.surge - cart.offer_discount
        if not _close(cart.landed_total, expected):
            violations.append(f"{p} landed_total {cart.landed_total} != components {r2(expected)}")
        if cart.eta_mins is not None and (p, cart.eta_mins) not in etas:
            violations.append(f"{p} eta {cart.eta_mins} not found in any get_eta output")
        total += cart.landed_total

    if proposal.carts and not _close(proposal.total_landed, total):
        violations.append(f"total_landed {proposal.total_landed} != sum of carts {r2(total)}")

    if proposal.best_single_total is not None:
        if proposal.best_single_platform is None:
            violations.append("best_single_total without a platform")
        else:
            bp = proposal.best_single_platform.value
            ok = False
            for f in fees:
                if f["platform"] != bp:
                    continue
                for p, cv, offs in offers:
                    if p != bp or not _close(cv, f["cart_value"]):
                        continue
                    disc = max([o["discount_amount"] for o in offs if o["applicable"]] or [0])
                    if _close(proposal.best_single_total, f["cart_value"] + f["total"] - disc):
                        ok = True
            if not ok:
                violations.append(f"best_single_total {proposal.best_single_total} not reproducible from tool outputs")
        if proposal.savings_vs_single is not None and not _close(
                proposal.savings_vs_single, proposal.best_single_total - proposal.total_landed):
            violations.append("savings_vs_single != best_single_total - total_landed")

    for prov in proposal.provenance:
        if not (prov.source.startswith("tool:") or prov.source == "derived"):
            violations.append(f"{prov.field} has non-tool provenance '{prov.source}'")
    return violations


def assert_no_invented_numbers(proposal: ShoppingProposal, trace: list[AgentAction]) -> None:
    v = verify_proposal_numbers(proposal, trace)
    if v:
        raise GuardrailViolation("INVENTED_NUMBER", "; ".join(v))


def platform_list(values: Iterable[str | Platform]) -> list[Platform]:
    out = []
    for v in values:
        try:
            out.append(Platform(v.value if isinstance(v, Platform) else str(v).lower()))
        except ValueError:
            continue
    return out
