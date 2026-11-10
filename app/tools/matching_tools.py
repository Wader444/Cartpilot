"""Matching tools – interface to Member B's Matching Agent.

Interface (Member B implements these in CARTPILOT_MATCHING_MODULE, default
`app.agents.matching_agent`; same signatures, same shared models):

    match_products(item: ItemRequest, listings: list[Listing],
                   constraints: MatchConstraints) -> MatchedProduct
    find_substitutes(item: ItemRequest,
                     constraints: MatchConstraints) -> list[SubstituteSuggestion]

Until that module exists, the rule-based fallback below is used. It is
conservative: anything that is not clearly the same product gets a confidence
below 0.85 and therefore needs user confirmation – it is never auto-substituted.
"""
from __future__ import annotations

import importlib
from typing import Callable, Optional

from pydantic import BaseModel, Field

from app.core.guardrails import brand_avoided, diet_allows, never_substitute_rule
from app.core.models import (
    ActionStatus,
    DietPreference,
    ItemRequest,
    Listing,
    MatchBand,
    MatchCandidate,
    MatchedProduct,
)
from app.core.trace import ToolCallError, TraceRecorder

AUTO = 0.85
CONFIRM = 0.60


class MatchConstraints(BaseModel):
    diet: Optional[DietPreference] = None
    avoid_brands: list[str] = Field(default_factory=list)
    preferred_brands: list[str] = Field(default_factory=list)
    never_substitute: list[str] = Field(default_factory=list)
    known_brands: list[str] = Field(default_factory=list)


class SubstituteSuggestion(BaseModel):
    query: str = Field(..., description="What to search for on the platforms")
    confidence: float = Field(..., ge=0, le=1)
    reason: str = ""


def band_for(conf: float) -> MatchBand:
    if conf >= AUTO:
        return MatchBand.AUTO
    if conf >= CONFIRM:
        return MatchBand.CONFIRM
    return MatchBand.ASK


# --------------------------------------------------------------------------- #
# Fallback implementation (rule-based, explainable)
# --------------------------------------------------------------------------- #
_SYNONYMS = {
    "wheat flour": "atta", "aata": "atta", "flour": "atta", "dahi": "curd", "yogurt": "curd",
    "yoghurt": "curd", "egg": "eggs", "tomatoes": "tomato", "onions": "onion", "potatoes": "potato",
    "aloo": "potato", "bananas": "banana", "apples": "apple", "palak": "spinach", "cooking oil": "oil",
    "sunflower oil": "oil", "refined oil": "oil", "dal": "toor dal", "arhar dal": "toor dal",
    "biscuit": "biscuits", "maggi": "noodles", "chai": "tea",
}

_SUBSTITUTES: dict[str, list[tuple[str, float, str]]] = {
    "paneer": [("tofu", 0.70, "SUBSTITUTE_FOR paneer: similar protein, vegan")],
    "milk": [("oat milk", 0.62, "SUBSTITUTE_FOR milk: plant-based, different taste")],
    "toor dal": [("moong dal", 0.72, "SUBSTITUTE_FOR toor dal: another everyday dal")],
    "moong dal": [("toor dal", 0.72, "SUBSTITUTE_FOR moong dal")],
    "tea": [("coffee", 0.40, "different beverage – would need explicit approval")],
    "butter": [],
}


def canonical(name: str) -> str:
    n = " ".join(name.lower().split())
    return _SYNONYMS.get(n, n)


def fallback_match_products(item: ItemRequest, listings: list[Listing],
                            constraints: MatchConstraints) -> MatchedProduct:
    target = canonical(item.name)
    locked, required_brand = never_substitute_rule(item.name, constraints.never_substitute, constraints.known_brands)
    if item.brand and not required_brand and locked:
        required_brand = item.brand.lower()
    preferred = {b.lower() for b in constraints.preferred_brands}

    candidates: list[MatchCandidate] = []
    excluded: list[str] = []
    for l in listings:
        p = l.product
        if not diet_allows(constraints.diet, l):
            excluded.append(f"{p.name} ({p.diet_tag.value} not allowed for {constraints.diet.value})")
            continue
        if brand_avoided(l, constraints.avoid_brands):
            excluded.append(f"{p.name} (brand {p.brand} rejected earlier)")
            continue
        brand = p.brand.lower()
        if required_brand and brand != required_brand:
            excluded.append(f"{p.name} (never-substitute rule requires {required_brand})")
            continue

        generic = canonical(p.generic_name or "")
        if generic == target:
            conf, reason = 0.95, f"same item '{target}'"
        elif target in p.name.lower().split():
            conf, reason = 0.72, f"name mentions '{target}' but item type is '{generic or 'unknown'}'"
        else:
            conf, reason = 0.40, f"weak text match for '{target}'"

        if item.brand:
            if brand == item.brand.lower():
                conf, reason = min(0.99, conf + 0.03), reason + f"; requested brand {p.brand}"
            else:
                conf, reason = min(conf, 0.75), reason + f"; brand {p.brand} differs from requested {item.brand}"
        elif brand in preferred:
            conf, reason = min(0.99, conf + 0.02), reason + f"; preferred brand {p.brand}"

        if locked and conf < AUTO:
            excluded.append(f"{p.name} (would be a substitute; '{item.name}' is never-substitute)")
            continue
        candidates.append(MatchCandidate(listing=l, confidence=round(conf, 2), band=band_for(conf),
                                         is_substitute=conf < AUTO, reason=reason))

    best = max((c.confidence for c in candidates), default=0.0)
    expl = f"{len(candidates)} candidate(s) for '{item.name}'"
    if excluded:
        expl += f"; excluded {len(excluded)}: " + "; ".join(excluded[:4])
    return MatchedProduct(request_item=item, candidates=candidates, best_confidence=best,
                          band=band_for(best), explanation=expl)


def fallback_find_substitutes(item: ItemRequest, constraints: MatchConstraints) -> list[SubstituteSuggestion]:
    locked, _ = never_substitute_rule(item.name, constraints.never_substitute, constraints.known_brands)
    if locked:
        return []
    return [SubstituteSuggestion(query=q, confidence=c, reason=r) for q, c, r in _SUBSTITUTES.get(canonical(item.name), [])]


# --------------------------------------------------------------------------- #
# Tool wrapper used by the planner
# --------------------------------------------------------------------------- #
def _load(module_path: str) -> tuple[Optional[Callable], Optional[Callable], str]:
    try:
        mod = importlib.import_module(module_path)
        return getattr(mod, "match_products"), getattr(mod, "find_substitutes"), module_path
    except Exception:
        return None, None, "fallback"


class MatchingTools:
    def __init__(self, recorder: TraceRecorder, module_path: str = "app.agents.matching_agent"):
        self.rec = recorder
        m, s, src = _load(module_path)
        self.source = src
        self._match = self._guard(m, fallback_match_products, MatchedProduct, "match_products") if m \
            else fallback_match_products
        self._subs = self._guard(s, fallback_find_substitutes, None, "find_substitutes") if s \
            else fallback_find_substitutes

    def _guard(self, teammate: Callable, fallback: Callable, model, name: str) -> Callable:
        """Call the teammate implementation; if it raises or breaks the contract, use the fallback and SAY so."""
        def run(*args):
            try:
                out = teammate(*args)
                if model is not None and not isinstance(out, model):
                    out = model.model_validate(out)
                return out
            except Exception as e:
                self.rec.note(f"{name}.fallback", f"{self.source}.{name} failed ({type(e).__name__}: {e}) – "
                                                  f"using the built-in fallback", status=ActionStatus.FAILED)
                return fallback(*args)
        return run

    def match_products(self, item: ItemRequest, listings: list[Listing], constraints: MatchConstraints,
                       reason: str = "") -> MatchedProduct:
        try:
            result, _ = self.rec.call(
                "match_products",
                {"item": item.model_dump(), "listing_count": len(listings),
                 "constraints": constraints.model_dump(exclude={"known_brands"}), "implementation": self.source},
                lambda: self._match(item, listings, constraints),
                reason or f"Group equivalent products for '{item.name}' across platforms",
                max_retries=0,
                postprocess=lambda r, a: r if isinstance(r, MatchedProduct) else MatchedProduct.model_validate(r))
            return result
        except ToolCallError:
            return MatchedProduct(request_item=item, explanation="matching tool failed")

    def find_substitutes(self, item: ItemRequest, constraints: MatchConstraints,
                         reason: str = "") -> list[SubstituteSuggestion]:
        try:
            result, _ = self.rec.call(
                "find_substitutes", {"item": item.model_dump(), "implementation": self.source},
                lambda: self._subs(item, constraints),
                reason or f"Find substitutes for '{item.name}'", max_retries=0,
                postprocess=lambda r, a: [x if isinstance(x, SubstituteSuggestion)
                                          else SubstituteSuggestion.model_validate(x) for x in (r or [])])
            return result
        except ToolCallError:
            return []
