"""REST endpoints. Thin: validation + HTTP mapping only; logic is in api/service.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.schemas import (
    ApproveBody,
    ClarifyRequest,
    DecisionResponse,
    EditBody,
    FaultBody,
    PlanRequest,
    RejectBody,
    ReminderBody,
    SessionView,
    TraceResponse,
)
from app.api.service import ShoppingService, get_service, view
from app.core.config import get_settings
from app.core.guardrails import GuardrailViolation
from app.core.models import ShoppingProposal
from app.planner.workflow import IllegalTransition
from app.tools.approval_tools import ApprovalError

router = APIRouter()


def _http(e: Exception) -> HTTPException:
    if isinstance(e, ApprovalError):
        return HTTPException(e.status_code, {"code": e.code, "message": e.message})
    if isinstance(e, GuardrailViolation):
        return HTTPException(status.HTTP_403_FORBIDDEN, {"code": e.code, "message": e.message})
    if isinstance(e, IllegalTransition):
        return HTTPException(status.HTTP_409_CONFLICT, {"code": "ILLEGAL_TRANSITION", "message": str(e)})
    raise e


@router.post("/api/shopping/plan", response_model=SessionView, status_code=status.HTTP_201_CREATED,
             tags=["shopping"], summary="Start a shopping workflow from natural language")
def plan(body: PlanRequest, svc: ShoppingService = Depends(get_service)):
    return view(svc.plan(body.text, body.user_id, body.pincode))


@router.post("/api/shopping/{session_id}/clarify", response_model=SessionView, tags=["shopping"],
             summary="Answer the agent's clarification question")
def clarify(session_id: str, body: ClarifyRequest, svc: ShoppingService = Depends(get_service)):
    try:
        return view(svc.clarify(session_id, body.answer))
    except (ApprovalError, IllegalTransition) as e:
        raise _http(e)


@router.get("/api/shopping/{session_id}", response_model=SessionView, tags=["shopping"],
            summary="Current workflow state")
def get_session(session_id: str, svc: ShoppingService = Depends(get_service)):
    try:
        return view(svc.get(session_id))
    except ApprovalError as e:
        raise _http(e)


@router.get("/api/shopping/{session_id}/trace", response_model=TraceResponse, tags=["shopping"],
            summary="Complete agent execution trace (every tool call and decision)")
def get_trace(session_id: str, svc: ShoppingService = Depends(get_service)):
    try:
        s = svc.get(session_id)
    except ApprovalError as e:
        raise _http(e)
    return TraceResponse(session_id=s.session_id, phase=s.phase.value, actions=s.trace, replans=s.replans)


@router.get("/api/shopping/{session_id}/proposal", response_model=ShoppingProposal, tags=["shopping"],
            summary="The current shopping proposal")
def get_proposal(session_id: str, svc: ShoppingService = Depends(get_service)):
    try:
        s = svc.get(session_id)
    except ApprovalError as e:
        raise _http(e)
    if s.proposal is None:
        raise HTTPException(404, {"code": "NO_PROPOSAL", "message": f"Session is in {s.phase.value}",
                                  "clarification_question": s.clarification_question})
    return s.proposal


@router.post("/api/shopping/{session_id}/approve", response_model=DecisionResponse, tags=["approval"],
             summary="Approve a specific proposal – prepares carts only, never pays")
def approve(session_id: str, body: ApproveBody, svc: ShoppingService = Depends(get_service)):
    try:
        return svc.approve(session_id, body.proposal_id, body.confirmed_items, body.remind_in_days)
    except (ApprovalError, GuardrailViolation, IllegalTransition) as e:
        raise _http(e)


@router.post("/api/shopping/{session_id}/reject", response_model=DecisionResponse, tags=["approval"],
             summary="Reject a proposal (optionally reject brands for next time)")
def reject(session_id: str, body: RejectBody, svc: ShoppingService = Depends(get_service)):
    try:
        return svc.reject(session_id, body.proposal_id, body.reason, body.rejected_brands)
    except (ApprovalError, IllegalTransition) as e:
        raise _http(e)


@router.post("/api/shopping/{session_id}/edit", response_model=DecisionResponse, tags=["approval"],
             summary="Edit a proposal – the agent re-plans and returns a new proposal version")
def edit(session_id: str, body: EditBody, svc: ShoppingService = Depends(get_service)):
    try:
        return svc.edit(session_id, body.proposal_id, body.changes)
    except (ApprovalError, IllegalTransition) as e:
        raise _http(e)


@router.get("/api/memory/preferences", tags=["memory"], summary="Saved preferences and their change history")
def preferences(user_id: str = Query("demo-user"), svc: ShoppingService = Depends(get_service)):
    return svc.preferences(user_id)


@router.get("/api/memory/episodes", tags=["memory"], summary="Previous shopping sessions")
def episodes(user_id: str = Query("demo-user"), limit: int = Query(20, ge=1, le=100),
             svc: ShoppingService = Depends(get_service)):
    return {"user_id": user_id, "episodes": svc.episodes(user_id, limit)}


@router.get("/api/memory/pantry", tags=["memory"], summary="What was bought and what is due for a refill")
def pantry(user_id: str = Query("demo-user"), svc: ShoppingService = Depends(get_service)):
    return svc.pantry(user_id)


@router.post("/api/reminders", status_code=status.HTTP_201_CREATED, tags=["reminders"],
             summary="set_reminder(time, text) – reorder / price-watch reminder")
def set_reminder(body: ReminderBody, svc: ShoppingService = Depends(get_service)):
    from app.tools.reminder_tools import ReminderError

    try:
        return svc.set_reminder(body.user_id, body.text, body.time, body.in_days, body.kind)
    except ReminderError as e:
        raise HTTPException(422, {"code": "BAD_REMINDER", "message": str(e)})


@router.get("/api/reminders", tags=["reminders"], summary="List reminders")
def list_reminders(user_id: str = Query("demo-user"), upcoming_only: bool = Query(False),
                   svc: ShoppingService = Depends(get_service)):
    return {"user_id": user_id, "reminders": svc.reminders(user_id, upcoming_only)}


@router.get("/health", tags=["system"], summary="Backend health")
def health(svc: ShoppingService = Depends(get_service)):
    return svc.health()


# ---------------------------------------------------------------- demo faults
@router.post("/api/dev/faults", tags=["demo"], summary="Inject a fault into the mock store (demo / tests)")
def add_fault(body: FaultBody):
    if not get_settings().enable_fault_injection or get_settings().store_url:
        raise HTTPException(403, {"code": "FAULTS_DISABLED", "message": "Fault injection is disabled"})
    from app.adapters.mock_store import Fault, get_store

    get_store().add_fault(Fault(**body.model_dump()))
    return {"ok": True, "active_faults": len(get_store().faults)}


@router.delete("/api/dev/faults", tags=["demo"], summary="Clear injected faults")
def clear_faults():
    if not get_settings().enable_fault_injection or get_settings().store_url:
        raise HTTPException(403, {"code": "FAULTS_DISABLED", "message": "Fault injection is disabled"})
    from app.adapters.mock_store import get_store

    get_store().clear_faults()
    return {"ok": True}
