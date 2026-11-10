"""CartPilot - match_products v1 (Member B: Data & Product Graph).

Tool contract (implementation.md SS5):
    match_products(listings) -> groups with confidence score 0-1

    listings: one dict per platform listing, with keys
        product_id, platform, name, brand, quantity_value, quantity_unit,
        category, diet_tag, price, in_stock
    returns: a list of groups, each a dict
        group_id, sku (canonical Product sku, or None), confidence,
        decision, reason, listings

Confidence bands (frozen contract - do not change without telling A and C):
    >= 0.85  auto-accept   - the agent may use this group as one product
    0.60-0.84 show-both    - show both options, let the user pick
    < 0.60   ask-user      - never guess; ask the user

Order of evidence (plan SS2):
    1. Neo4j graph first  - SAME_AS / HAS_LISTING gives the ground truth.
    2. Similarity second  - name tokens + brand + normalised quantity,
                             for listings not in the graph yet.
    3. Attribute rules as a check - diet, brand and quantity must agree;
                             a mismatch caps the confidence, it never
                             gets averaged away.

Copy this file to app/agents/matching.py in the repo. It needs only
`pip install neo4j` for the graph path; the fallback path has no
dependencies, so match_products still works if Neo4j is down.

Run the demo (Neo4j running with the seed loaded, or not - the demo
falls back and says so):
    pip install neo4j
    python cartpilot-match-products.py
Expect: 13 groups, 3 listings each, confidence 0.95 (graph) on the
merged catalog (10 starter products + 3 substitutes).
"""

import csv
import difflib
import os
import re
from pathlib import Path

# Load cartpilot/.env if python-dotenv is installed (pip install python-dotenv).
# Without it, NEO4J_PASSWORD must be set in the terminal, or the demo
# runs fallback-only. See .env.example for the exact variable names.
# The walk-up below matters: running from app/agents must still find
# the .env that lives at the project root, two folders up.
_DOTENV_MISSING = False
try:
    from dotenv import load_dotenv

    load_dotenv()
    for _parent in Path(__file__).resolve().parents:
        _env_file = _parent / ".env"
        if _env_file.exists():
            load_dotenv(_env_file, override=False)
            break
except ImportError:
    _DOTENV_MISSING = True

# ---------------------------------------------------------------------------
# Pure helpers - no Neo4j needed, easy to unit-test
# ---------------------------------------------------------------------------

_UNIT_ALIASES = {"ml": "ml", "millilitre": "ml", "milliliter": "ml",
                 "l": "L", "litre": "L", "liter": "L",
                 "g": "g", "gram": "g", "grams": "g",
                 "kg": "kg", "kilogram": "kg",
                 "piece": "piece", "pcs": "piece", "pc": "piece"}


def normalize_quantity(value, unit):
    """Return (base_value, base_unit) so 1000ml == 1L and 1000g == 1kg."""
    unit = _UNIT_ALIASES.get(str(unit).strip().lower(), str(unit))
    value = float(value)
    if unit == "ml":
        return value / 1000.0, "L"
    if unit == "g":
        return value / 1000.0, "kg"
    return value, unit


def normalize_name(name):
    """Lowercase, fold 1000ml -> 1l, '1 kg' -> '1kg', strip punctuation."""
    name = str(name).lower()
    name = name.replace("1000ml", "1l").replace("1000 ml", "1l")
    name = name.replace("1000g", "1kg").replace("1000 g", "1kg")
    # "1 kg" / "500 ml" / "12 pcs" -> "1kg" / "500ml" / "12pcs", so the
    # same pack written two ways still tokenises identically.
    name = re.sub(r"(\d+)\s+(kg|g|ml|l|pcs?)\b", r"\1\2", name)
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def name_similarity(a, b):
    """0-1 similarity from token overlap + character similarity.

    Token overlap carries most of the weight: platforms write the
    same product brand-first or brand-last ("India Gate Basmati Rice"
    vs "Basmati Rice - India Gate"), and word order must not count
    against a match. SequenceMatcher is order-sensitive, so it only
    breaks near-ties.
    """
    na, nb = normalize_name(a), normalize_name(b)
    if na == nb:
        return 1.0
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    ratio = difflib.SequenceMatcher(None, na, nb).ratio()
    return round(0.7 * jaccard + 0.3 * ratio, 3)


def attribute_check(a, b):
    """Do two listings agree on diet, brand, quantity, category?

    Used inside graph groups: the graph already proved SAME_AS, so
    this check must only verify attributes - it must NOT re-judge
    the name wording. (An earlier version reused pair_confidence
    here, and its order-sensitive name score wrongly flagged rice
    and dal, whose Instamart names are brand-last.)
    Returns (ok, [reasons]).
    """
    reasons = []
    if a.get("diet_tag") and b.get("diet_tag") and a["diet_tag"] != b["diet_tag"]:
        return False, ["diet_tag differs (%s vs %s)" % (a["diet_tag"], b["diet_tag"])]
    ba, bb = (a.get("brand") or "").strip().lower(), (b.get("brand") or "").strip().lower()
    if ba and bb and ba != bb:
        return False, ["brand differs (%s vs %s)" % (ba, bb)]
    qa, ua = normalize_quantity(a["quantity_value"], a["quantity_unit"])
    qb, ub = normalize_quantity(b["quantity_value"], b["quantity_unit"])
    if ua != ub or abs(qa - qb) > 1e-9:
        return False, ["quantity differs (%s %s vs %s %s)" % (qa, ua, qb, ub)]
    if a.get("category") and b.get("category") and a["category"] != b["category"]:
        return False, ["category differs"]
    if ba and bb:
        reasons.append("same brand")
    reasons.append("same quantity")
    return True, reasons


def pair_confidence(a, b):
    """Confidence that two listings are the same product, + reasons.

    Returns (confidence, [reasons]). Attribute mismatches cap the
    score instead of being averaged away.
    """
    reasons = []
    # Diet must agree - a veg basket must never get a non-veg match.
    if a.get("diet_tag") and b.get("diet_tag") and a["diet_tag"] != b["diet_tag"]:
        return 0.0, ["diet_tag differs (%s vs %s)" % (a["diet_tag"], b["diet_tag"])]
    conf = name_similarity(a.get("name", ""), b.get("name", ""))
    # Brand: same brand supports the match; different brands cap it hard.
    ba, bb = (a.get("brand") or "").strip().lower(), (b.get("brand") or "").strip().lower()
    if ba and bb:
        if ba == bb:
            conf = min(1.0, conf + 0.05)
            reasons.append("same brand")
        else:
            conf = min(conf, 0.55)
            reasons.append("brand differs (%s vs %s)" % (ba, bb))
    # Quantity: compare in base units, so 1L and 1000ml are equal.
    qa, ua = normalize_quantity(a["quantity_value"], a["quantity_unit"])
    qb, ub = normalize_quantity(b["quantity_value"], b["quantity_unit"])
    if ua != ub or abs(qa - qb) > 1e-9:
        conf = min(conf, 0.50)
        reasons.append("quantity differs (%s %s vs %s %s)" % (qa, ua, qb, ub))
    else:
        reasons.append("same quantity")
    # Category: different categories cap the score.
    if a.get("category") and b.get("category") and a["category"] != b["category"]:
        conf = min(conf, 0.60)
        reasons.append("category differs")
    return round(conf, 3), reasons


def decision_for(confidence):
    if confidence >= 0.85:
        return "auto-accept"
    if confidence >= 0.60:
        return "show-both"
    return "ask-user"


# ---------------------------------------------------------------------------
# Neo4j graph lookups
# ---------------------------------------------------------------------------

class GraphMatcher:
    """Thin wrapper over the seeded graph. One method per query, so
    Member A can call these from the planner without knowing Cypher."""

    def __init__(self, uri, user, password, database="cartpilot"):
        from neo4j import GraphDatabase
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._database = database

    def close(self):
        self._driver.close()

    def _run(self, query, **params):
        with self._driver.session(database=self._database) as session:
            return list(session.run(query, **params))

    def get_sku(self, product_id):
        """Canonical Product sku for a listing, or None if not in graph."""
        rows = self._run(
            "MATCH (l:Listing {product_id: $pid})<-[:HAS_LISTING]-(p:Product) "
            "RETURN p.sku AS sku",
            pid=product_id,
        )
        return rows[0]["sku"] if rows else None

    def get_substitute(self, sku, diet_tag, never_substitute=()):
        """Best SUBSTITUTE_FOR for a Product sku, respecting diet.

        Returns a dict, or None. `buyable` is False when the substitute
        is graph-only (listed: false) - it is known, but there is no
        listing to buy yet, so the agent must not propose it as a cart
        item until the 100-SKU scale-up adds listings for it.
        """
        rows = self._run(
            "MATCH (p:Product {sku: $sku})-[s:SUBSTITUTE_FOR]->(sub:Product) "
            "WHERE sub.diet_tag = $diet "
            "RETURN sub.sku AS sku, sub.name AS name, s.confidence AS confidence, "
            "s.reason AS reason, sub.listed AS listed "
            "ORDER BY s.confidence DESC",
            sku=sku, diet=diet_tag,
        )
        for row in rows:
            if row["sku"] in (never_substitute or ()):
                continue
            return {
                "sku": row["sku"],
                "name": row["name"],
                "confidence": row["confidence"],
                "reason": row["reason"],
                "buyable": bool(row["listed"]),
                "decision": decision_for(row["confidence"]),
            }
        return None


def get_substitute_for_listing(graph, product_id, diet_tag, never_substitute=()):
    """Substitute for an out-of-stock listing. Returns None when the
    graph knows no substitute, or none that respects diet/rules."""
    if graph is None:
        return None
    sku = graph.get_sku(product_id)
    if sku is None:
        return None
    return graph.get_substitute(sku, diet_tag, never_substitute)


# ---------------------------------------------------------------------------
# match_products - the tool
# ---------------------------------------------------------------------------

def match_products(listings, graph=None):
    """Group listings from all platforms into same-product groups.

    Graph groups come first (confidence 0.95 when the attribute check
    passes). Anything not in the graph is grouped by similarity, and
    anything that matches nothing gets confidence 0.0 / ask-user -
    never a guessed group.
    """
    groups = []          # each: {key, sku, listings, confidence, reasons}
    by_sku = {}

    # 1. Graph first.
    ungrouped = []
    for listing in listings:
        sku = graph.get_sku(listing["product_id"]) if graph is not None else None
        if sku is None:
            ungrouped.append(listing)
            continue
        if sku not in by_sku:
            by_sku[sku] = {"key": sku, "sku": sku, "listings": [],
                           "confidence": 0.95, "reasons": ["graph SAME_AS / HAS_LISTING"]}
            groups.append(by_sku[sku])
        by_sku[sku]["listings"].append(listing)

    # Attribute check inside graph groups: a diet/brand/quantity/
    # category mismatch inside one sku group means the seed data is
    # wrong - flag it. Name wording is NOT checked here: the graph
    # already proved SAME_AS, and brand-last phrasing is expected.
    for group in groups:
        first = group["listings"][0]
        for other in group["listings"][1:]:
            ok, reasons = attribute_check(first, other)
            if not ok:
                group["confidence"] = min(group["confidence"], 0.70)
                group["reasons"].append("attribute check failed: " + "; ".join(reasons))

    # 2. Similarity second, for listings not in the graph.
    fallback_groups = []
    for listing in ungrouped:
        best, best_conf, best_reasons = None, 0.0, []
        for group in fallback_groups:
            conf, reasons = pair_confidence(group["listings"][0], listing)
            if conf > best_conf:
                best, best_conf, best_reasons = group, conf, reasons
        # Groups form only from auto-accept-strength pairs. On the
        # labelled set, correct pairs score >= 0.894 while near-miss
        # substitutes (masoor vs toor dal, groundnut vs sunflower oil,
        # sona masoori vs basmati rice) score <= 0.794, so 0.85 sits
        # in the gap. Weaker pairs stay separate and get flagged,
        # never merged into a wrong group.
        if best is not None and best_conf >= 0.85:
            best["listings"].append(listing)
            best["confidence"] = round(min(best["confidence"], best_conf), 3)
            best["reasons"].extend(best_reasons)
        else:
            fallback_groups.append({"key": "fallback-%s" % listing["product_id"],
                                    "sku": None, "listings": [listing],
                                    "confidence": 1.0, "reasons": ["no match yet"]})
    groups.extend(fallback_groups)

    # 3. Finalise: singletons matched nothing, so confidence 0.0.
    out = []
    for group in groups:
        if len(group["listings"]) == 1:
            confidence, decision = 0.0, "ask-user"
            reasons = group["reasons"] + ["no cross-platform match found"]
        else:
            confidence = round(group["confidence"], 3)
            decision = decision_for(confidence)
            reasons = group["reasons"]
        out.append({
            "group_id": group["key"],
            "sku": group["sku"],
            "confidence": confidence,
            "decision": decision,
            "reason": "; ".join(sorted(set(reasons))),
            "listings": group["listings"],
        })
    return out


# ---------------------------------------------------------------------------
# Demo - run this file directly
# ---------------------------------------------------------------------------

def _load_demo_listings():
    here = Path(__file__).parent
    for candidate in (here / "cartpilot-starter-catalog.csv",
                      here / "data" / "mock_catalog" / "catalog.csv",
                      here.parent.parent / "data" / "mock_catalog" / "catalog.csv"):
        if candidate.exists():
            with open(candidate, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            return [{
                "product_id": r["product_id"], "platform": r["platform"],
                "name": r["name"], "brand": r["brand"],
                "quantity_value": float(r["quantity_value"]),
                "quantity_unit": r["quantity_unit"], "category": r["category"],
                "diet_tag": r["diet_tag"], "price": int(r["price"]),
                "in_stock": r["in_stock"] == "True",
            } for r in rows]
    raise SystemExit("Demo CSV not found - expected at "
                     "data/mock_catalog/catalog.csv in the repo.")


def _connect_graph():
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        print("No NEO4J_PASSWORD set - running fallback-only (no graph).")
        if _DOTENV_MISSING:
            print("Hint: python-dotenv is not installed, so a .env file is ignored. Run: pip install python-dotenv")
        else:
            print("Hint: no .env was found. Check it is named exactly .env, in the project root, with a line NEO4J_PASSWORD=...")
        return None
    try:
        graph = GraphMatcher(uri, user, password)
        graph.get_sku("BLK-ATTA-5KG")  # prove the connection works
        print("Connected to Neo4j at %s - graph path active." % uri)
        return graph
    except Exception as exc:  # noqa: BLE001 - demo must not crash on DB down
        print("Could not reach Neo4j (%s) - running fallback-only." % exc)
        return None


def main():
    listings = _load_demo_listings()
    print("Listings loaded: %d" % len(listings))
    graph = _connect_graph()
    groups = match_products(listings, graph=graph)
    print("Groups: %d" % len(groups))
    for group in sorted(groups, key=lambda g: str(g["group_id"])):
        names = ", ".join("%s:%s" % (l["platform"], l["price"]) for l in group["listings"])
        print("  %s sku=%s conf=%s decision=%s [%s]  // %s"
              % (group["group_id"], group["sku"], group["confidence"],
                 group["decision"], names, group["reason"]))
    if graph is not None:
        sub = get_substitute_for_listing(graph, "BLK-DAL-1KG", "veg")
        print("Substitute demo (toor dal P03):", sub)
        graph.close()
    # Graph groups are 0.95; fallback-only groups are weaker by design
    # (similarity, not ground truth), so the bar differs by mode.
    min_conf = 0.85 if graph is not None else 0.60
    ok = len(groups) == 13 and all(len(g["listings"]) == 3 for g in groups) \
        and all(g["confidence"] >= min_conf for g in groups)
    print("Demo check (13 groups x 3 listings, conf >= %s): %s"
          % (min_conf, "PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
