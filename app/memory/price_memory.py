"""Price memory: every price CartPilot observes through search_products is stored.

Same output shape as Member B's price-history interface, so the Auditor can
use it for fake-discount checks when no seeded history exists:
    history(platform, product_id, days=28) -> [{"days_ago": int, "price": float}]
    recent_average(platform, product_id, days=28) -> float | None
Only tool outputs are stored – never estimates.
"""
from __future__ import annotations

from datetime import timedelta
from statistics import mean
from typing import Optional

from app.core.models import Listing, Platform, new_id, utcnow
from app.memory.database import Database


class PriceMemory:
    def __init__(self, db: Database):
        self.db = db

    def record_listings(self, listings: list[Listing], session_id: Optional[str] = None) -> int:
        now = utcnow().isoformat()
        seen = set()
        n = 0
        for l in listings:
            if l.key in seen:
                continue
            seen.add(l.key)
            self.db.execute("INSERT INTO price_observations (id, platform, product_id, price, mrp, in_stock, "
                            "session_id, observed_at) VALUES (:id, :p, :pid, :pr, :m, :s, :sess, :t)",
                            {"id": new_id("po"), "p": l.platform.value, "pid": l.product_id, "pr": l.price,
                             "m": l.mrp, "s": int(l.in_stock), "sess": session_id, "t": now})
            n += 1
        return n

    def history(self, platform: Platform, product_id: str, days: int = 28,
                exclude_session: Optional[str] = None) -> list[dict]:
        """Past observations. Exclude the current session so a price is never compared with itself."""
        now = utcnow()
        since = (now - timedelta(days=days)).isoformat()
        rows = self.db.query("SELECT price, observed_at FROM price_observations WHERE platform = :p AND "
                             "product_id = :pid AND observed_at >= :since AND "
                             "(session_id IS NULL OR session_id != :ex) ORDER BY observed_at",
                             {"p": platform.value, "pid": product_id, "since": since, "ex": exclude_session or ""})
        from datetime import datetime

        return [{"days_ago": (now - datetime.fromisoformat(r["observed_at"])).days, "price": r["price"]} for r in rows]

    def recent_average(self, platform: Platform, product_id: str, days: int = 28) -> Optional[float]:
        h = self.history(platform, product_id, days)
        return round(mean(x["price"] for x in h), 2) if h else None
