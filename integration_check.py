"""CartPilot integration check -- run from the repo root:

    python integration_check.py

Runs the whole Member B chain end to end and prints PASS/FAIL per
stage:

  1. Catalog loads: 39 listings, 13 products, 3 platforms.
  2. Adapter demo basket: Blinkit 821 / Zepto 786 / Instamart 840.
  3. Matching on all 39 listings: 13 groups x 3, all auto-accept.
  4. Substitutes are buyable: P11/P12/P13 have in-stock listings,
     and both .cypher files pass static checks.
  5. Price history: exactly one flagged fake discount (ZEP-TOMATO-1KG).
  6. Test baskets: all 20 run, demo check PASS.

No Neo4j connection is needed: matching runs in fallback mode and
the .cypher files are checked statically. For the live graph check,
run data/graph_seed/seed.py with Neo4j running (see README.md).
Exit 0 when every stage passes.
"""

import importlib.util
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
EXPECTED_LANDED = {"blinkit": 821, "zepto": 786, "instamart": 840}
SUBSTITUTES = ("MASOOR-1KG", "GROUNDNUT-OIL-1L", "SONA-1KG")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def statements_of(path):
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip().startswith("//"):
                continue
            lines.append(line)
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main():
    adapter_mod = load_module(
        "platform_adapter",
        os.path.join(ROOT, "app", "adapters", "platform_adapter.py"))
    match_mod = load_module(
        "matching", os.path.join(ROOT, "app", "agents", "matching.py"))
    price_mod = load_module(
        "price_history", os.path.join(ROOT, "app", "agents", "price_history.py"))

    stages = []

    def stage(name, ok, detail):
        stages.append((name, ok, detail))
        print("[%s] %s -- %s" % ("PASS" if ok else "FAIL", name, detail))

    # 1. Catalog.
    store = adapter_mod.MockStore()
    listings = store.search_all("")
    products = {l["product_id"].split("-", 1)[1] for l in listings}
    stage("catalog loads",
          len(listings) == 39 and len(products) == 13,
          "%d listings, %d products, %d platforms"
          % (len(listings), len(products), len(store.platforms())))

    # 2. Adapter demo basket.
    totals = {}
    for platform in ("blinkit", "zepto", "instamart"):
        cart = store.demo_basket(platform)
        totals[platform] = store.adapter(platform).landed_cost(cart)["landed_total"]
    stage("adapter demo basket", totals == EXPECTED_LANDED,
          "Blinkit %s / Zepto %s / Instamart %s (expect 821 / 786 / 840)"
          % (totals["blinkit"], totals["zepto"], totals["instamart"]))

    # 3. Matching on all listings (fallback mode, no Neo4j).
    groups = match_mod.match_products(listings)
    min_conf = min((g["confidence"] for g in groups), default=0.0)
    stage("matching (fallback)",
          len(groups) == 13
          and all(len(g["listings"]) == 3 for g in groups)
          and all(g["decision"] == "auto-accept" for g in groups),
          "%d groups x 3 listings, min conf %.3f, all auto-accept"
          % (len(groups), min_conf))

    # 4. Substitutes buyable + .cypher files statically sound.
    buyable = {}
    for suffix in SUBSTITUTES:
        hits = [l for l in listings
                if l["product_id"].split("-", 1)[1] == suffix]
        buyable[suffix] = len(hits) == 3 and all(h["in_stock"] for h in hits)
    seed_stmts = statements_of(
        os.path.join(ROOT, "data", "graph_seed", "seed.cypher"))
    sub_stmts = statements_of(
        os.path.join(ROOT, "data", "graph_seed", "substitute_listings.cypher"))
    seed_ok = (len(seed_stmts) == 82
               and not any(s.upper().startswith("WITH") for s in seed_stmts)
               and sum("MERGE (l:Listing" in s for s in seed_stmts) == 30
               and sum("SAME_AS" in s for s in seed_stmts) == 30)
    sub_ok = (sum("SET p.listed = true" in s for s in sub_stmts) == 3
              and sum("MERGE (l:Listing" in s for s in sub_stmts) == 9
              and sum("SAME_AS" in s for s in sub_stmts) == 9)
    stage("substitutes buyable",
          all(buyable.values()) and seed_ok and sub_ok,
          "P11/P12/P13 in stock on all 3 platforms; "
          "seed %d stmts, substitute file 3 flips + 9 listings + 9 SAME_AS"
          % len(seed_stmts))

    # 5. Price history.
    _results, flagged = price_mod.PriceHistory().check_all()
    flagged_ids = [r["product_id"] for r in flagged]
    stage("price history", flagged_ids == ["ZEP-TOMATO-1KG"],
          "flagged=%s (expect ['ZEP-TOMATO-1KG'])" % flagged_ids)

    # 6. Test baskets.
    proc = subprocess.run(
        [sys.executable, "tests/baskets/test_baskets.py"],
        cwd=ROOT, capture_output=True, text=True)
    baskets_ok = proc.returncode == 0 and "Demo check: PASS" in proc.stdout
    headline = ""
    results_path = os.path.join(
        ROOT, "tests", "baskets", "test_baskets_results.json")
    if os.path.exists(results_path):
        summary = json.load(open(results_path))["summary"]
        headline = ("split wins %d/20, total savings Rs %d, "
                    "hand-labels %d/%d"
                    % (summary["split_wins"], summary["total_savings_rs"],
                       summary["labels_ok"], summary["labels_checked"]))
    stage("test baskets (20)", baskets_ok, headline or proc.stdout.strip().splitlines()[-1])

    ok = all(s[1] for s in stages)
    print()
    print("Integration check: %s (%d/%d stages)"
          % ("PASS" if ok else "FAIL",
             sum(s[1] for s in stages), len(stages)))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
