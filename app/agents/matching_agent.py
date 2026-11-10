"""Bridge: Member A's matching interface -> Member B's matcher (app/agents/matching.py).

The planner calls (via tools/matching_tools.py):
    match_products(item, listings, constraints) -> MatchedProduct
    find_substitutes(item, constraints)         -> list[SubstituteSuggestion]

How the two lanes combine:
  1. Member A's rules decide which listings are relevant to the requested item
     (diet, rejected brands, never-substitute, brand asked for, confidence bands).
  2. Member B's matcher (Neo4j graph first, similarity second) then checks that
     the relevant listings are the SAME product across apps. If B's attribute
     check finds a mismatch inside a group, that confidence is capped, so the
     item drops to "confirm" and is never auto-accepted.
  3. Any error in B's code -> A's rules are used alone and the trace says so
     (MatchingTools._guard).

The graph path is active only when NEO4J_PASSWORD is set and Neo4j is reachable;
otherwise B's similarity path runs. Both are deterministic.
"""
from __future__ import annotations

import os
from typing import Optional

from app.agents import matching as member_b
from app.core.models import ItemRequest, Listing, MatchedProduct
from app.tools.matching_tools import (
    MatchConstraints,
    SubstituteSuggestion,
    band_for,
    fallback_find_substitutes,
    fallback_match_products,
)

_UNITS = {"litre": "L", "ml": "ml", "kg": "kg", "g": "g", "pcs": "pcs"}
_graph = None
_graph_tried = False


def _get_graph():
    """Connect to Neo4j once, only if a password is configured; never block the demo."""
    global _graph, _graph_tried
    if not _graph_tried:
        _graph_tried = True
        if os.environ.get("NEO4J_PASSWORD"):
            try:
                _graph = member_b._connect_graph()
            except Exception:  # noqa: BLE001
                _graph = None
    return _graph


def _to_b(l: Listing) -> dict:
    p = l.product
    return {"product_id": l.product_id, "platform": l.platform.value, "name": p.name, "brand": p.brand,
            "quantity_value": p.quantity_value, "quantity_unit": _UNITS.get(p.quantity_unit, p.quantity_unit),
            "category": p.category, "diet_tag": p.diet_tag.value, "price": l.price, "in_stock": l.in_stock}


def match_products(item: ItemRequest, listings: list[Listing], constraints: MatchConstraints) -> MatchedProduct:
    m = fallback_match_products(item, listings, constraints)          # 1. relevance + rules (Member A)
    relevant = [c for c in m.candidates if c.confidence >= 0.85]
    if not relevant:
        return m
    graph = _get_graph()
    groups = member_b.match_products([_to_b(c.listing) for c in relevant], graph=graph)   # 2. Member B
    by_key = {}
    for g in groups:
        for l in g["listings"]:
            by_key[(l["platform"], l["product_id"])] = g
    multi = 0
    for c in relevant:
        g = by_key.get((c.listing.platform.value, c.listing.product_id))
        if g is None:
            continue
        n = len(g["listings"])
        if n >= 2:
            multi += 1
            if g["confidence"] < c.confidence:          # B found an attribute mismatch -> never auto-accept
                c.confidence = round(g["confidence"], 2)
                c.band = band_for(c.confidence)
                c.is_substitute = c.confidence < 0.85
            c.reason += f"; Member B: same product on {n} apps ({g['decision']}, {g['confidence']:.2f})"
        else:
            c.reason += "; Member B: sold on one app only"
    m.best_confidence = max((c.confidence for c in m.candidates), default=0.0)
    m.band = band_for(m.best_confidence)
    m.explanation += (f" | Member B matcher ({'Neo4j graph' if graph else 'similarity'}): "
                      f"{len(groups)} product group(s), {multi} cross-app listing(s) confirmed")
    return m


def find_substitutes(item: ItemRequest, constraints: MatchConstraints) -> list[SubstituteSuggestion]:
    # Member B's graph substitutes are keyed to his catalog SKUs; with the in-process
    # store the rule-based SUBSTITUTE_FOR table is used. Same contract either way.
    return fallback_find_substitutes(item, constraints)
