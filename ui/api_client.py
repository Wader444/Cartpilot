"""UI <-> backend client.

USE_MOCK=1 (default): the UI shows ui/mock_data.py, as before.
USE_MOCK=0: the UI talks to Member A's FastAPI backend and every number comes
from the backend's tool-verified proposal. `to_ui_proposal` converts the
backend's ShoppingProposal into the dict shape app.py already renders, so the
screens do not need to change.

Backend endpoints used (see http://localhost:8000/docs):
  POST /api/shopping/plan                {"text": goal}      -> session
  POST /api/shopping/{session_id}/clarify {"answer": text}   -> session
  GET  /api/shopping/{session_id}/proposal                    -> proposal
  POST /api/shopping/{session_id}/approve {"proposal_id", "confirmed_items", "remind_in_days"}
  POST /api/shopping/{session_id}/reject  {"proposal_id", "reason", "rejected_brands"}
  POST /api/shopping/{session_id}/edit    {"proposal_id", "changes": {...}}
  GET  /api/shopping/{session_id}/trace
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import httpx

from mock_data import SAMPLE_PROPOSAL

USE_MOCK = os.getenv("USE_MOCK", "1") == "1"
API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("API_TIMEOUT", "60"))

PLATFORM_NAMES = {"blinkit": "Blinkit", "zepto": "Zepto", "instamart": "Swiggy Instamart"}


class ClarificationNeeded(Exception):
    """The agent needs one more answer (budget or deadline) before it can plan."""

    def __init__(self, question: str, session_id: str):
        super().__init__(question)
        self.question = question
        self.session_id = session_id


class ApiError(Exception):
    """The backend refused a request; `message` is safe to show to the user."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


# --------------------------------------------------------------------------- HTTP
def _call(method: str, path: str, json: Optional[dict] = None) -> dict:
    resp = httpx.request(method, f"{API_URL}{path}", json=json, timeout=TIMEOUT)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", {})
        except ValueError:
            detail = {}
        if isinstance(detail, dict):
            code, msg = detail.get("code", "ERROR"), detail.get("message", resp.text)
        else:  # FastAPI request-validation errors are a list
            code, msg = "INVALID_INPUT", str(detail)
        raise ApiError(resp.status_code, code, str(msg))
    return resp.json()


# --------------------------------------------------------------------------- mapping
def _display(platform: str) -> str:
    return PLATFORM_NAMES.get(platform, platform.title())


def _whole(x):
    """1200.0 -> 1200 so the UI shows ₹1,200, not ₹1,200.0."""
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _fmt_time(iso: Optional[str]) -> str:
    if not iso:
        return "-"
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def to_ui_proposal(session: dict, proposal: dict) -> dict:
    """Backend ShoppingProposal JSON -> the dict shape ui/app.py renders.

    Pure function (no network) so it can be unit-tested. No number is
    computed here except copying; all totals come from the backend.
    """
    eta_miss = {w.get("platform") for w in proposal.get("warnings", []) if w.get("code") == "ETA_MISS"}
    platforms = []
    for c in proposal.get("carts", []):
        platforms.append({
            "platform": _display(c["platform"]),
            "platform_id": c["platform"],
            "eta_mins": c.get("eta_mins"),
            "deadline_met": c.get("eta_mins") is not None and c["platform"] not in eta_miss,
            "cart": [{
                "product": it["listing"]["product"],
                "listing": {k: it["listing"][k] for k in ("platform", "product_id", "price", "mrp", "in_stock", "eta_mins")},
                "qty": it["packs"],
                "line_total": it["line_total"],
                "unit_price": it.get("unit_price"),
                "unit_label": it.get("unit_label"),
                "request_item": it["request_item"],
                "is_substitute": it.get("is_substitute", False),
                "requires_confirmation": it["listing"]["product_id"] in proposal.get("requires_confirmation", []),
            } for it in c.get("items", [])],
            "landed_cost": {
                "item_total": c["item_total"], "handling": c["handling"], "delivery": c["delivery"],
                "small_cart": c["small_cart"], "surge": c["surge"], "offers": c["offer_discount"],
                "offer_id": c.get("offer_id"), "landed_total": c["landed_total"],
            },
        })
    best = proposal.get("best_single_platform")
    return {
        # fields app.py already uses
        "goal": session.get("request", {}).get("raw_text") or proposal["request"].get("raw_text", ""),
        "constraints": {"budget": _whole(proposal.get("budget")), "deadline": proposal.get("deadline")},
        "platforms": platforms,
        "split_total": proposal.get("total_landed", 0),
        "best_single_app": {"platform": _display(best) if best else "n/a",
                            "landed_total": proposal.get("best_single_total") or proposal.get("total_landed", 0)},
        "savings": proposal.get("savings_vs_single") or 0,
        "last_checked": _fmt_time(proposal.get("last_checked_at")),
        "warnings": [w["message"] for w in proposal.get("warnings", [])],
        # extra fields for approval / audit screens
        "session_id": proposal["session_id"],
        "proposal_id": proposal["proposal_id"],
        "version": proposal.get("version", 1),
        "status": proposal.get("status"),
        "approvable": (proposal.get("status") == "pending_approval" and proposal.get("within_budget", True)
                       and bool(proposal.get("carts"))),
        "within_budget": proposal.get("within_budget", True),
        "deadline_met": proposal.get("deadline_met"),
        "savings_pct": proposal.get("savings_pct"),
        "summary": proposal.get("summary", ""),
        "requires_confirmation": proposal.get("requires_confirmation", []),
        "unresolved_items": proposal.get("unresolved_items", []),
        "warning_details": proposal.get("warnings", []),
        "replan_count": proposal.get("replan_count", 0),
    }


def _proposal_for(session: dict) -> dict:
    if session.get("phase") == "AWAITING_CLARIFICATION":
        raise ClarificationNeeded(session.get("clarification_question") or "Please add a budget and deadline.",
                                  session["session_id"])
    if session.get("phase") == "FAILED" or not session.get("proposal_id"):
        errors = session.get("errors") or ["The planner could not build a proposal."]
        raise ApiError(422, "PLANNING_FAILED", errors[0])
    proposal = _call("GET", f"/api/shopping/{session['session_id']}/proposal")
    return to_ui_proposal(session, proposal)


# --------------------------------------------------------------------------- public API
def get_proposal(goal: str) -> dict:
    """Plan a basket. Raises ClarificationNeeded if the agent needs budget/deadline first."""
    if USE_MOCK:
        return {**SAMPLE_PROPOSAL, "goal": goal or SAMPLE_PROPOSAL["goal"]}
    session = _call("POST", "/api/shopping/plan", {"text": goal})
    return _proposal_for(session)


def answer_clarification(session_id: str, answer: str) -> dict:
    session = _call("POST", f"/api/shopping/{session_id}/clarify", {"answer": answer})
    return _proposal_for(session)


def approve(session_id: str, proposal_id: str, confirmed_items: Optional[list] = None,
            remind_in_days: Optional[int] = None) -> dict:
    """Prepares carts only – the backend never pays or places orders."""
    body = {"proposal_id": proposal_id, "confirmed_items": confirmed_items or []}
    if remind_in_days:
        body["remind_in_days"] = remind_in_days
    return _call("POST", f"/api/shopping/{session_id}/approve", body)


def reject(session_id: str, proposal_id: str, reason: str = "", rejected_brands: Optional[list] = None) -> dict:
    return _call("POST", f"/api/shopping/{session_id}/reject",
                 {"proposal_id": proposal_id, "reason": reason or None, "rejected_brands": rejected_brands or []})


def edit(session_id: str, proposal_id: str, changes: dict) -> dict:
    """changes: remove_items, add_items, update_quantities, budget, deadline, reject_brands, ...

    Returns the NEW proposal (new proposal_id) in the UI shape.
    """
    resp = _call("POST", f"/api/shopping/{session_id}/edit", {"proposal_id": proposal_id, "changes": changes})
    return _proposal_for(resp["session"])


def get_trace(session_id: str) -> dict:
    return _call("GET", f"/api/shopping/{session_id}/trace")
