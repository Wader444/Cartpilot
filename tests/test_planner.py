import time

from app.adapters.base import PlatformTimeout
from app.adapters.mock_adapter import MockPlatformAdapter
from app.core.guardrails import verify_proposal_numbers
from app.core.models import ActionStatus, Phase, Platform, ProposalStatus

from tests.conftest import BASIC, WEEKLY, add_fault, awaiting, chosen, make_orchestrator


def test_normal_request_full_loop(orch):  # scenario 1
    s = orch.start(WEEKLY)
    p = awaiting(s)
    assert p.status == ProposalStatus.PENDING_APPROVAL and p.within_budget and p.deadline_met
    assert p.total_landed <= 1200 and len(p.carts) >= 1
    assert p.best_single_total is not None and p.savings_vs_single >= 0
    phases = {a.phase for a in s.trace}
    assert {Phase.PLAN, Phase.ACT, Phase.OBSERVE, Phase.VERIFY, Phase.PROPOSE, Phase.AWAITING_APPROVAL} <= phases
    tools = {a.tool for a in s.trace}
    assert {"search_products", "get_fees", "get_offers", "get_eta", "match_products", "optimize_cart"} <= tools
    for a in s.trace:  # every action carries the required trace fields
        assert a.action_id and a.tool and a.timestamp and a.status and a.reason is not None
    assert s.prepared_carts == [] and not any(a.tool == "build_cart" for a in s.trace)
    assert verify_proposal_numbers(p, s.trace) == []


def test_split_never_loses_to_single_app(orch):
    p = awaiting(orch.start(WEEKLY))
    assert p.total_landed <= p.best_single_total + 0.01


def test_out_of_stock_during_verification_replans(orch, fresh_store):  # scenario 4
    platform, sku = chosen(awaiting(orch.start(BASIC)), "milk")
    from app.adapters.mock_store import reset_store
    store = reset_store()
    add_fault(store, "out_of_stock", platform, sku, after=1)  # in stock at planning, gone at verification
    o2 = make_orchestrator()
    s = o2.start(BASIC)
    p = awaiting(s)
    assert any("OUT_OF_STOCK" in r.triggers for r in s.replans)
    assert chosen(s, "milk") != (platform, sku)
    assert any(w.code == "OUT_OF_STOCK" for w in p.warnings)
    assert verify_proposal_numbers(p, s.trace) == []


def test_platform_timeout_is_retried_then_dropped(orch, fresh_store):  # scenario 5
    add_fault(fresh_store, "timeout", "blinkit")
    s = orch.start(BASIC)
    p = awaiting(s)
    assert all(c.platform != Platform.BLINKIT for c in p.carts)
    retried = [a for a in s.trace if a.status == ActionStatus.RETRIED and a.input.get("platform") == "blinkit"]
    failed = [a for a in s.trace if a.status == ActionStatus.FAILED and a.input.get("platform") == "blinkit"]
    assert len(retried) == 2 and len(failed) == 1, "2 retries then give up"
    assert any("PLATFORM_FAILURE" in r.triggers for r in s.replans)


def test_hanging_platform_hits_hard_timeout(fresh_store):
    class Slow(MockPlatformAdapter):
        def search_products(self, query):
            time.sleep(1.0)
            return super().search_products(query)

    o = make_orchestrator(tool_timeout_s=0.2, tool_max_retries=1)
    o.adapters[Platform.ZEPTO] = Slow(Platform.ZEPTO)
    s = o.start(BASIC)
    awaiting(s)
    assert any(a.error and "timeout" in a.error for a in s.trace)
    assert all(c.platform != Platform.ZEPTO for c in s.proposal.carts)


def test_all_platforms_down_fails_clearly(orch, fresh_store):
    for p in Platform:
        add_fault(fresh_store, "timeout", p)
    s = orch.start(BASIC)
    assert s.phase == Phase.FAILED and s.proposal is None
    assert "No platform is reachable" in s.errors[0]


def test_price_change_during_verification(orch):  # scenario 6
    platform, sku = chosen(awaiting(orch.start(BASIC)), "atta")
    old = next(i.listing.price for c in orch.sessions._cache.values() for cc in c.proposal.carts
               for i in cc.items if i.listing.product_id == sku and cc.platform == platform)
    from app.adapters.mock_store import reset_store
    store = reset_store()
    add_fault(store, "price_change", platform, sku, after=1, value=old + 40)
    s = make_orchestrator().start(BASIC)
    p = awaiting(s)
    assert any("PRICE_CHANGED" in r.triggers for r in s.replans)
    now = chosen(s, "atta")
    if now == (platform, sku):  # still cheapest: must carry the NEW tool price
        price = next(i.listing.price for c in p.carts for i in c.items if i.listing.product_id == sku)
        assert price == old + 40
    assert verify_proposal_numbers(p, s.trace) == []


def test_fee_anomaly_is_flagged(orch):  # scenario 7
    first = awaiting(orch.start(BASIC))
    target = first.carts[0].platform
    from app.adapters.mock_store import reset_store
    store = reset_store()
    add_fault(store, "fee_spike", target, after=1, value=45)  # normal at planning, +₹45 surge at verification
    s = make_orchestrator().start(BASIC)
    p = awaiting(s)
    flagged = [w for w in p.warnings if w.code == "FEE_ANOMALY"]
    assert flagged and flagged[0].platform == target
    assert any("FEE_ANOMALY" in r.triggers for r in s.replans)
    assert verify_proposal_numbers(p, s.trace) == []
    for c in p.carts:  # whatever was chosen, the surge shown is exactly what the tool quoted
        if c.platform == target:
            assert c.surge == 45


def test_budget_exceeded_is_not_approvable(orch):  # scenario 8
    s = orch.start("Weekly groceries for 2 people, vegetarian, under ₹300, delivered before 8 PM.")
    p = awaiting(s)
    assert p.status == ProposalStatus.OVER_BUDGET and not p.within_budget and not p.approvable
    assert any(w.code == "OVER_BUDGET" for w in p.warnings)


def test_deadline_excludes_slow_platform(orch, fresh_store):  # scenario 9
    add_fault(fresh_store, "eta", "instamart", value=95)
    s = orch.start("2 kg atta, 1 kg rice and 1 kg toor dal, budget 1000, within 45 minutes")
    p = awaiting(s)
    assert all(c.platform != Platform.INSTAMART for c in p.carts)
    assert any("ETA" in n for n in s.optimization.notes)
    assert p.deadline_met


def test_eta_miss_during_verification_replans(orch):
    first = awaiting(orch.start(BASIC))
    target = first.carts[0].platform
    from app.adapters.mock_store import reset_store
    store = reset_store()
    add_fault(store, "eta", target, after=1, value=400)
    s = make_orchestrator().start(BASIC)
    p = awaiting(s)
    assert any("ETA_MISS" in r.triggers for r in s.replans)
    assert all(c.platform != target for c in p.carts)


def test_low_confidence_substitute_needs_confirmation(orch, fresh_store):  # scenario 10
    for plat in Platform:
        for sku in ("paneer-amul-200g", "paneer-milkymist-200g"):
            add_fault(fresh_store, "out_of_stock", plat, sku)
    s = orch.start("200 g paneer and 1 litre milk, vegetarian, budget 500, before 9 pm")
    p = awaiting(s)
    sub = [i for c in p.carts for i in c.items if i.request_item == "paneer"]
    assert sub and sub[0].listing.product.generic_name == "tofu" and sub[0].is_substitute
    assert sub[0].listing.product_id in p.requires_confirmation
    assert any(w.code == "LOW_CONFIDENCE" for w in p.warnings)
    assert any("UNAVAILABLE" in r.triggers for r in s.replans)


def test_unknown_item_is_asked_not_guessed(orch):
    p = awaiting(orch.start("1 kg unicorn dust and 1 litre milk, budget 500, before 9 pm"))
    assert any(u["item"] == "unicorn dust" for u in p.unresolved_items)
    assert all(i.request_item != "unicorn dust" for c in p.carts for i in c.items)


def test_invalid_platform_response_is_handled(orch, fresh_store):
    add_fault(fresh_store, "invalid_response", "zepto")
    s = orch.start(BASIC)
    awaiting(s)
    assert all(c.platform != Platform.ZEPTO for c in s.proposal.carts)
    assert any(a.error and "InvalidPlatformResponse" in a.error for a in s.trace)


def test_empty_results_on_one_platform(orch, fresh_store):
    add_fault(fresh_store, "empty_results", "blinkit")
    p = awaiting(orch.start(BASIC))
    assert all(c.platform != Platform.BLINKIT for c in p.carts)


def test_template_basket_scales_and_is_editable(orch):
    s = orch.start("Weekly groceries for 4 people, vegetarian, under ₹3,000, before 8 PM")
    awaiting(s)
    milk = next(i for i in s.request.items if i.name == "milk")
    assert s.request.items_source == "template" and milk.quantity == 6


def test_deadline_already_passed_rolls_to_tomorrow(orch):
    p = awaiting(orch.start("1 litre milk, budget 200, before 9 am"))
    assert any(w.code == "DEADLINE_ROLLED" for w in p.warnings)
