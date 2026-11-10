"""Shared data contracts for CartPilot.

FROZEN AFTER HOUR 3. Members A, B and C all import from here. Additive changes
(new optional fields) are fine; renaming or removing a field needs a sync.

Every money value is in rupees (float, 2 dp). Every number that reaches a
proposal must come from a platform tool output or from deterministic
arithmetic over tool outputs (see core/guardrails.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_clock_time(value: Optional[str]) -> Optional[str]:
    """Accept '20:00', '8 pm', '8:30PM', '08.15 am', 'noon', 'midnight' -> 'HH:MM' (24h).

    A bare hour without AM/PM ('8') is ambiguous and rejected rather than guessed.
    """
    if value is None:
        return None
    v = " ".join(str(value).strip().lower().replace(".", ":").split())
    if v in ("noon", "12 noon"):
        return "12:00"
    if v == "midnight":
        return "23:59"
    import re

    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a:m:|p:m:)?", v)
    if not m:
        raise ValueError("deadline must look like '20:00' or '8 pm'")
    hh, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(":", "")
    if ap:
        if not 1 <= hh <= 12:
            raise ValueError("hour must be 1-12 with AM/PM")
        hh = (0 if hh == 12 else hh) + (12 if ap == "pm" else 0)
    elif m.group(2) is None and hh <= 12:
        raise ValueError(f"'{value}' is ambiguous – use 24h (e.g. 20:00) or add AM/PM")
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError("deadline must be a valid time of day")
    return f"{hh:02d}:{mm:02d}"


# --------------------------------------------------------------------------- #
# Enums
# --------------------------------------------------------------------------- #
class Platform(str, Enum):
    BLINKIT = "blinkit"
    ZEPTO = "zepto"
    INSTAMART = "instamart"


class DietTag(str, Enum):
    VEG = "veg"          # vegetarian, may contain dairy
    VEGAN = "vegan"      # no animal products
    EGG = "egg"          # contains egg
    NON_VEG = "non_veg"  # meat / fish


class DietPreference(str, Enum):
    VEGETARIAN = "vegetarian"
    VEGAN = "vegan"
    EGGETARIAN = "eggetarian"
    NON_VEGETARIAN = "non_vegetarian"


# Which product diet tags each user diet accepts.
DIET_ALLOWED_TAGS: dict[DietPreference, set[DietTag]] = {
    DietPreference.VEGAN: {DietTag.VEGAN},
    DietPreference.VEGETARIAN: {DietTag.VEG, DietTag.VEGAN},
    DietPreference.EGGETARIAN: {DietTag.VEG, DietTag.VEGAN, DietTag.EGG},
    DietPreference.NON_VEGETARIAN: {DietTag.VEG, DietTag.VEGAN, DietTag.EGG, DietTag.NON_VEG},
}


class Phase(str, Enum):
    PLAN = "PLAN"
    ACT = "ACT"
    OBSERVE = "OBSERVE"
    VERIFY = "VERIFY"
    PROPOSE = "PROPOSE"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    FINALIZE = "FINALIZE"
    DONE = "DONE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


class ActionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RETRIED = "retried"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ProposalStatus(str, Enum):
    PENDING_APPROVAL = "pending_approval"
    OVER_BUDGET = "over_budget"          # shown to the user, cannot be approved
    APPROVED = "approved"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"            # replaced by an edited version


class MatchBand(str, Enum):
    AUTO = "auto_accept"        # confidence >= 0.85
    CONFIRM = "confirm"         # 0.60 – 0.84: shown, user must confirm
    ASK = "ask_user"            # < 0.60: never used, user is asked


class WarningSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# --------------------------------------------------------------------------- #
# Request
# --------------------------------------------------------------------------- #
class ItemRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Generic item name, e.g. 'milk'")
    quantity: float = Field(1, gt=0)
    unit: str = Field("pcs", description="kg | g | litre | ml | pcs")
    brand: Optional[str] = Field(None, description="Brand the user asked for, if any")
    quantity_assumed: bool = Field(False, description="True if the user gave no quantity")

    @field_validator("name")
    @classmethod
    def _norm_name(cls, v: str) -> str:
        return " ".join(v.lower().split())


class ShoppingRequest(BaseModel):
    raw_text: str = ""
    items: list[ItemRequest] = Field(default_factory=list)
    household_size: Optional[int] = Field(None, ge=1, le=50)
    dietary_preference: Optional[DietPreference] = None
    budget: Optional[float] = Field(None, gt=0)
    deadline: Optional[str] = Field(None, description="HH:MM, 24h, local time")
    pincode: str = "560001"
    preferred_brands: list[str] = Field(default_factory=list)
    avoid_brands: list[str] = Field(default_factory=list)
    never_substitute: list[str] = Field(default_factory=list,
                                        description="Items that must not be substituted, e.g. 'amul milk' or 'milk'")
    preferred_platforms: list[Platform] = Field(default_factory=list)
    excluded_platforms: list[Platform] = Field(default_factory=list)
    items_source: str = Field("user", description="user | template | episode")
    constraint_sources: dict[str, str] = Field(default_factory=dict,
                                               description="Where each constraint came from: user | memory")

    @field_validator("deadline")
    @classmethod
    def _check_deadline(cls, v: Optional[str]) -> Optional[str]:
        return normalize_clock_time(v)

    def missing_constraints(self) -> list[str]:
        missing = []
        if self.budget is None:
            missing.append("budget")
        if self.deadline is None:
            missing.append("deadline")
        return missing


class IntentResult(BaseModel):
    request: ShoppingRequest
    parser: str = Field(..., description="llm | deterministic")
    clarification_question: Optional[str] = None
    missing: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Catalog / platform data (Member B produces these)
# --------------------------------------------------------------------------- #
class Product(BaseModel):
    sku_id: str
    name: str
    brand: str
    quantity_value: float = Field(..., gt=0)
    quantity_unit: str = Field(..., description="kg | g | litre | ml | pcs")
    category: str
    diet_tag: DietTag
    generic_name: Optional[str] = Field(None, description="Canonical item, e.g. 'milk'. Optional, helps matching")


class Listing(BaseModel):
    platform: Platform
    product_id: str = Field(..., description="== Product.sku_id")
    product: Product
    price: float = Field(..., ge=0)
    mrp: float = Field(..., ge=0)
    in_stock: bool
    eta_mins: Optional[int] = None
    source_action_id: Optional[str] = Field(None, description="Trace action that produced this listing")

    @property
    def key(self) -> str:
        return f"{self.platform.value}:{self.product_id}"


class CartLine(BaseModel):
    product_id: str
    quantity: int = Field(..., ge=1, description="Number of packs")


class CartItem(BaseModel):
    platform: Platform
    request_item: str = Field(..., description="ItemRequest.name this line fulfils")
    listing: Listing
    packs: int = Field(..., ge=1)
    line_total: float
    is_substitute: bool = False
    match_confidence: float = 1.0
    requires_confirmation: bool = False
    unit_price: Optional[float] = Field(None, description="Derived: price per kg / litre / piece")
    unit_label: Optional[str] = Field(None, description="kg | litre | pc")

    def to_cart_line(self) -> CartLine:
        return CartLine(product_id=self.listing.product_id, quantity=self.packs)


class FeeSchedule(BaseModel):
    handling_fee: float = 0
    delivery_fee: float = 0
    free_delivery_threshold: Optional[float] = None
    small_cart_fee: float = 0
    small_cart_threshold: Optional[float] = None
    surge_fee: float = 0


class PlatformFees(BaseModel):
    platform: Platform
    cart_value: float
    handling: float
    delivery: float
    small_cart: float
    surge: float
    total: float
    schedule: FeeSchedule
    source_action_id: Optional[str] = None


class Offer(BaseModel):
    offer_id: str
    platform: Platform
    description: str
    discount_type: str = Field(..., description="flat | percent")
    value: float
    min_cart_value: float = 0
    max_discount: Optional[float] = None
    requires_payment_method: Optional[str] = Field(None, description="e.g. bank card; never auto-applied")
    applicable: bool = False
    discount_amount: float = 0
    source_action_id: Optional[str] = None


class EtaQuote(BaseModel):
    platform: Platform
    eta_mins: int = Field(..., ge=0)
    deadline_met: Optional[bool] = None
    source_action_id: Optional[str] = None


class PreparedCart(BaseModel):
    cart_ref: str
    platform: Platform
    lines: list[CartLine]
    status: str = "prepared"
    payment_executed: bool = False   # always False – CartPilot never pays
    order_placed: bool = False       # always False – CartPilot never orders
    message: str = "Cart prepared. Review and pay inside the platform app."


# --------------------------------------------------------------------------- #
# Matching (Member B)
# --------------------------------------------------------------------------- #
class MatchCandidate(BaseModel):
    listing: Listing
    confidence: float = Field(..., ge=0, le=1)
    band: MatchBand
    is_substitute: bool = False
    reason: str = ""


class MatchedProduct(BaseModel):
    request_item: ItemRequest
    candidates: list[MatchCandidate] = Field(default_factory=list)
    best_confidence: float = 0
    band: MatchBand = MatchBand.ASK
    explanation: str = ""


# --------------------------------------------------------------------------- #
# Optimisation (Member C)
# --------------------------------------------------------------------------- #
class OptimizationConstraints(BaseModel):
    budget: Optional[float] = None
    minutes_to_deadline: Optional[int] = None
    platform_eta: dict[Platform, int] = Field(default_factory=dict)
    excluded_platforms: list[Platform] = Field(default_factory=list)
    preferred_platforms: list[Platform] = Field(default_factory=list)
    fee_schedules: dict[Platform, FeeSchedule] = Field(default_factory=dict)
    offers: dict[Platform, list[Offer]] = Field(default_factory=dict)


class PlatformBreakdown(BaseModel):
    platform: Platform
    item_total: float
    handling: float
    delivery: float
    small_cart: float
    surge: float
    offer_discount: float
    offer_id: Optional[str] = None
    landed_total: float
    eta_mins: Optional[int] = None
    items: list[CartItem] = Field(default_factory=list)


class OptimizationResult(BaseModel):
    assignments: list[CartItem] = Field(default_factory=list)
    per_platform: dict[Platform, PlatformBreakdown] = Field(default_factory=dict)
    total_landed: float = 0
    best_single_platform: Optional[Platform] = None
    best_single_total: Optional[float] = None
    best_single_items: list[CartItem] = Field(default_factory=list,
                                              description="Cheapest single-app basket, used as the savings baseline")
    savings_vs_single: Optional[float] = None
    feasible: bool = True
    within_budget: bool = True
    unassigned_items: list[str] = Field(default_factory=list)
    solver: str = "fallback"
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Audit, proposal, approval
# --------------------------------------------------------------------------- #
class AuditWarning(BaseModel):
    code: str = Field(..., description="OUT_OF_STOCK | PRICE_CHANGED | FEE_ANOMALY | ETA_MISS | "
                                       "UNIT_PRICE | SUSPICIOUS_DISCOUNT | OFFER_INVALID | OVER_BUDGET | "
                                       "LOW_CONFIDENCE | UNVERIFIED | DEADLINE_ROLLED | ITEM_UNAVAILABLE")
    severity: WarningSeverity = WarningSeverity.WARNING
    message: str
    platform: Optional[Platform] = None
    product_id: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)
    triggers_replan: bool = False


class AuditReport(BaseModel):
    warnings: list[AuditWarning] = Field(default_factory=list)
    verified_listings: dict[str, Listing] = Field(default_factory=dict)   # key platform:product_id
    verified_fees: dict[Platform, PlatformFees] = Field(default_factory=dict)
    verified_offers: dict[Platform, list[Offer]] = Field(default_factory=dict)
    verified_etas: dict[Platform, EtaQuote] = Field(default_factory=dict)
    baseline_fees: Optional[PlatformFees] = None
    baseline_offers: list[Offer] = Field(default_factory=list)
    unverified: list[str] = Field(default_factory=list)
    checked_at: datetime = Field(default_factory=utcnow)

    @property
    def needs_replan(self) -> bool:
        return any(w.triggers_replan for w in self.warnings)


class NumberProvenance(BaseModel):
    field: str
    value: float
    source: str = Field(..., description="tool:<action_id> | derived")


class ShoppingProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: new_id("prop"))
    session_id: str
    version: int = 1
    status: ProposalStatus = ProposalStatus.PENDING_APPROVAL
    request: ShoppingRequest
    carts: list[PlatformBreakdown] = Field(default_factory=list)
    total_landed: float = 0
    best_single_platform: Optional[Platform] = None
    best_single_total: Optional[float] = None
    savings_vs_single: Optional[float] = None
    savings_pct: Optional[float] = None
    budget: Optional[float] = None
    within_budget: bool = True
    deadline: Optional[str] = None
    deadline_met: Optional[bool] = None
    warnings: list[AuditWarning] = Field(default_factory=list)
    requires_confirmation: list[str] = Field(default_factory=list,
                                             description="product_ids the user must confirm (medium-confidence matches)")
    unresolved_items: list[dict[str, Any]] = Field(default_factory=list)
    provenance: list[NumberProvenance] = Field(default_factory=list)
    replan_count: int = 0
    summary: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    last_checked_at: Optional[datetime] = None

    @property
    def approvable(self) -> bool:
        return self.status == ProposalStatus.PENDING_APPROVAL and self.within_budget and bool(self.carts)


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"


class ProposalEdit(BaseModel):
    remove_items: list[str] = Field(default_factory=list)
    add_items: list[ItemRequest] = Field(default_factory=list)
    update_quantities: dict[str, float] = Field(default_factory=dict)
    budget: Optional[float] = Field(None, gt=0)
    deadline: Optional[str] = None
    reject_brands: list[str] = Field(default_factory=list)
    prefer_brands: list[str] = Field(default_factory=list)
    exclude_platforms: list[Platform] = Field(default_factory=list)
    never_substitute: list[str] = Field(default_factory=list)

    @field_validator("deadline")
    @classmethod
    def _check_deadline(cls, v: Optional[str]) -> Optional[str]:
        return normalize_clock_time(v)

    def is_empty(self) -> bool:
        return self == ProposalEdit()


class ApprovalRequest(BaseModel):
    proposal_id: str
    decision: ApprovalDecision
    confirmed_items: list[str] = Field(default_factory=list,
                                       description="product_ids the user confirms (medium-confidence matches)")
    reason: Optional[str] = None
    rejected_brands: list[str] = Field(default_factory=list)
    changes: Optional[ProposalEdit] = None
    actor: str = Field("user", description="Only 'user' (via the API) may approve")
    remind_in_days: Optional[int] = Field(None, ge=1, le=60,
                                          description="Approve only: also set a reorder reminder in N days")


# --------------------------------------------------------------------------- #
# Trace and state
# --------------------------------------------------------------------------- #
class AgentAction(BaseModel):
    action_id: str = Field(default_factory=lambda: new_id("act"))
    phase: Phase
    tool: str
    input: dict[str, Any] = Field(default_factory=dict)
    output: Any = None
    reason: str = ""
    status: ActionStatus = ActionStatus.SUCCESS
    timestamp: datetime = Field(default_factory=utcnow)
    duration_ms: float = 0
    attempt: int = 1
    error: Optional[str] = None


class ReplanEvent(BaseModel):
    iteration: int
    triggers: list[str]
    action_taken: str
    timestamp: datetime = Field(default_factory=utcnow)


class AgentState(BaseModel):
    session_id: str = Field(default_factory=lambda: new_id("sess"))
    user_id: str = "demo-user"
    phase: Phase = Phase.PLAN
    request: Optional[ShoppingRequest] = None
    plan_steps: list[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None
    listings: list[Listing] = Field(default_factory=list)
    matches: list[MatchedProduct] = Field(default_factory=list)
    optimization: Optional[OptimizationResult] = None
    audit: Optional[AuditReport] = None
    proposal: Optional[ShoppingProposal] = None
    proposal_history: list[str] = Field(default_factory=list)
    prepared_carts: list[PreparedCart] = Field(default_factory=list)
    trace: list[AgentAction] = Field(default_factory=list)
    replans: list[ReplanEvent] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    memory_notes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
