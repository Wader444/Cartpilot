"""Pantry memory: what the user bought and when (recorded only after an approval).

Interface is ready for the "pantry autopilot" stretch goal:
    record_purchase(user_id, proposal)       -> number of rows written
    items(user_id)                           -> latest purchase per item
    due_for_refill(user_id, now, default_days=7) -> items whose usual cycle has elapsed
The refill rule is deliberately simple: the average gap between past
purchases of an item, or `default_days` when there is only one purchase.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.core.models import ShoppingProposal, new_id, utcnow
from app.memory.database import Database


class PantryMemory:
    def __init__(self, db: Database):
        self.db = db

    def record_purchase(self, user_id: str, proposal: ShoppingProposal, when: Optional[datetime] = None) -> int:
        ts = (when or utcnow()).isoformat()
        n = 0
        for cart in proposal.carts:
            for it in cart.items:
                req = next((i for i in proposal.request.items if i.name == it.request_item), None)
                self.db.execute(
                    "INSERT INTO pantry (id, user_id, item, product_id, product_name, brand, quantity, unit, packs, "
                    "platform, proposal_id, purchased_at) VALUES (:id, :u, :i, :pid, :pn, :b, :q, :un, :pk, :pl, :pr, :t)",
                    {"id": new_id("pan"), "u": user_id, "i": it.request_item, "pid": it.listing.product_id,
                     "pn": it.listing.product.name, "b": it.listing.product.brand,
                     "q": req.quantity if req else None, "un": req.unit if req else None, "pk": it.packs,
                     "pl": cart.platform.value, "pr": proposal.proposal_id, "t": ts})
                n += 1
        return n

    def history(self, user_id: str, item: Optional[str] = None) -> list[dict]:
        sql, params = "SELECT * FROM pantry WHERE user_id = :u", {"u": user_id}
        if item:
            sql += " AND item = :i"
            params["i"] = item
        return self.db.query(sql + " ORDER BY purchased_at DESC", params)

    def items(self, user_id: str) -> list[dict]:
        latest: dict[str, dict] = {}
        for r in self.history(user_id):
            latest.setdefault(r["item"], r)
        return list(latest.values())

    def due_for_refill(self, user_id: str, now: Optional[datetime] = None, default_days: int = 7) -> list[dict]:
        now = now or utcnow()
        due = []
        by_item: dict[str, list[datetime]] = {}
        for r in self.history(user_id):
            by_item.setdefault(r["item"], []).append(datetime.fromisoformat(r["purchased_at"]))
        for item, times in by_item.items():
            times = sorted(set(times))
            if len(times) >= 2:
                gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:])]
                cycle = timedelta(seconds=sum(gaps) / len(gaps))
            else:
                cycle = timedelta(days=default_days)
            last = times[-1]
            if now.astimezone(last.tzinfo) >= last + cycle:
                due.append({"item": item, "last_bought": last.isoformat(), "cycle_days": round(cycle.days, 1)})
        return sorted(due, key=lambda d: d["last_bought"])
