"""Explicit state machine for the agent loop.

    PLAN → ACT → OBSERVE → ACT(optimize) → VERIFY ─┬→ PROPOSE → AWAITING_APPROVAL → FINALIZE → DONE
                                ▲                   │
                                └──── re-plan ──────┘

Illegal transitions raise. In particular nothing can reach FINALIZE except
from AWAITING_APPROVAL, and only the approval gate performs that transition.
"""
from __future__ import annotations

from app.core.models import ActionStatus, AgentState, Phase, utcnow
from app.core.trace import TraceRecorder

P = Phase
TRANSITIONS: dict[Phase, set[Phase]] = {
    P.PLAN: {P.ACT, P.AWAITING_CLARIFICATION, P.FAILED},
    P.AWAITING_CLARIFICATION: {P.PLAN, P.FAILED},
    P.ACT: {P.OBSERVE, P.VERIFY, P.FAILED},
    P.OBSERVE: {P.ACT, P.VERIFY, P.FAILED},
    P.VERIFY: {P.ACT, P.PROPOSE, P.FAILED},
    P.PROPOSE: {P.AWAITING_APPROVAL, P.FAILED},
    P.AWAITING_APPROVAL: {P.FINALIZE, P.REJECTED, P.PLAN},   # approve | reject | edit
    P.FINALIZE: {P.DONE, P.FAILED},
    P.DONE: set(),
    P.REJECTED: set(),
    P.FAILED: {P.PLAN},
}


class IllegalTransition(Exception):
    pass


def transition(state: AgentState, to: Phase, rec: TraceRecorder, reason: str = "",
               _gate: bool = False) -> None:
    frm = state.phase
    if to == P.FINALIZE and not _gate:
        rec.note("workflow.transition", "Blocked: only the approval gate may enter FINALIZE",
                 input={"from": frm.value, "to": to.value}, status=ActionStatus.BLOCKED)
        raise IllegalTransition("FINALIZE can only be entered through the approval gate")
    if to not in TRANSITIONS[frm]:
        raise IllegalTransition(f"{frm.value} → {to.value} is not allowed")
    state.phase = to
    state.updated_at = utcnow()
    rec.note("workflow.transition", reason or f"{frm.value} → {to.value}",
             input={"from": frm.value, "to": to.value}, phase=to)
