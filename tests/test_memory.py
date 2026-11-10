import pytest

from app.core.models import ApprovalDecision, ApprovalRequest, DietPreference, Phase, ProposalEdit
from app.memory.database import HAVE_SQLALCHEMY, Database
from app.memory.preference_memory import PreferenceMemory

from tests.conftest import BASIC, WEEKLY, awaiting, make_orchestrator

BACKENDS = [True] + ([False] if HAVE_SQLALCHEMY else [])  # force_stdlib


@pytest.mark.parametrize("force_stdlib", BACKENDS)
def test_preferences_never_overwritten_silently(force_stdlib):
    pm = PreferenceMemory(Database("sqlite://", force_stdlib=force_stdlib))
    assert pm.set("u", "accepted_brand", "Amul").action == "added"
    assert pm.set("u", "accepted_brand", "amul").action == "unchanged"
    ch = pm.set("u", "rejected_brand", "Amul", reason="user rejected")
    assert ch.action == "superseded" and ch.previous == "accepted_brand:Amul"
    prefs = pm.get("u")
    assert prefs.rejected_brands == ["Amul"] and prefs.accepted_brands == []
    hist = [(h["kind"], h["change"]) for h in pm.history("u")]
    assert ("accepted_brand", "superseded") in hist  # the change is recorded, not silent
    ch = pm.set("u", "diet", "vegetarian")
    ch2 = pm.set("u", "diet", "vegan")
    assert ch2.action == "superseded" and ch2.previous == "vegetarian" and pm.get("u").diet == DietPreference.VEGAN


def test_rejected_brand_is_avoided_next_session(tmp_path):  # scenario 13
    url = f"sqlite:///{tmp_path}/mem.db"
    o1 = make_orchestrator(url)
    s1 = o1.start(BASIC)
    p1 = awaiting(s1)
    brand = next(i.listing.product.brand for c in p1.carts for i in c.items if i.request_item == "milk")
    o1.reject_proposal(s1.session_id, ApprovalRequest(proposal_id=p1.proposal_id, decision=ApprovalDecision.REJECT,
                                                      rejected_brands=[brand]))
    o2 = make_orchestrator(url)  # new process, same database
    s2 = o2.start(BASIC)
    p2 = awaiting(s2)
    assert all(i.listing.product.brand != brand for c in p2.carts for i in c.items)
    assert brand in s2.request.avoid_brands
    assert any(f"Avoiding {brand}" in n for n in s2.memory_notes)


def test_request_overrides_memory_without_erasing_it(orch):
    orch.prefs.set("demo-user", "rejected_brand", "Amul")
    s = orch.start("1 litre milk, prefer Amul, budget 200, before 9 pm")
    awaiting(s)
    assert "Amul" not in s.request.avoid_brands
    assert orch.prefs.get("demo-user").rejected_brands == ["Amul"]


def test_diet_from_memory_and_learned_on_approval(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    orch.approve_proposal(s.session_id, ApprovalRequest(proposal_id=p.proposal_id, decision=ApprovalDecision.APPROVE))
    assert orch.prefs.get("demo-user").diet == DietPreference.VEGETARIAN
    s2 = orch.start("200 g chicken and 1 litre milk, budget 600, before 9 pm")
    p2 = awaiting(s2)
    assert s2.request.dietary_preference == DietPreference.VEGETARIAN
    assert all(i.listing.product.diet_tag.value != "non_veg" for c in p2.carts for i in c.items)


def test_edit_updates_preferences(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    s, changes = orch.edit_proposal(s.session_id, ApprovalRequest(
        proposal_id=p.proposal_id, decision=ApprovalDecision.EDIT,
        changes=ProposalEdit(reject_brands=["Pillsbury"], never_substitute=["atta"])))
    assert {(c.kind, c.value) for c in changes} == {("rejected_brand", "Pillsbury"), ("never_substitute", "atta")}
    assert all(i.listing.product.brand != "Pillsbury" for c in s.proposal.carts for i in c.items)


def test_episodes_are_recorded_and_retrievable(orch):
    s = orch.start(WEEKLY)
    p = awaiting(s)
    orch.approve_proposal(s.session_id, ApprovalRequest(proposal_id=p.proposal_id, decision=ApprovalDecision.APPROVE))
    eps = orch.episodes.list("demo-user")
    e = eps[0]
    assert e["proposal_id"] == p.proposal_id and e["decision"] == "approved" and e["decided_at"]
    assert e["request"]["budget"] == 1200 and e["total_landed"] == p.total_landed
    assert e["estimated_savings"] == p.savings_vs_single
    assert set(e["platform_allocation"]) == {c.platform.value for c in p.carts}
    assert len(e["selected_products"]) == sum(len(c.items) for c in p.carts)


def test_next_week_reuses_last_approved_basket(orch):
    s = orch.start("2 kg atta, 1 litre milk, 1 kg sugar. Vegetarian. Budget 600. Before 8 PM.")
    p = awaiting(s)
    orch.approve_proposal(s.session_id, ApprovalRequest(proposal_id=p.proposal_id, decision=ApprovalDecision.APPROVE))
    s2 = orch.start("Weekly groceries, under ₹600, before 8 PM")
    awaiting(s2)
    assert s2.request.items_source == "episode"
    assert [i.name for i in s2.request.items] == ["atta", "milk", "sugar"]


def test_preference_and_episode_memory_are_separate(orch):
    s = orch.start(BASIC)
    awaiting(s)
    tables = {r["name"] for r in orch.db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"preferences", "preference_history", "episodes", "sessions"} <= tables
    assert orch.prefs.get("demo-user").accepted_brands == []  # a pending proposal teaches nothing


def test_sessions_survive_restart(tmp_path):
    url = f"sqlite:///{tmp_path}/s.db"
    s = make_orchestrator(url).start(BASIC)
    again = make_orchestrator(url).get(s.session_id)
    assert again.phase == Phase.AWAITING_APPROVAL and again.proposal.proposal_id == s.proposal.proposal_id
    assert len(again.trace) == len(s.trace)
