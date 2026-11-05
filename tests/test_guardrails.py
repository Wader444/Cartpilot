import pytest

from app.adapters.base import InvalidPlatformResponse
from app.adapters.http_adapter import HttpPlatformAdapter
from app.adapters.mock_adapter import MockPlatformAdapter
from app.api.service import ShoppingService
from app.core.guardrails import (
    GuardrailViolation,
    assert_no_invented_numbers,
    assert_no_payment_capability,
    enforce_budget,
    forbidden_capabilities,
    verify_proposal_numbers,
)
from app.core.models import ApprovalDecision, ApprovalRequest, Platform, PreparedCart
from app.tools.approval_tools import ApprovalGate

from tests.conftest import BASIC, WEEKLY, add_fault, awaiting


# ---------------------------------------------------------------- zero invented numbers
def test_every_proposal_number_traces_to_a_tool(orch):
    s = orch.start(WEEKLY)
    p = awaiting(s)
    assert verify_proposal_numbers(p, s.trace) == []
    assert all(x.source.startswith("tool:") or x.source == "derived" for x in p.provenance)
    tool_ids = {a.action_id for a in s.trace}
    assert all(x.source[5:] in tool_ids for x in p.provenance if x.source.startswith("tool:"))


@pytest.mark.parametrize("tamper", ["price", "fee", "discount", "total", "baseline", "eta"])
def test_invented_numbers_are_caught(orch, tamper):
    s = orch.start(WEEKLY)
    p = awaiting(s).model_copy(deep=True)
    c = p.carts[0]
    if tamper == "price":
        c.items[0].listing.price -= 5
    elif tamper == "fee":
        c.handling += 3
    elif tamper == "discount":
        c.offer_discount += 20
    elif tamper == "total":
        p.total_landed -= 10
    elif tamper == "baseline":
        p.best_single_total += 50
    elif tamper == "eta":
        c.eta_mins = 3
    with pytest.raises(GuardrailViolation) as e:
        assert_no_invented_numbers(p, s.trace)
    assert e.value.code == "INVENTED_NUMBER"


# ---------------------------------------------------------------- budget / diet / never-substitute
def test_budget_cap():
    enforce_budget(1199.99, 1200)
    with pytest.raises(GuardrailViolation):
        enforce_budget(1200.5, 1200)


def test_diet_is_enforced(orch):
    p = awaiting(orch.start("500 g chicken, 6 eggs and 1 litre milk, vegetarian, budget 700, before 9 pm"))
    tags = {i.listing.product.diet_tag.value for c in p.carts for i in c.items}
    assert tags <= {"veg", "vegan"}
    assert {u["item"] for u in p.unresolved_items} == {"chicken", "eggs"}


def test_never_substitute_brand_rule(orch, fresh_store):  # scenario 14
    for plat in Platform:
        for sku in ("milk-amul-1l", "milk-amul-500ml"):
            add_fault(fresh_store, "out_of_stock", plat, sku)
    s = orch.start("only Amul milk 1 litre, 1 kg rice, budget 400, before 9 pm")
    p = awaiting(s)
    assert all(i.request_item != "milk" for c in p.carts for i in c.items), "must not swap to another brand"
    assert any(u["item"] == "milk" for u in p.unresolved_items)


def test_never_substitute_item_rule(orch, fresh_store):
    for plat in Platform:
        for sku in ("paneer-amul-200g", "paneer-milkymist-200g"):
            add_fault(fresh_store, "out_of_stock", plat, sku)
    p = awaiting(orch.start("200 g paneer, never substitute paneer, 1 litre milk, budget 500, before 9 pm"))
    assert all(i.request_item != "paneer" for c in p.carts for i in c.items)
    assert not p.requires_confirmation
    assert not any(a.tool == "search_products" and a.input["query"] == "tofu" for a in orch.sessions._cache[p.session_id].trace)


# ---------------------------------------------------------------- payments (scenario 16)
def test_no_payment_capability_anywhere(orch):
    for obj in [*orch.adapters.values(), orch, orch.gate, ShoppingService(orch),
                MockPlatformAdapter(Platform.ZEPTO), HttpPlatformAdapter(Platform.ZEPTO, "http://x")]:
        assert_no_payment_capability(obj)
    assert forbidden_capabilities(type("Evil", (), {"pay_now": 1, "place_order": 2})) == ["pay_now", "place_order"]


def test_no_payment_routes():
    import ast
    import pathlib

    src = pathlib.Path("app/api/routes.py").read_text()
    paths = [n.args[0].value for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in {"get", "post", "put", "delete", "patch"}
             and n.args and isinstance(n.args[0], ast.Constant)]
    assert paths and not [p for p in paths if any(w in p.lower() for w in ("pay", "checkout", "order"))]


def test_approved_flow_never_pays(orch):
    s = orch.start(BASIC)
    p = awaiting(s)
    s, _ = orch.approve_proposal(s.session_id, ApprovalRequest(proposal_id=p.proposal_id,
                                                               decision=ApprovalDecision.APPROVE))
    assert s.prepared_carts and all(not c.payment_executed and not c.order_placed for c in s.prepared_carts)


def test_store_claiming_payment_is_rejected(monkeypatch):
    a = HttpPlatformAdapter(Platform.ZEPTO, "http://store")
    monkeypatch.setattr(a, "_call", lambda *args, **kw: PreparedCart(
        cart_ref="Z1", platform=Platform.ZEPTO, lines=[], payment_executed=True).model_dump(mode="json"))
    with pytest.raises(InvalidPlatformResponse):
        a.build_cart([])


def test_invalid_tool_payload_is_rejected(monkeypatch):
    a = HttpPlatformAdapter(Platform.ZEPTO, "http://store")
    monkeypatch.setattr(a, "_call", lambda *args, **kw: [{"platform": "zepto", "price": "free"}])
    with pytest.raises(InvalidPlatformResponse):
        a.search_products("milk")
