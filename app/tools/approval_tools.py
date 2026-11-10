"""Human approval gate – CRITICAL SAFETY COMPONENT.

* build_cart can only run with a single-use ApprovalToken minted by
  ApprovalGate.approve() for one specific proposal id + content fingerprint.
* Only actor == "user" (set by the API layer) can approve. The planner,
  intent agent or an LLM cannot produce an approval.
* Over-budget proposals, stale (edited) proposals and unconfirmed
  medium-confidence matches cannot be approved.
* There is no payment function anywhere in CartPilot. Prepared carts are
  checked to confirm no payment and no order happened.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from app.adapters.base import PlatformAdapter
from app.core.guardrails import GuardrailViolation, assert_no_invented_numbers, enforce_budget
from app.core.models import (
    ActionStatus,
    AgentState,
    ApprovalDecision,
    ApprovalRequest,
    CartLine,
    Phase,
    Platform,
    PreparedCart,
    ProposalStatus,
    ShoppingProposal,
    utcnow,
)
from app.core.trace import ToolCallError, TraceRecorder


class ApprovalError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(f"[{code}] {message}")
        self.code, self.message, self.status_code = code, message, status_code


def proposal_fingerprint(p: ShoppingProposal) -> str:
    body = {"id": p.proposal_id, "version": p.version, "total": p.total_landed,
            "carts": [[c.platform.value, c.landed_total, [(i.listing.product_id, i.packs, i.line_total) for i in c.items]]
                      for c in p.carts]}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


@dataclass
class ApprovalToken:
    token: str
    proposal_id: str
    session_id: str
    fingerprint: str
    issued_at: datetime = field(default_factory=utcnow)
    used: bool = False


class ApprovalGate:
    def __init__(self):
        self._tokens: dict[str, ApprovalToken] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ checks
    @staticmethod
    def _current(state: AgentState, proposal_id: str) -> ShoppingProposal:
        if state.proposal is None:
            raise ApprovalError("NO_PROPOSAL", "This session has no proposal yet", 404)
        if proposal_id in state.proposal_history and proposal_id != state.proposal.proposal_id:
            raise ApprovalError("STALE_PROPOSAL", f"Proposal {proposal_id} was replaced by "
                                                  f"{state.proposal.proposal_id}; review the new version", 409)
        if state.proposal.proposal_id != proposal_id:
            raise ApprovalError("UNKNOWN_PROPOSAL", f"Proposal {proposal_id} does not belong to this session", 404)
        if state.phase != Phase.AWAITING_APPROVAL:
            raise ApprovalError("NOT_AWAITING_APPROVAL", f"Session is in {state.phase.value}, not awaiting approval", 409)
        return state.proposal

    # ------------------------------------------------------------------ decisions
    def approve(self, state: AgentState, req: ApprovalRequest, rec: TraceRecorder) -> ApprovalToken:
        try:
            if req.decision != ApprovalDecision.APPROVE:
                raise ApprovalError("WRONG_DECISION", "approve() needs decision=approve", 400)
            if req.actor != "user":
                raise ApprovalError("NOT_USER", "Only the user can approve a proposal", 403)
            p = self._current(state, req.proposal_id)
            if p.status == ProposalStatus.OVER_BUDGET or not p.within_budget:
                raise ApprovalError("OVER_BUDGET", "This plan is above your budget. Edit the basket or the budget first", 422)
            if p.status != ProposalStatus.PENDING_APPROVAL:
                raise ApprovalError("NOT_PENDING", f"Proposal status is {p.status.value}", 409)
            if not p.carts:
                raise ApprovalError("EMPTY_PROPOSAL", "Nothing to approve – no item could be sourced", 422)
            missing = sorted(set(p.requires_confirmation) - set(req.confirmed_items))
            if missing:
                raise ApprovalError("CONFIRMATION_REQUIRED",
                                    f"Confirm these substitute/low-confidence items first: {', '.join(missing)}", 422)
            enforce_budget(p.total_landed, p.budget)
            assert_no_invented_numbers(p, state.trace)
        except (ApprovalError, GuardrailViolation) as e:
            rec.note("approval_gate.approve", "Approval refused", input=req.model_dump(mode="json"),
                     output={"error": str(e)}, status=ActionStatus.BLOCKED)
            raise
        token = ApprovalToken(token=secrets.token_urlsafe(24), proposal_id=p.proposal_id,
                              session_id=state.session_id, fingerprint=proposal_fingerprint(p))
        with self._lock:
            self._tokens[token.token] = token
        p.status = ProposalStatus.APPROVED
        rec.note("approval_gate.approve", "User explicitly approved this exact proposal",
                 input={"proposal_id": p.proposal_id, "confirmed_items": req.confirmed_items},
                 output={"fingerprint": token.fingerprint})
        return token

    def reject(self, state: AgentState, req: ApprovalRequest, rec: TraceRecorder) -> ShoppingProposal:
        p = self._current(state, req.proposal_id)
        p.status = ProposalStatus.REJECTED
        rec.note("approval_gate.reject", req.reason or "User rejected the proposal",
                 input=req.model_dump(mode="json"))
        return p

    def check_editable(self, state: AgentState, req: ApprovalRequest) -> ShoppingProposal:
        """Validate an edit WITHOUT changing anything (so a bad edit leaves the proposal approvable)."""
        p = self._current(state, req.proposal_id)
        if req.changes is None or req.changes.is_empty():
            raise ApprovalError("NO_CHANGES", "edit needs at least one change", 400)
        return p

    def supersede(self, state: AgentState, req: ApprovalRequest, rec: TraceRecorder) -> ShoppingProposal:
        p = self.check_editable(state, req)
        p.status = ProposalStatus.SUPERSEDED
        rec.note("approval_gate.edit", "User edited the proposal – it will be re-planned",
                 input=req.model_dump(mode="json"))
        return p

    # ------------------------------------------------------------------ carts
    def _validate_token(self, state: AgentState, token: Optional[ApprovalToken]) -> ApprovalToken:
        if token is None:
            raise GuardrailViolation("NO_APPROVAL", "build_cart called without an approval token")
        with self._lock:
            stored = self._tokens.get(token.token)
        if stored is None or stored is not token:
            raise GuardrailViolation("FORGED_TOKEN", "approval token was not issued by the approval gate")
        if stored.used:
            raise GuardrailViolation("TOKEN_USED", "approval token already used")
        if (state.proposal is None or stored.session_id != state.session_id
                or stored.proposal_id != state.proposal.proposal_id
                or stored.fingerprint != proposal_fingerprint(state.proposal)
                or state.proposal.status != ProposalStatus.APPROVED):
            raise GuardrailViolation("TOKEN_MISMATCH", "approval token does not match the current approved proposal")
        return stored

    def build_cart(self, state: AgentState, platform: Platform, lines: list[CartLine],
                   token: Optional[ApprovalToken], adapters: dict[Platform, PlatformAdapter],
                   rec: TraceRecorder) -> PreparedCart:
        """The ONLY code path that may call adapter.build_cart."""
        try:
            self._validate_token(state, token)
        except GuardrailViolation as e:
            rec.note("build_cart", "Blocked: no valid approval", input={"platform": platform.value},
                     output={"error": str(e)}, status=ActionStatus.BLOCKED)
            raise
        adapter = adapters[platform]
        try:
            cart, _ = rec.call("build_cart", {"platform": platform.value, "cart": [l.model_dump() for l in lines]},
                               lambda: adapter.build_cart(lines),
                               f"Prepare the approved {platform.value} cart (no payment, no order)")
        except ToolCallError as e:
            raise ApprovalError("CART_FAILED", str(e), 502) from e
        if not isinstance(cart, PreparedCart):
            cart = PreparedCart.model_validate(cart)
        if cart.payment_executed or cart.order_placed:
            raise GuardrailViolation("PAYMENT_DETECTED", f"{platform.value} reported payment/order – aborting")
        return cart

    def prepare_carts(self, state: AgentState, token: ApprovalToken, adapters: dict[Platform, PlatformAdapter],
                      rec: TraceRecorder) -> list[PreparedCart]:
        self._validate_token(state, token)
        carts = []
        for c in state.proposal.carts:
            lines = [CartLine(product_id=i.listing.product_id, quantity=i.packs) for i in c.items]
            carts.append(self.build_cart(state, c.platform, lines, token, adapters, rec))
        with self._lock:
            token.used = True
        return carts
