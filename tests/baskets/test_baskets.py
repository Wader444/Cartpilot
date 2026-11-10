"""CartPilot -- 20 test baskets runner (Member B, implementation.md Phase 19-22).

Runs the fixed baskets in test_baskets.csv through the
PlatformAdapter and match_products, and reports, per basket:

  * best single-platform landed cost vs best split landed cost
    (split = cheapest in-stock listing per item, fees + offers applied
    per platform actually used; the split is chosen only when its
    landed total beats the best single platform -- the net-split rule)
  * savings in Rs and % vs the best single platform
  * matching accuracy (groups formed vs distinct products, min
    confidence, auto-accept share) via match_products, fallback mode
  * guardrails: budget, veg diet, delivery deadline, out-of-stock
    re-planning (an OOS listing must never be used; the plan must
    re-plan around it, not fail)
  * hand-label check for the 5 baskets with expected_split filled in

Usage (from the repo root):  python tests/baskets/test_baskets.py
Writes test_baskets_results.json next to this file and prints
a summary. No Neo4j connection is needed (matching runs in fallback
mode); the numbers come from the mock starter catalog only.

Exit code 0 (demo check PASS) when: all 20 baskets ran, every
hand-labelled split matches the computed plan, every guardrail behaves
as designed (over-budget baskets must be FLAGGED, not silently
proposed; deadline and diet constraints must hold; OOS items must be
re-planned around), and matching forms one group per product with
min confidence >= 0.85.
"""

import csv
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
BASKETS_CSV = os.path.join(HERE, "test_baskets.csv")
RESULTS_JSON = os.path.join(HERE, "test_baskets_results.json")
PLATFORMS = ["blinkit", "instamart", "zepto"]
KEYS = ("ATTA", "RICE", "DAL", "MILK", "OIL",
        "TOMATO", "POTATO", "CURD", "EGGS", "SUGAR")


def _find_file(name):
    here = HERE
    candidates = [os.path.join(here, name), os.path.join(os.getcwd(), name)]
    d = here
    for _ in range(5):
        candidates.append(os.path.join(d, name))
        d = os.path.dirname(d)
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(name + " not found next to this file or above it")


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_items(spec):
    items = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        key, qty = part.split(":")
        items.append((key.strip(), int(qty)))
    return items


def _parse_opt(value, cast):
    value = (value or "").strip()
    return cast(value) if value else None


def load_baskets():
    baskets = []
    with open(BASKETS_CSV, newline="") as f:
        # Skip the '#' comment block at the top of the file.
        lines = [ln for ln in f
                 if ln.strip() and not ln.lstrip().startswith("#")]
        for row in csv.DictReader(lines):
            if not row["basket_id"].strip() or row["basket_id"].startswith("#"):
                continue
            baskets.append({
                "basket_id": row["basket_id"].strip(),
                "name": row["name"].strip(),
                "items": _parse_items(row["items"]),
                "budget": _parse_opt(row["budget_inr"], int),
                "diet": (row["diet"] or "").strip() or None,
                "deadline": _parse_opt(row["deadline_mins"], int),
                "test_type": row["test_type"].strip(),
                "expected_split": (row["expected_split"] or "").strip() or None,
                "notes": row["notes"].strip(),
            })
    return baskets


def main():
    repo_root = os.path.dirname(os.path.dirname(HERE))
    adapter_mod = _load_module("cartpilot_adapter", os.path.join(
        repo_root, "app", "adapters", "platform_adapter.py"))
    match_mod = _load_module("cartpilot_matching", os.path.join(
        repo_root, "app", "agents", "matching.py"))
    store = adapter_mod.MockStore()
    adapters = {p: store.adapter(p) for p in PLATFORMS}

    # All listings, indexed by canonical product key (ATTA, RICE, ...).
    # Exact match on the listing-id suffix (BLK-ATTA-5KG -> ATTA):
    # substring matching broke once substitute products arrived -
    # GROUNDNUT-OIL-1L contains "OIL", and MASOOR-1KG / SONA-1KG match
    # no key at all. Substitute listings are skipped here: baskets
    # only ever contain the 10 canonical products.
    _SUFFIX_TO_KEY = {"ATTA-5KG": "ATTA", "RICE-1KG": "RICE",
                      "DAL-1KG": "DAL", "MILK-1L": "MILK",
                      "OIL-1L": "OIL", "TOMATO-1KG": "TOMATO",
                      "POTATO-1KG": "POTATO", "CURD-400G": "CURD",
                      "EGGS-12": "EGGS", "SUGAR-1KG": "SUGAR"}
    by_key = {}
    for listing in store.search_all(""):
        suffix = listing["product_id"].split("-", 1)[1]
        key = _SUFFIX_TO_KEY.get(suffix)
        if key is None:
            continue
        by_key.setdefault(key, []).append(listing)

    def listing_for(platform, key):
        hits = [l for l in by_key.get(key, [])
                if l["platform"] == platform]
        return hits[0] if hits else None

    def cart_on(platform, items):
        """Basket items resolved to one platform's listing ids."""
        cart = []
        for key, qty in items:
            listing = listing_for(platform, key)
            if listing is None:
                return None
            cart.append({"product_id": listing["product_id"], "qty": qty})
        return cart

    def best_single(items):
        """Cheapest single-platform landed cost; a platform where an
        item is out of stock raises in landed_cost, so it is skipped
        (re-plan, not fail)."""
        results = {}
        for platform in PLATFORMS:
            cart = cart_on(platform, items)
            if cart is None:
                continue
            try:
                results[platform] = adapters[platform].landed_cost(cart)
            except ValueError:
                pass
        if not results:
            return None, {}
        best = min(results.values(), key=lambda r: r["landed_total"])
        return best, results

    def best_split(items):
        """Cheapest in-stock listing per item; fees + offers applied
        per platform actually used. Returns (total, costs, carts)."""
        assignment = {}
        for key, qty in items:
            in_stock = [l for l in by_key.get(key, []) if l["in_stock"]]
            cheapest = min(in_stock, key=lambda l: (l["price"], l["platform"]))
            assignment.setdefault(cheapest["platform"], []).append(
                {"product_id": cheapest["product_id"], "qty": qty})
        costs = {p: adapters[p].landed_cost(c)
                 for p, c in assignment.items()}
        total = sum(c["landed_total"] for c in costs.values())
        return total, costs, assignment

    def plan_label(kind, platforms_used):
        if kind == "single":
            return "single:" + platforms_used[0]
        return "split:" + "+".join(sorted(platforms_used))

    baskets = load_baskets()
    results = []
    n_split_wins = 0
    total_savings = 0
    label_ok = label_checked = 0
    guardrail_problems = []
    matching_problems = []

    for b in baskets:
        # --- matching accuracy (fallback mode, no Neo4j) ---
        listings = [l for key, _ in b["items"] for l in by_key.get(key, [])]
        groups = match_mod.match_products(listings)
        expected_groups = len({key for key, _ in b["items"]})
        min_conf = min((g["confidence"] for g in groups), default=0.0)
        auto = sum(1 for g in groups if g["decision"] == "auto-accept")
        matching_ok = (len(groups) == expected_groups and min_conf >= 0.85)
        if not matching_ok:
            matching_problems.append(
                "%s: %d groups (want %d), min conf %.3f"
                % (b["basket_id"], len(groups), expected_groups, min_conf))

        # --- costs: best single vs best split (net-split rule) ---
        single_best, _singles = best_single(b["items"])
        split_total, split_costs, split_carts = best_split(b["items"])
        if single_best is None:
            results.append({"basket_id": b["basket_id"], "error":
                            "no single platform can fulfil this basket"})
            guardrail_problems.append("%s: no platform can fulfil it"
                                      % b["basket_id"])
            continue
        if split_total < single_best["landed_total"]:
            kind, chosen_total = "split", split_total
            used = sorted(split_costs)
            chosen_carts = split_carts
            n_split_wins += 1
        else:
            kind, chosen_total = "single", single_best["landed_total"]
            used = [single_best["platform"]]
            chosen_carts = {used[0]: cart_on(used[0], b["items"])}
        savings = single_best["landed_total"] - chosen_total
        total_savings += savings

        # --- guardrails ---
        guards = {"platforms_used": used}
        if b["budget"] is not None:
            guards["budget_ok"] = chosen_total <= b["budget"]
            if not guards["budget_ok"]:
                # Correct behaviour is to FLAG, not to propose silently.
                guards["budget_outcome"] = "flagged over budget"
        if b["diet"] == "veg":
            non_veg = [l for key, _ in b["items"] for l in by_key.get(key, [])
                       if l["diet_tag"] == "non-veg"]
            guards["diet_ok"] = not non_veg
            if not guards["diet_ok"]:
                guardrail_problems.append(
                    "%s: veg basket contains non-veg item" % b["basket_id"])
        if b["deadline"] is not None:
            etas = {p: adapters[p].get_eta(c)["eta_mins"]
                    for p, c in chosen_carts.items()}
            guards["deadline_ok"] = all(e <= b["deadline"]
                                        for e in etas.values())
            guards["etas"] = etas
            if not guards["deadline_ok"]:
                guardrail_problems.append(
                    "%s: ETA %s exceeds %d-min deadline"
                    % (b["basket_id"], etas, b["deadline"]))
        # Out-of-stock: the chosen plan must never use an OOS listing.
        oos_used = []
        for platform, cart in chosen_carts.items():
            for line in cart:
                listing = adapters[platform].get_listing(line["product_id"])
                if not listing["in_stock"]:
                    oos_used.append((line["product_id"], platform))
        guards["oos_replanned"] = not oos_used
        if oos_used:
            guardrail_problems.append(
                "%s: plan uses out-of-stock %s" % (b["basket_id"], oos_used))

        # --- hand-label check ---
        label = plan_label(kind, used)
        label_match = None
        if b["expected_split"]:
            label_checked += 1
            label_match = (label == b["expected_split"])
            if label_match:
                label_ok += 1

        results.append({
            "basket_id": b["basket_id"],
            "name": b["name"],
            "test_type": b["test_type"],
            "items": len(b["items"]),
            "best_single_platform": single_best["platform"],
            "best_single_total": single_best["landed_total"],
            "split_total": split_total,
            "split_platforms": sorted(split_costs),
            "chosen": kind,
            "chosen_total": chosen_total,
            "savings_rs": savings,
            "savings_pct": round(100 * savings / single_best["landed_total"], 1),
            "matching_groups": len(groups),
            "matching_expected": expected_groups,
            "matching_min_conf": round(min_conf, 3),
            "matching_auto_accept": "%d/%d" % (auto, len(groups)),
            "matching_ok": matching_ok,
            "guardrails": guards,
            "expected_split": b["expected_split"],
            "computed_split": label,
            "label_match": label_match,
        })

    with open(RESULTS_JSON, "w") as f:
        json.dump({"baskets": results,
                   "summary": {
                       "n_baskets": len(results),
                       "split_wins": n_split_wins,
                       "total_savings_rs": total_savings,
                       "labels_checked": label_checked,
                       "labels_ok": label_ok,
                       "matching_problems": matching_problems,
                       "guardrail_problems": guardrail_problems,
                   }}, f, indent=2)

    print("Test baskets: %d ran" % len(results))
    print("Split wins: %d / %d | total savings vs best single: Rs %d"
          % (n_split_wins, len(results), total_savings))
    print("Hand-labels: %d / %d match" % (label_ok, label_checked))
    print("Matching problems: %s" % (matching_problems or "none"))
    print("Guardrail problems: %s" % (guardrail_problems or "none"))
    print()
    print("%-5s %-22s %8s %8s %-9s %7s" %
          ("ID", "chosen", "single", "split", "save", "match"))
    for r in results:
        if "error" in r:
            print("%-5s ERROR: %s" % (r["basket_id"], r["error"]))
            continue
        flag = ""
        if r["test_type"] == "guardrail-budget" and not r["guardrails"].get("budget_ok", True):
            flag = " <- over budget: flagged"
        print("%-5s %-22s %8d %8d %-9s %7s%s" % (
            r["basket_id"],
            r["chosen"] + ":" + ",".join(r["guardrails"]["platforms_used"]),
            r["best_single_total"], r["split_total"],
            "Rs %d" % r["savings_rs"],
            "%d/%d" % (r["matching_groups"], r["matching_expected"]),
            flag))
    ok = (len(results) == 20 and not matching_problems
          and not guardrail_problems and label_ok == label_checked)
    print()
    print("Demo check: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
