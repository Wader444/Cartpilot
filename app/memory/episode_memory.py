"""Episode memory: one row per proposal shown to the user, with the user's
decision. Kept separate from preference memory (different table, different
class). The planner reads recent episodes; it never rewrites them.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from app.core.models import ShoppingProposal, new_id, utcnow
from app.memory.database import Database


def _loads(v: Optional[str]) -> Any:
    return json.loads(v) if v else None


class EpisodeMemory:
    def __init__(self, db: Database):
        self.db = db

    def record_proposal(self, user_id: str, proposal: ShoppingProposal) -> str:
        selected = [{"platform": c.platform.value, "product_id": it.listing.product_id,
                     "name": it.listing.product.name, "brand": it.listing.product.brand,
                     "request_item": it.request_item, "packs": it.packs, "line_total": it.line_total,
                     "is_substitute": it.is_substitute}
                    for c in proposal.carts for it in c.items]
        allocation = {c.platform.value: {"items": len(c.items), "landed_total": c.landed_total} for c in proposal.carts}
        eid = new_id("ep")
        self.db.execute(
            "INSERT INTO episodes (id, user_id, session_id, proposal_id, request_json, proposal_json, "
            "selected_products_json, platform_allocation_json, estimated_savings, total_landed, decision, "
            "edits_json, created_at, decided_at) VALUES (:id, :u, :s, :p, :rq, :pj, :sel, :alloc, :sav, :tot, "
            "'pending', NULL, :t, NULL)",
            {"id": eid, "u": user_id, "s": proposal.session_id, "p": proposal.proposal_id,
             "rq": proposal.request.model_dump_json(), "pj": proposal.model_dump_json(),
             "sel": json.dumps(selected), "alloc": json.dumps(allocation),
             "sav": proposal.savings_vs_single, "tot": proposal.total_landed, "t": utcnow().isoformat()})
        return eid

    def record_decision(self, proposal_id: str, decision: str, edits: Optional[dict] = None) -> None:
        self.db.execute("UPDATE episodes SET decision = :d, edits_json = :e, decided_at = :t WHERE proposal_id = :p",
                        {"d": decision, "e": json.dumps(edits) if edits else None, "t": utcnow().isoformat(),
                         "p": proposal_id})

    def _row(self, r: dict) -> dict:
        return {"episode_id": r["id"], "user_id": r["user_id"], "session_id": r["session_id"],
                "proposal_id": r["proposal_id"], "request": _loads(r["request_json"]),
                "selected_products": _loads(r["selected_products_json"]),
                "platform_allocation": _loads(r["platform_allocation_json"]),
                "estimated_savings": r["estimated_savings"], "total_landed": r["total_landed"],
                "decision": r["decision"], "edits": _loads(r["edits_json"]),
                "created_at": r["created_at"], "decided_at": r["decided_at"]}

    def list(self, user_id: str, limit: int = 20, decision: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM episodes WHERE user_id = :u"
        params: dict = {"u": user_id, "n": limit}
        if decision:
            sql += " AND decision = :d"
            params["d"] = decision
        return [self._row(r) for r in self.db.query(sql + " ORDER BY created_at DESC LIMIT :n", params)]

    def last_approved(self, user_id: str) -> Optional[dict]:
        rows = self.list(user_id, limit=1, decision="approved")
        return rows[0] if rows else None
