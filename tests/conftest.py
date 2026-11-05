import pytest

from app.adapters.mock_store import Fault, reset_store
from app.core.config import Clock, reset_settings
from app.core.models import Phase, Platform
from app.memory.database import Database
from app.planner.orchestrator import CartPilotOrchestrator

WEEKLY = "Weekly groceries for 2 people, vegetarian, under ₹1,200, delivered before 8 PM."
BASIC = "Buy 2 kg atta, 1 litre milk and 1 kg rice. Vegetarian. Budget ₹1,200. Deliver before 8 PM."


def make_orchestrator(db_url: str = "sqlite://", **overrides) -> CartPilotOrchestrator:
    cfg = dict(database_url=db_url, tool_timeout_s=2.0, tool_max_retries=2,
               matching_module="tests._no_such_matching", optimizer_module="tests._no_such_optimizer",
               enable_fault_injection=True)
    cfg.update(overrides)
    settings = reset_settings(**cfg)
    return CartPilotOrchestrator(settings=settings, db=Database(db_url), clock=Clock(fixed_now="17:00"))


@pytest.fixture(autouse=True)
def fresh_store():
    store = reset_store()
    yield store
    store.clear_faults()


@pytest.fixture
def orch():
    return make_orchestrator()


def chosen(state, item: str):
    """(platform, product_id) chosen for an item in a proposal (accepts a state or a proposal)."""
    proposal = getattr(state, "proposal", None) or state
    for c in proposal.carts:
        for i in c.items:
            if i.request_item == item:
                return c.platform, i.listing.product_id
    return None


def add_fault(store, kind, platform, sku=None, after=0, value=None):
    store.add_fault(Fault(kind=kind, platform=Platform(platform) if isinstance(platform, str) else platform,
                          sku_id=sku, after_calls=after, value=value))


def awaiting(state):
    assert state.phase == Phase.AWAITING_APPROVAL, (state.phase, state.errors)
    assert state.proposal is not None
    return state.proposal
