"""Request/response bodies for the REST API (shared models live in core/models.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.core.models import (
    AgentAction,
    Platform,
    PreparedCart,
    ProposalEdit,
    ReplanEvent,
    ShoppingProposal,
    ShoppingRequest,
)


class PlanRequest(BaseModel):
    text: str = Field(..., min_length=3, max_length=2000,
                      examples=["Weekly groceries for 2 people, vegetarian, under ₹1,200, delivered before 8 PM."])
    user_id: str = Field("demo-user", min_length=1, max_length=64)
    pincode: Optional[str] = Field(None, pattern=r"^\d{6}$")


class ClarifyRequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=500, examples=["Budget 1200, by 8 pm"])


class ApproveBody(BaseModel):
    proposal_id: str
    confirmed_items: list[str] = Field(default_factory=list,
                                       description="product_ids listed in proposal.requires_confirmation")
    remind_in_days: Optional[int] = Field(None, ge=1, le=60, description="Also set a reorder reminder in N days")


class RejectBody(BaseModel):
    proposal_id: str
    reason: Optional[str] = None
    rejected_brands: list[str] = Field(default_factory=list)


class EditBody(BaseModel):
    proposal_id: str
    changes: ProposalEdit


class SessionView(BaseModel):
    session_id: str
    user_id: str
    phase: str
    clarification_question: Optional[str] = None
    request: Optional[ShoppingRequest] = None
    plan_steps: list[str] = Field(default_factory=list)
    proposal_id: Optional[str] = None
    proposal_summary: Optional[str] = None
    proposal_status: Optional[str] = None
    prepared_carts: list[PreparedCart] = Field(default_factory=list)
    replans: list[ReplanEvent] = Field(default_factory=list)
    memory_notes: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    trace_length: int = 0
    next_actions: list[str] = Field(default_factory=list)


class DecisionResponse(BaseModel):
    session: SessionView
    proposal: Optional[ShoppingProposal] = None
    preference_changes: list[dict[str, Any]] = Field(default_factory=list)
    message: str


class TraceResponse(BaseModel):
    session_id: str
    phase: str
    actions: list[AgentAction]
    replans: list[ReplanEvent]


class ReminderBody(BaseModel):
    user_id: str = Field("demo-user", min_length=1, max_length=64)
    text: str = Field(..., min_length=1, max_length=300)
    time: Optional[datetime] = Field(None, description="ISO time; or use in_days")
    in_days: Optional[float] = Field(None, gt=0, le=60)
    kind: str = Field("custom", pattern="^(reorder|price_watch|custom)$")


class FaultBody(BaseModel):
    kind: str = Field(..., pattern="^(out_of_stock|price_change|fee_spike|eta|timeout|invalid_response|empty_results)$")
    platform: Platform
    sku_id: Optional[str] = None
    after_calls: int = Field(0, ge=0, description="1 = fault starts during verification (after the first search)")
    value: Optional[float] = None
