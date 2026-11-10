"""Member B's matcher wired into the agent through app/agents/matching_agent.py."""
from app.core.guardrails import verify_proposal_numbers
from app.core.models import Phase

from tests.conftest import BASIC, WEEKLY, make_orchestrator


def test_agent_uses_member_b_matcher():
    o = make_orchestrator(matching_module="app.agents.matching_agent")
    s = o.start(WEEKLY)
    assert s.phase == Phase.AWAITING_APPROVAL and s.proposal.carts
    assert any("Member B matcher" in (m.explanation or "") for m in s.matches)
    assert not any(a.tool == "match_products.fallback" for a in s.trace), "bridge must not error"
    assert verify_proposal_numbers(s.proposal, s.trace) == []


def test_bridge_gives_same_plan_as_rules_alone():
    a = make_orchestrator().start(BASIC).proposal
    b = make_orchestrator(matching_module="app.agents.matching_agent").start(BASIC).proposal
    assert a.total_landed == b.total_landed and [c.platform for c in a.carts] == [c.platform for c in b.carts]
