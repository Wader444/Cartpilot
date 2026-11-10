"""Member C's UI client against Member A's real API (runs where FastAPI + httpx are installed)."""
import importlib.util
import pathlib
import sys

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.adapters.mock_store import reset_store  # noqa: E402
from app.api.service import ShoppingService, set_service  # noqa: E402
from app.main import create_app  # noqa: E402

from tests.conftest import WEEKLY, add_fault, make_orchestrator  # noqa: E402

UI = pathlib.Path(__file__).resolve().parents[1] / "ui"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, UI / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def ac(monkeypatch):
    set_service(ShoppingService(make_orchestrator()))
    client = TestClient(create_app())
    _load("mock_data")
    mod = _load("api_client")
    mod.USE_MOCK = False

    def call(method, path, json=None):  # route the UI client's HTTP calls into the real app
        r = client.request(method, path, json=json)
        if r.status_code >= 400:
            d = r.json().get("detail", {})
            raise mod.ApiError(r.status_code, d.get("code", "ERROR") if isinstance(d, dict) else "INVALID_INPUT",
                               d.get("message", "") if isinstance(d, dict) else str(d))
        return r.json()

    monkeypatch.setattr(mod, "_call", call)
    yield mod
    set_service(None)


def test_plan_shape_matches_app_py(ac):
    ui = ac.get_proposal(WEEKLY)
    assert ui["constraints"] == {"budget": 1200, "deadline": "20:00"}
    assert ui["platforms"] and ui["approvable"] and ui["proposal_id"].startswith("prop_")
    for p in ui["platforms"]:
        lc = p["landed_cost"]
        assert abs(lc["item_total"] + lc["handling"] + lc["delivery"] + lc["small_cart"] + lc["surge"]
                   - lc["offers"] - lc["landed_total"]) < 0.02
        for line in p["cart"]:
            assert {"product", "listing", "qty"} <= set(line) and line["product"]["name"]
    assert abs(sum(p["landed_cost"]["landed_total"] for p in ui["platforms"]) - ui["split_total"]) < 0.02
    assert ui["best_single_app"]["landed_total"] - ui["split_total"] == pytest.approx(ui["savings"], abs=0.02)


def test_clarification_then_answer(ac):
    with pytest.raises(ac.ClarificationNeeded) as e:
        ac.get_proposal("2 kg atta and 1 litre milk")
    ui = ac.answer_clarification(e.value.session_id, "budget 600 by 9 pm")
    assert ui["constraints"] == {"budget": 600, "deadline": "21:00"}


def test_approve_prepares_carts_without_payment(ac):
    ui = ac.get_proposal(WEEKLY)
    r = ac.approve(ui["session_id"], ui["proposal_id"], remind_in_days=7)
    carts = r["session"]["prepared_carts"]
    assert len(carts) == len(ui["platforms"]) and all(not c["payment_executed"] for c in carts)


def test_edit_returns_new_proposal_and_old_is_stale(ac):
    ui = ac.get_proposal(WEEKLY)
    ui2 = ac.edit(ui["session_id"], ui["proposal_id"], {"remove_items": ["spinach"]})
    assert ui2["proposal_id"] != ui["proposal_id"] and ui2["version"] == 2
    with pytest.raises(ac.ApiError) as e:
        ac.approve(ui["session_id"], ui["proposal_id"])
    assert e.value.code == "STALE_PROPOSAL"


def test_over_budget_and_substitute_flags(ac):
    ui = ac.get_proposal(WEEKLY.replace("1,200", "300"))
    assert not ui["approvable"] and ui["status"] == "over_budget"
    with pytest.raises(ac.ApiError) as e:
        ac.approve(ui["session_id"], ui["proposal_id"])
    assert e.value.status == 422
    store = reset_store()
    for p in ("blinkit", "zepto", "instamart"):
        for sku in ("paneer-amul-200g", "paneer-milkymist-200g"):
            add_fault(store, "out_of_stock", p, sku)
    ui = ac.get_proposal("200 g paneer and 1 litre milk, vegetarian, budget 500, before 9 pm")
    assert ui["requires_confirmation"] and any(l["requires_confirmation"] for p in ui["platforms"] for l in p["cart"])
    r = ac.approve(ui["session_id"], ui["proposal_id"], confirmed_items=ui["requires_confirmation"])
    assert r["session"]["phase"] == "DONE"


def test_reject_and_trace(ac):
    ui = ac.get_proposal(WEEKLY)
    assert any(a["tool"] == "search_products" for a in ac.get_trace(ui["session_id"])["actions"])
    r = ac.reject(ui["session_id"], ui["proposal_id"], "too pricey", ["Nandini"])
    assert r["session"]["phase"] == "REJECTED"
