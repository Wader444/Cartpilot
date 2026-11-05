"""Regression tests for the review findings + the added Member A features."""
import threading
from datetime import timedelta

import pytest
from pydantic import ValidationError

from app.core.guardrails import verify_proposal_numbers
from app.core.models import (
    ApprovalDecision,
    ApprovalRequest,
    ItemRequest,
    Phase,
    Platform,
    ProposalEdit,
    ProposalStatus,
)
from app.tools.approval_tools import ApprovalError

from tests.conftest import BASIC, WEEKLY, add_fault, awaiting, chosen, make_orchestrator


def approve(pid, **kw):
    return ApprovalRequest(proposal_id=pid, decision=ApprovalDecision.APPROVE, **kw)


def edit(pid, **changes):
    return ApprovalRequest(proposal_id=pid, decision=ApprovalDecision.EDIT, changes=ProposalEdit(**changes))


# ---------------------------------------------------------------- bug 1: re-plan budget
def test_substitution_events_do_not_use_up_verification_replans(fresh_store):
    text = "200 g paneer, 1 kg unicorn dust, 1 kg dragonfruit, 1 litre milk, budget 600, before 9 pm"
    for sku in ("paneer-amul-200g", "paneer-milkymist-200g"):
        for p in Platform:
            add_fault(fresh_store, "out_of_stock", p, sku)
    platform, sku = chosen(awaiting(make_orchestrator().start(text)), "milk")[:2]
    from app.adapters.mock_store import reset_store
    store = reset_store()
    for x in ("paneer-amul-200g", "paneer-milkymist-200g"):
        for p in Platform:
            add_fault(store, "out_of_stock", p, x)
    add_fault(store, "out_of_stock", platform, sku, after=1)
    s = make_orchestrator().start(text)
    p = awaiting(s)  # previously FAILED: "Could not verify ... after 3 re-plan(s)"
    assert any("OUT_OF_STOCK" in r.triggers for r in s.replans)
    assert verify_proposal_numbers(p, s.trace) == []


def test_replan_count_is_per_proposal_version(orch, fresh_store):
    add_fault(fresh_store, "timeout", "blinkit")
    s = orch.start(BASIC)
    p1 = awaiting(s)
    before = len(s.replans)
    assert p1.replan_count == before >= 1
    s, _ = orch.edit_proposal(s.session_id, edit(p1.proposal_id, budget=1500))
    p2 = awaiting(s)
    assert p2.replan_count == len(s.replans) - before, "v2 must count only its own re-plans"


# ---------------------------------------------------------------- bug 2: bad edits
def test_bad_deadline_rejected_before_anything_changes(orch):
    with pytest.raises(ValidationError):
        ProposalEdit(deadline="8")  # ambiguous
    with pytest.raises(ValidationError):
        ProposalEdit(deadline="25:00")
    assert ProposalEdit(deadline="8 pm").deadline == "20:00"


def test_invalid_edit_leaves_proposal_approvable(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    for bad in (dict(remove_items=["caviar"]), dict(update_quantities={"milk": 0}),
                dict(remove_items=["atta", "milk", "rice"]), dict()):
        with pytest.raises(ApprovalError):
            orch.edit_proposal(s.session_id, edit(p.proposal_id, **bad))
    assert p.status == ProposalStatus.PENDING_APPROVAL and s.phase == Phase.AWAITING_APPROVAL
    assert orch.episodes.list(s.user_id)[0]["decision"] == "pending"
    s, _ = orch.approve_proposal(s.session_id, approve(p.proposal_id))
    assert s.phase == Phase.DONE


def test_edit_with_valid_deadline(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    s, _ = orch.edit_proposal(s.session_id, edit(p.proposal_id, deadline="9:30 pm"))
    assert s.request.deadline == "21:30" and awaiting(s).deadline == "21:30"


# ---------------------------------------------------------------- concurrency
def test_double_approve_click_prepares_carts_once(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    results, errors = [], []

    def click():
        try:
            results.append(orch.approve_proposal(s.session_id, approve(p.proposal_id)))
        except ApprovalError as e:
            errors.append(e.code)

    threads = [threading.Thread(target=click) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1 and len(errors) == 4
    assert sum(a.tool == "build_cart" and a.status.value == "success" for a in s.trace) == len(p.carts)


# ---------------------------------------------------------------- teammate module failure
def test_broken_teammate_matching_falls_back_visibly(fresh_store, tmp_path, monkeypatch):
    mod = tmp_path / "broken_matching.py"
    mod.write_text("def match_products(listings):\n    return []\n\ndef find_substitutes(item, c):\n    return []\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    o = make_orchestrator(matching_module="broken_matching")
    s = o.start(BASIC)
    p = awaiting(s)
    assert p.carts, "fallback matcher should still source the basket"
    assert any(a.tool == "match_products.fallback" for a in s.trace)


# ---------------------------------------------------------------- preferences are soft
def _atta_line(o):
    p = awaiting(o.start(BASIC))
    return next(i for c in p.carts for i in c.items if i.request_item == "atta")


def test_accepted_brand_is_soft_preference(fresh_store):
    baseline = _atta_line(make_orchestrator())
    o = make_orchestrator()
    o.prefs.set("demo-user", "accepted_brand", "Aashirvaad")   # pricier per kg for a 2 kg request
    pick = _atta_line(o)
    assert pick.line_total <= baseline.line_total * 1.10 + 0.01, "a remembered brand may cost at most 10% more"


def test_preferred_platform_breaks_ties():
    from app.core.models import (DietTag, FeeSchedule, ItemRequest, Listing, MatchBand, MatchCandidate,
                                 MatchedProduct, OptimizationConstraints, Product)
    from app.tools.optimizer_tools import fallback_optimize_cart
    prod = Product(sku_id="x", name="X 1 kg", brand="B", quantity_value=1, quantity_unit="kg", category="c",
                   diet_tag=DietTag.VEG, generic_name="x")
    cands = [MatchCandidate(listing=Listing(platform=p, product_id="x", product=prod, price=100, mrp=100,
                                            in_stock=True), confidence=0.95, band=MatchBand.AUTO)
             for p in (Platform.BLINKIT, Platform.ZEPTO)]
    m = MatchedProduct(request_item=ItemRequest(name="x", quantity=1, unit="kg"), candidates=cands)
    sched = {p: FeeSchedule(handling_fee=5) for p in Platform}
    for pref in (Platform.BLINKIT, Platform.ZEPTO):
        r = fallback_optimize_cart([m], OptimizationConstraints(fee_schedules=sched, preferred_platforms=[pref]))
        assert list(r.per_platform) == [pref]


# ---------------------------------------------------------------- reminders, pantry, price memory
def test_approve_with_reminder_and_pantry(orch):
    s = orch.start(WEEKLY)
    p = awaiting(s)
    s, _ = orch.approve_proposal(s.session_id, approve(p.proposal_id, remind_in_days=7))
    rems = orch.reminders.list("demo-user")
    assert len(rems) == 1 and rems[0].kind == "reorder" and "reorder" in rems[0].text.lower()
    assert any(a.tool == "set_reminder" for a in s.trace)
    items = {r["item"] for r in orch.pantry.items("demo-user")}
    assert {"milk", "atta"} <= items
    assert orch.pantry.due_for_refill("demo-user") == []
    later = orch.clock.now() + timedelta(days=8)
    assert {d["item"] for d in orch.pantry.due_for_refill("demo-user", later)} >= {"milk"}


def test_reminder_validation(orch):
    from app.tools.reminder_tools import ReminderError
    now = orch.clock.now()
    with pytest.raises(ReminderError):
        orch.reminders.set_reminder("u", now - timedelta(hours=1), "past", now)
    with pytest.raises(ReminderError):
        orch.reminders.set_reminder("u", now + timedelta(hours=1), "   ", now)


def test_reject_sets_no_reminder_and_no_pantry(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    orch.reject_proposal(s.session_id, ApprovalRequest(proposal_id=p.proposal_id, decision=ApprovalDecision.REJECT))
    assert orch.reminders.list("demo-user") == [] and orch.pantry.items("demo-user") == []


def test_price_memory_records_and_excludes_current_session(orch):
    s = orch.start(BASIC)
    awaiting(s)
    plat, sku = chosen(s, "milk")[:2]
    assert orch.prices.history(plat, sku, exclude_session=s.session_id) == []
    assert orch.prices.recent_average(plat, sku) is not None
    s2 = orch.start(BASIC)
    awaiting(s2)
    assert len(orch.prices.history(plat, sku, exclude_session=s2.session_id)) == 1


def test_unit_price_on_items_is_derived(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    for c in p.carts:
        for i in c.items:
            assert i.unit_price is not None and i.unit_label in ("kg", "litre", "pc")
    p2 = p.model_copy(deep=True)
    p2.carts[0].items[0].unit_price = 1.0
    assert any("unit_price" in v for v in verify_proposal_numbers(p2, s.trace))


# ---------------------------------------------------------------- edge cases from the plan
def test_everything_out_of_stock_everywhere(orch, fresh_store):
    for sku in ("milk-amul-1l", "milk-amul-500ml", "milk-md-1l", "milk-nandini-500ml"):
        for p in Platform:
            add_fault(fresh_store, "out_of_stock", p, sku)
    s = orch.start("1 litre milk, never substitute milk, budget 200, before 9 pm")
    p = awaiting(s)
    assert p.carts == [] and not p.approvable and p.unresolved_items
    with pytest.raises(ApprovalError) as e:
        orch.approve_proposal(s.session_id, approve(p.proposal_id))
    assert e.value.code == "EMPTY_PROPOSAL"


def test_singular_household_phrasing(orch):
    s = orch.start("Weekly groceries for 1 person, vegan, under ₹900, before 8 PM")
    assert s.request.household_size == 1 and s.request.items_source == "template"
    assert all("grocer" not in i.name for i in s.request.items)


def test_evaluation_runner_smoke(tmp_path):
    import json
    import scripts.evaluate as ev  # noqa: F401
    data = json.load(open("tests/baskets/baskets.json"))
    assert len(data["baskets"]) == 20
    small = tmp_path / "b.json"
    small.write_text(json.dumps({"baskets": data["baskets"][:2] + [data["baskets"][12]]}))
    out = ev.run(str(small), None)
    s = out["summary"]
    assert s["stopped_at_approval_gate_pct"] == 100.0 and s["invented_numbers_found"] == 0
    assert s["replan_success_pct"] == 100.0


def test_tonight_and_morning_disambiguate_time(orch):
    s = orch.start("get 2kg onions under 200 deliver by 9 tonight")
    assert s.request.deadline == "21:00" and [i.name for i in s.request.items] == ["onions"]
    s = orch.start("bread under 100 by 8 this morning")
    assert s.request.deadline == "08:00"


def test_fault_injection_off_by_default(monkeypatch):
    monkeypatch.delenv("CARTPILOT_ENABLE_FAULTS", raising=False)
    from app.core.config import Settings
    assert Settings().enable_fault_injection is False


def test_removing_every_item_does_not_invent_a_basket(orch):
    s = orch.start("1 litre milk, budget 200, before 9 pm")
    p = awaiting(s)
    with pytest.raises(ApprovalError) as e:
        orch.edit_proposal(s.session_id, edit(p.proposal_id, remove_items=["milk"]))
    assert e.value.code == "EMPTY_BASKET"
    assert [i.name for i in s.request.items] == ["milk"] and s.proposal.proposal_id == p.proposal_id
