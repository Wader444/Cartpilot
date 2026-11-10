"""CartPilot — Price history (Member B, implementation.md Phase 14-19).

Prepared 2026-11-10, the night before the 24-hour build (Wed 11 Nov
10:00 AM - Thu 12 Nov 10:00 AM IST), so Hours 14-19 do not start from
scratch. Copy this file to app/agents/price_history.py (or wherever
Member A expects it) and cartpilot-price-history.csv beside it.

What it is
----------
* cartpilot-price-history.csv: 21 days of mock prices (2026-10-21 to
  2026-11-10), 39 listings x 21 days = 819 rows -- the 30 starter
  listings plus the 9 substitute listings (P11-P13) from
  cartpilot-substitute-listings.csv, so the history still works if she
  takes the substitutions path. That is 3 weeks, inside the plan's
  "2-4 weeks per key SKU and platform". Prices are MOCK,
  generated as a deterministic +/-3% walk around each listing's current
  catalog price, ending exactly on the current price on 2026-11-10 --
  except for ONE deliberate fake discount (see below).
* check_discount(): the simple query Member A / Member C (the Auditor)
  can call: current "offer" price vs. recent average.

The fake discount (the Phase 14-19 "done when")
-----------------------------------------------
Zepto tomato (ZEP-TOMATO-1KG): MRP Rs 45, current price Rs 42, so it
looks 6.7% off. But it sold at Rs 35-36 for 17 days and only jumped to
Rs 42 on 2026-11-07. Its 14-day average is Rs 37.36, so Rs 42 is
+12.4% ABOVE the recent average -- no real saving at all. The Auditor
must flag exactly this one listing, and no other.

Flag rule (deliberately simple, explainable to a judge):
  flagged = claimed discount vs MRP >= 5%
            AND current price > 14-day average x 1.05
A genuine discount (e.g. Blinkit atta Rs 289 vs MRP Rs 310, 14-day
average ~Rs 289) is below its average, so it is NOT flagged.

Usage
-----
    from price_history import PriceHistory
    ph = PriceHistory()  # finds data/mock_catalog/price_history.csv
    ph.recent_average("ZEP-TOMATO-1KG", days=14)   # -> 37.36
    ph.check_discount("ZEP-TOMATO-1KG", current_price=42, mrp=45)
    # -> {"flagged": True, "claimed_discount_pct": 6.7,
    #     "vs_avg_pct": 12.4, "reason": ...}
    ph.check_all()  # checks every listing in data/mock_catalog/catalog.csv
    # -> checks every current catalog listing; demo expects exactly
    #    one flagged listing (ZEP-TOMATO-1KG).

Run `python app/agents/price_history.py` (from the repo root) for the demo check.
No Neo4j connection is needed; the CSV is the seed. If the team wants
the history in the graph as well, the same rows map 1:1 to
(:Listing)-[:PRICE_ON {date}]->(:PricePoint {price}) -- ask bt and it
can be generated, but it is not needed for the Auditor check.
"""

import csv
import os
from datetime import date

AS_OF = date(2026, 11, 10)  # last history date; build starts 2026-11-11
FAKE_DISCOUNT_ID = "ZEP-TOMATO-1KG"


def _find_file(name):
    """Find name next to this file, in cwd, or by walking up (same
    pattern as match_products.py, so it works from app/agents/)."""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [os.path.join(here, name), os.path.join(os.getcwd(), name)]
    d = here
    for _ in range(5):
        candidates.append(os.path.join(d, name))
        d = os.path.dirname(d)
    # Repo layout: data lives in data/mock_catalog/ at the repo root.
    repo_root = os.path.dirname(os.path.dirname(here))
    candidates.append(os.path.join(repo_root, "data", "mock_catalog", name))
    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError(name + " not found next to this file or above it")


class PriceHistory:
    """Daily mock prices per listing, loaded from the history CSV."""

    def __init__(self, path=None):
        self.path = path or _find_file("price_history.csv")
        self.series = {}  # product_id -> [(date, price)]
        with open(self.path, newline="") as f:
            for row in csv.DictReader(f):
                d = date.fromisoformat(row["date"])
                self.series.setdefault(row["product_id"], []).append(
                    (d, int(row["price"])))
        for pid in self.series:
            self.series[pid].sort()

    def prices(self, product_id, days=None, as_of=AS_OF):
        """Prices for a listing, most recent `days` up to `as_of`."""
        pts = [p for d, p in self.series.get(product_id, []) if d <= as_of]
        if days is not None:
            pts = pts[-days:]
        return pts

    def recent_average(self, product_id, days=14, as_of=AS_OF):
        pts = self.prices(product_id, days=days, as_of=as_of)
        if not pts:
            raise ValueError("no price history for %r" % product_id)
        return round(sum(pts) / len(pts), 2)

    def check_discount(self, product_id, current_price, mrp,
                       days=14, as_of=AS_OF):
        """Current offer price vs. recent average. Returns a dict the
        Auditor can use directly; `flagged` is the fake-discount call."""
        avg = self.recent_average(product_id, days=days, as_of=as_of)
        claimed = round((mrp - current_price) / mrp * 100, 1) if mrp else 0.0
        vs_avg = round((current_price - avg) / avg * 100, 1)
        flagged = claimed >= 5.0 and current_price > avg * 1.05
        reason = (
            "FAKE DISCOUNT: claims %.1f%% off MRP (Rs %d) but Rs %d is "
            "%.1f%% ABOVE the %d-day average of Rs %.2f"
            % (claimed, mrp, current_price, vs_avg, days, avg)
            if flagged else
            "genuine: Rs %d vs %d-day average Rs %.2f (%.1f%% vs avg)"
            % (current_price, days, avg, vs_avg))
        return {"product_id": product_id, "current_price": current_price,
                "mrp": mrp, "avg_%dd" % days: avg,
                "claimed_discount_pct": claimed, "vs_avg_pct": vs_avg,
                "flagged": flagged, "reason": reason}

    def check_all(self, catalog_path=None, days=14):
        """Check every listing in the starter catalog at its current
        price. Returns (results, flagged_list)."""
        catalog = catalog_path or _find_file("catalog.csv")
        results, flagged = [], []
        with open(catalog, newline="") as f:
            for row in csv.DictReader(f):
                r = self.check_discount(row["product_id"],
                                        int(row["price"]), int(row["mrp"]),
                                        days=days)
                results.append(r)
                if r["flagged"]:
                    flagged.append(r)
        return results, flagged


def main():
    ph = PriceHistory()
    n_listings = len(ph.series)
    n_rows = sum(len(v) for v in ph.series.values())
    print("Price history: %d listings, %d rows, %s to %s"
          % (n_listings, n_rows, "2026-10-21", AS_OF))
    results, flagged = ph.check_all()
    print("Checked %d current listings against 14-day averages" % len(results))
    for r in flagged:
        print("FLAGGED: %s -- %s" % (r["product_id"], r["reason"]))
    # The demo check: exactly one fake discount, and it is the seeded one.
    ok = ([r["product_id"] for r in flagged] == [FAKE_DISCOUNT_ID])
    print("Demo check: %s (flagged=%s, expected=[%s])"
          % ("PASS" if ok else "FAIL",
             [r["product_id"] for r in flagged], FAKE_DISCOUNT_ID))
    # Show the genuine-discount contrast for the deck / Auditor.
    genuine = ph.check_discount("BLK-ATTA-5KG", 289, 310)
    print("Contrast (genuine): %s" % genuine["reason"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
