"""CartPilot - PlatformAdapter + mock store API (Member B: Data & Product Graph).

One interface, three platforms. Each platform differs ONLY in data
(prices, fees, offers, ETAs) - never in code. That is the whole point:
when a real partner API replaces the mock, Member A and C change
nothing; only the data behind the adapter changes.

Tool contract (implementation.md SS5):
    search_products(platform, query)  -> listings with price, pack, stock
    get_fees(platform, cart)          -> handling, delivery, small-cart, surge
    get_eta(platform, cart, pincode)  -> ETA in minutes, deadline met yes/no
    get_offers(platform, cart)        -> offers that actually apply
    match_products(listings)          -> see matching.py (feeds off this file)

Data: the starter catalog CSV (30 listings, 10 products x 3 platforms).
Copy this file to app/adapters/platform_adapter.py and the CSV to
data/mock_catalog/catalog.csv in the repo. No dependencies - stdlib only.

Rules baked in (from cartpilot-starter-catalog.md - do not change
without telling A and C):
    * Offers do NOT stack - one offer per platform, best one wins.
    * Percent discounts are rounded DOWN to whole rupees.
    * Landed total = item_total + handling + delivery + small-cart
      + surge - best offer. (Member C owns the real landed-cost
      engine and the optimizer; landed_cost() here is only the
      check that proves the three platforms give three totals.)

Cart format: [{"product_id": "BLK-ATTA-5KG", "qty": 1}, ...]
Listings returned are dicts in exactly the shape match_products
expects, so:  match_products(store.search_all("rice"))
"""

import csv
import math
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform data - the ONLY place platform differences live
# ---------------------------------------------------------------------------

PLATFORMS = {
    # fees: handling, delivery, small-cart threshold + fee, surge, base ETA
    "blinkit": {
        "display": "Blinkit",
        "handling": 9, "delivery": 25,
        "small_cart_threshold": 199, "small_cart_fee": 20,
        "surge": 0, "eta_mins": 12,
        "offers": [
            {"code": "FLAT50", "kind": "flat", "value": 50, "min_cart": 799},
            {"code": "BANK10", "kind": "percent", "value": 10, "cap": 100, "min_cart": 500},
        ],
    },
    "zepto": {
        "display": "Zepto",
        "handling": 5, "delivery": 20,
        "small_cart_threshold": 149, "small_cart_fee": 15,
        "surge": 10, "eta_mins": 10,
        "offers": [
            {"code": "ZEPPO20", "kind": "percent", "value": 20, "cap": 120, "min_cart": 599},
            {"code": "FREEDEL", "kind": "free_delivery", "value": 0, "min_cart": 499},
        ],
    },
    "instamart": {
        "display": "Swiggy Instamart",
        "handling": 12, "delivery": 30,
        "small_cart_threshold": 199, "small_cart_fee": 25,
        "surge": 0, "eta_mins": 25,
        "offers": [
            {"code": "MART100", "kind": "flat", "value": 100, "min_cart": 999},
            {"code": "FIRST15", "kind": "percent", "value": 15, "cap": 80, "min_cart": 499},
        ],
    },
}

# The demo basket from the plan: one unit each of atta, rice, dal,
# milk, oil, tomato. Keyed by product keyword -> per-platform listing.
DEMO_BASKET_KEYS = ["ATTA", "RICE", "DAL", "MILK", "OIL", "TOMATO"]

# Hand-checked landed totals for the demo basket (see the catalog guide).
# The demo asserts against these - if they ever change, the catalog,
# the guide AND this file change together.
EXPECTED_LANDED = {"blinkit": 821, "zepto": 786, "instamart": 840}


# ---------------------------------------------------------------------------
# Catalog loading
# ---------------------------------------------------------------------------

def _find_catalog():
    here = Path(__file__).parent
    for candidate in (here / "cartpilot-starter-catalog.csv",
                      here / "data" / "mock_catalog" / "catalog.csv",
                      here.parent / "data" / "mock_catalog" / "catalog.csv",
                      here.parent.parent / "data" / "mock_catalog" / "catalog.csv"):
        if candidate.exists():
            return candidate
    raise SystemExit("Catalog CSV not found - expected at "
                     "data/mock_catalog/catalog.csv in the repo.")


def _load_catalog(path=None):
    """All listings as match_products-shaped dicts, grouped by platform."""
    path = path or _find_catalog()
    by_platform = {name: [] for name in PLATFORMS}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            listing = {
                "product_id": r["product_id"], "platform": r["platform"],
                "name": r["name"], "brand": r["brand"],
                "quantity_value": float(r["quantity_value"]),
                "quantity_unit": r["quantity_unit"], "category": r["category"],
                "diet_tag": r["diet_tag"], "price": int(r["price"]),
                "mrp": int(r["mrp"]), "in_stock": r["in_stock"] == "True",
                "eta_mins": int(r["eta_mins"]),
            }
            by_platform[r["platform"]].append(listing)
    return by_platform


# ---------------------------------------------------------------------------
# The adapter - one class, one platform's data
# ---------------------------------------------------------------------------

class PlatformAdapter:
    """The interface every platform agent talks to (implementation.md SS5).

    Member A calls these four methods; how the data is stored behind
    them is Member B's business. A real API later implements the same
    four methods.
    """

    def __init__(self, platform, listings):
        if platform not in PLATFORMS:
            raise ValueError("unknown platform %r (want one of %s)"
                             % (platform, sorted(PLATFORMS)))
        self.platform = platform
        self.config = PLATFORMS[platform]
        self._listings = list(listings)
        self._by_id = {l["product_id"]: l for l in self._listings}

    # -- listings ------------------------------------------------------
    def search_products(self, query):
        """Listings whose name / brand / category contains the query.

        Returns in-stock and out-of-stock listings alike, with the
        in_stock flag set - the caller decides what to do about stock.
        Sorted cheapest first.
        """
        q = query.strip().lower()
        hits = [dict(l) for l in self._listings
                if q in l["name"].lower()
                or q in (l["brand"] or "").lower()
                or q in l["category"].lower()]
        return sorted(hits, key=lambda l: l["price"])

    def get_listing(self, product_id):
        """One listing by id, or None. Raises if the id is another
        platform's - a cart must never mix platforms inside one adapter."""
        listing = self._by_id.get(product_id)
        if listing is None:
            raise ValueError("%s has no listing %s"
                             % (self.platform, product_id))
        return dict(listing)

    # -- cart helpers --------------------------------------------------
    def _cart_lines(self, cart):
        """Resolve cart items to (listing, qty) pairs, validating stock."""
        lines = []
        for item in cart:
            listing = self.get_listing(item["product_id"])
            qty = int(item.get("qty", 1))
            if qty < 1:
                raise ValueError("qty must be >= 1 for %s" % item["product_id"])
            if not listing["in_stock"]:
                raise ValueError("%s is out of stock on %s - re-plan, "
                                 "do not silently swap it"
                                 % (listing["name"], self.platform))
            lines.append((listing, qty))
        return lines

    def _item_total(self, cart):
        return sum(l["price"] * q for l, q in self._cart_lines(cart))

    # -- the four tools -------------------------------------------------
    def get_fees(self, cart):
        """handling + delivery + small-cart + surge for this cart."""
        item_total = self._item_total(cart)
        cfg = self.config
        small_cart = cfg["small_cart_fee"] if item_total < cfg["small_cart_threshold"] else 0
        fees = {"handling": cfg["handling"], "delivery": cfg["delivery"],
                "small_cart": small_cart, "surge": cfg["surge"]}
        fees["total"] = sum(fees.values())
        fees["item_total"] = item_total
        return fees

    def get_eta(self, cart, pincode=None, deadline_mins=None):
        """ETA in minutes for this cart. pincode is accepted for the
        interface but the mock ignores it (one city, one warehouse).
        deadline_mins, when given, answers deadline met yes/no."""
        eta = self.config["eta_mins"]
        out = {"eta_mins": eta, "pincode": pincode}
        if deadline_mins is not None:
            out["deadline_mins"] = deadline_mins
            out["deadline_met"] = eta <= deadline_mins
        return out

    def get_offers(self, cart):
        """Offers that actually apply to this cart, best first.

        Only one offer may be used per platform - the caller takes
        the first (best) entry. A percent offer is capped, and an
        offer below its minimum cart value does not appear at all.
        """
        fees = self.get_fees(cart)
        item_total = fees["item_total"]
        applicable = []
        for offer in self.config["offers"]:
            if item_total < offer["min_cart"]:
                continue
            if offer["kind"] == "flat":
                discount = offer["value"]
            elif offer["kind"] == "percent":
                discount = min(offer.get("cap", 10 ** 9),
                               math.floor(item_total * offer["value"] / 100))
            elif offer["kind"] == "free_delivery":
                discount = fees["delivery"]
            else:
                continue
            applicable.append({"code": offer["code"], "kind": offer["kind"],
                               "discount": discount,
                               "min_cart": offer["min_cart"]})
        return sorted(applicable, key=lambda o: o["discount"], reverse=True)

    def landed_cost(self, cart):
        """Full breakdown for one platform. Demo/check helper only -
        Member C owns the real landed-cost engine and the optimizer,
        which consume get_fees/get_offers above."""
        fees = self.get_fees(cart)
        offers = self.get_offers(cart)
        best = offers[0] if offers else None
        discount = best["discount"] if best else 0
        return {
            "platform": self.platform,
            "item_total": fees["item_total"],
            "fees": {k: v for k, v in fees.items() if k != "item_total"},
            "fee_total": fees["total"],
            "best_offer": best["code"] if best else None,
            "discount": discount,
            "landed_total": fees["item_total"] + fees["total"] - discount,
        }


# ---------------------------------------------------------------------------
# The mock store - all three platforms behind one object
# ---------------------------------------------------------------------------

class MockStore:
    """Loads the catalog once and hands out one adapter per platform."""

    def __init__(self, catalog_path=None):
        self._by_platform = _load_catalog(catalog_path)

    def adapter(self, platform):
        return PlatformAdapter(platform, self._by_platform[platform])

    def platforms(self):
        return sorted(PLATFORMS)

    def search_all(self, query):
        """Same query on all three platforms - what match_products eats."""
        out = []
        for platform in self.platforms():
            out.extend(self.adapter(platform).search_products(query))
        return out

    def demo_basket(self, platform):
        """The 6-item demo basket as cart items for one platform."""
        listings = self._by_platform[platform]
        cart = []
        for key in DEMO_BASKET_KEYS:
            match = [l for l in listings if key in l["product_id"]]
            if not match:
                raise ValueError("no %s listing on %s" % (key, platform))
            cart.append({"product_id": match[0]["product_id"], "qty": 1})
        return cart


# ---------------------------------------------------------------------------
# Demo - run this file directly
# ---------------------------------------------------------------------------

def main():
    store = MockStore()
    print("Catalog: %d listings per platform"
          % len(store._by_platform["blinkit"]))
    print()
    print("search_products('milk') on each platform:")
    for platform in store.platforms():
        hits = store.adapter(platform).search_products("milk")
        print("  %-9s %s" % (platform, [(h["product_id"], h["price"], h["in_stock"]) for h in hits]))
    print()
    print("Out-of-stock is visible, not hidden:")
    print("  zepto curd in_stock:",
          store.adapter("zepto").search_products("curd")[0]["in_stock"])
    print("  instamart eggs in_stock:",
          store.adapter("instamart").search_products("eggs")[0]["in_stock"])
    print()
    print("Same 6-item basket, three landed totals:")
    totals = {}
    for platform in store.platforms():
        adapter = store.adapter(platform)
        cart = store.demo_basket(platform)
        result = adapter.landed_cost(cart)
        eta = adapter.get_eta(cart, deadline_mins=60)
        totals[platform] = result["landed_total"]
        print("  %-9s items %4d + fees %2d - %-8s %3d = %4d   ETA %d min (deadline met: %s)"
              % (platform, result["item_total"], result["fee_total"],
                 result["best_offer"], result["discount"],
                 result["landed_total"], eta["eta_mins"], eta["deadline_met"]))
    ok = (len(set(totals.values())) == 3
          and all(totals[p] == EXPECTED_LANDED[p] for p in totals))
    print()
    print("Demo check (3 different totals, matching hand-checked %s): %s"
          % (EXPECTED_LANDED, "PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
