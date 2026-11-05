"""Service layer between FastAPI routes and the orchestrator.

All behaviour lives here (and below), so it is fully testable without an HTTP
server; routes only translate to/from HTTP.
"""
from __future__ import annotations

from typing import Optional

from app.api.schemas import DecisionResponse, SessionView
from app.core.models import AgentState, ApprovalDecision, ApprovalRequest, Phase, ProposalEdit
from app.memory.preference_memory import PreferenceChange
from app.planner.orchestrator import CartPilotOrchestrator

NEXT = {
    Phase.AWAITING_CLARIFICATION: ["POST /api/shopping/{id}/clarify"],
    Phase.AWAITING_APPROVAL: ["POST /api/shopping/{id}/approve", "POST /api/shopping/{id}/edit",
                              "POST /api/shopping/{id}/reject"],
    Phase.FAILED: ["POST /api/shopping/plan (start again)"],
}


def view(state: AgentState) -> SessionView:
    p = state.proposal
    return SessionView(
        session_id=state.session_id, user_id=state.user_id, phase=state.phase.value,
        clarification_question=state.clarification_question, request=state.request, plan_steps=state.plan_steps,
        proposal_id=p.proposal_id if p else None, proposal_summary=p.summary if p else None,
        proposal_status=p.status.value if p else None, prepared_carts=state.prepared_carts, replans=state.replans,
        memory_notes=state.memory_notes, errors=state.errors, trace_length=len(state.trace),
        next_actions=[a.replace("{id}", state.session_id) for a in NEXT.get(state.phase, [])])


class ShoppingService:
    def __init__(self, orchestrator: Optional[CartPilotOrchestrator] = None):
        self.o = orchestrator or CartPilotOrchestrator()

    # sessions
    def plan(self, text: str, user_id: str, pincode: Optional[str]) -> AgentState:
        return self.o.start(text, user_id=user_id, pincode=pincode)

    def clarify(self, session_id: str, answer: str) -> AgentState:
        return self.o.clarify(session_id, answer)

    def get(self, session_id: str) -> AgentState:
        return self.o.get(session_id)

    # decisions – actor is always "user": these are only reachable from the API
    def _respond(self, state: AgentState, changes: list[PreferenceChange], message: str) -> DecisionResponse:
        return DecisionResponse(session=view(state), proposal=state.proposal,
                                preference_changes=[c.model_dump() for c in changes], message=message)

    def approve(self, session_id: str, proposal_id: str, confirmed_items: list[str],
                remind_in_days: Optional[int] = None) -> DecisionResponse:
        req = ApprovalRequest(proposal_id=proposal_id, decision=ApprovalDecision.APPROVE,
                              confirmed_items=confirmed_items, actor="user", remind_in_days=remind_in_days)
        state, changes = self.o.approve_proposal(session_id, req)
        return self._respond(state, changes, f"Prepared {len(state.prepared_carts)} cart(s). No payment was made and "
                                             f"no order was placed – open each app to review and pay.")

    def reject(self, session_id: str, proposal_id: str, reason: Optional[str], rejected_brands: list[str]) -> DecisionResponse:
        req = ApprovalRequest(proposal_id=proposal_id, decision=ApprovalDecision.REJECT, reason=reason,
                              rejected_brands=rejected_brands, actor="user")
        state, changes = self.o.reject_proposal(session_id, req)
        return self._respond(state, changes, "Proposal rejected. Nothing was prepared.")

    def edit(self, session_id: str, proposal_id: str, changes: ProposalEdit) -> DecisionResponse:
        req = ApprovalRequest(proposal_id=proposal_id, decision=ApprovalDecision.EDIT, changes=changes, actor="user")
        state, pchanges = self.o.edit_proposal(session_id, req)
        msg = (f"Re-planned as proposal v{state.proposal.version} ({state.proposal.proposal_id})."
               if state.proposal and state.proposal.proposal_id != proposal_id else "Edit applied.")
        return self._respond(state, pchanges, msg)

    # memory
    def preferences(self, user_id: str) -> dict:
        return {"preferences": self.o.prefs.get(user_id).model_dump(mode="json"),
                "history": self.o.prefs.history(user_id, limit=50)}

    def episodes(self, user_id: str, limit: int) -> list[dict]:
        return self.o.episodes.list(user_id, limit=limit)

    def pantry(self, user_id: str) -> dict:
        return {"user_id": user_id, "items": self.o.pantry.items(user_id),
                "due_for_refill": self.o.pantry.due_for_refill(user_id, self.o.clock.now())}

    def set_reminder(self, user_id: str, text: str, time, in_days, kind: str):
        from datetime import timedelta

        from app.tools.reminder_tools import ReminderError

        now = self.o.clock.now()
        if (time is None) == (in_days is None):
            raise ReminderError("Give exactly one of 'time' or 'in_days'")
        when = time if time is not None else now + timedelta(days=in_days)
        return self.o.reminders.set_reminder(user_id, when, text, now, kind=kind)

    def reminders(self, user_id: str, upcoming_only: bool):
        return self.o.reminders.list(user_id, include_past=not upcoming_only, now=self.o.clock.now())

    def health(self) -> dict:
        from app.memory.database import HAVE_SQLALCHEMY

        return {"status": "ok" if self.o.db.ping() else "degraded",
                "database": {"ok": self.o.db.ping(), "backend": self.o.db.backend,
                             "sqlalchemy_installed": HAVE_SQLALCHEMY},
                "platforms": [p.value for p in self.o.adapters],
                "platform_source": "http" if self.o.settings.store_url else "mock (in-process)",
                "intent_parser": "llm+deterministic" if self.o.settings.llm_enabled else "deterministic",
                "matching": self._impl(self.o.settings.matching_module),
                "optimizer": self._impl(self.o.settings.optimizer_module),
                "payments": "disabled – CartPilot never pays or places orders"}

    @staticmethod
    def _impl(module: str) -> str:
        import importlib

        try:
            importlib.import_module(module)
            return module
        except Exception:
            return "built-in fallback"


_service: Optional[ShoppingService] = None


def get_service() -> ShoppingService:
    global _service
    if _service is None:
        _service = ShoppingService()
    return _service


def set_service(s: Optional[ShoppingService]) -> None:
    global _service
    _service = s
