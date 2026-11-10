"""HTTP-level tests. Skipped automatically if FastAPI/httpx are not installed."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from app.api.service import ShoppingService, set_service  # noqa: E402
from app.main import create_app  # noqa: E402

from tests.conftest import BASIC, WEEKLY, make_orchestrator  # noqa: E402


@pytest.fixture
def client():
    set_service(ShoppingService(make_orchestrator()))
    yield TestClient(create_app())
    set_service(None)


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "never pays" in r.json()["payments"]


def test_full_flow(client):
    r = client.post("/api/shopping/plan", json={"text": WEEKLY})
    assert r.status_code == 201
    sid, body = r.json()["session_id"], r.json()
    assert body["phase"] == "AWAITING_APPROVAL" and body["proposal_id"]
    prop = client.get(f"/api/shopping/{sid}/proposal").json()
    assert prop["total_landed"] <= 1200 and prop["carts"]
    trace = client.get(f"/api/shopping/{sid}/trace").json()
    assert any(a["tool"] == "search_products" for a in trace["actions"])
    r = client.post(f"/api/shopping/{sid}/approve", json={"proposal_id": prop["proposal_id"]})
    assert r.status_code == 200, r.text
    carts = r.json()["session"]["prepared_carts"]
    assert carts and all(c["payment_executed"] is False and c["order_placed"] is False for c in carts)
    assert client.get("/api/memory/episodes").json()["episodes"][0]["decision"] == "approved"
    assert client.get("/api/memory/preferences").json()["preferences"]["accepted_brands"]


def test_approval_errors(client):
    sid = client.post("/api/shopping/plan", json={"text": BASIC}).json()["session_id"]
    assert client.post(f"/api/shopping/{sid}/approve", json={"proposal_id": "nope"}).status_code == 404
    assert client.post("/api/shopping/sess_missing/approve", json={"proposal_id": "x"}).status_code == 404
    over = client.post("/api/shopping/plan", json={"text": WEEKLY.replace("1,200", "300")}).json()
    r = client.post(f"/api/shopping/{over['session_id']}/approve", json={"proposal_id": over["proposal_id"]})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "OVER_BUDGET"


def test_clarify_reject_edit(client):
    r = client.post("/api/shopping/plan", json={"text": "2 kg atta and 1 litre milk"}).json()
    assert r["phase"] == "AWAITING_CLARIFICATION" and r["clarification_question"]
    assert client.get(f"/api/shopping/{r['session_id']}/proposal").status_code == 404
    r = client.post(f"/api/shopping/{r['session_id']}/clarify", json={"answer": "budget 600 by 9 pm"}).json()
    assert r["phase"] == "AWAITING_APPROVAL"
    e = client.post(f"/api/shopping/{r['session_id']}/edit",
                    json={"proposal_id": r["proposal_id"], "changes": {"remove_items": ["milk"]}}).json()
    assert e["session"]["proposal_id"] != r["proposal_id"]
    j = client.post(f"/api/shopping/{r['session_id']}/reject",
                    json={"proposal_id": e["session"]["proposal_id"], "rejected_brands": ["Pillsbury"]})
    assert j.status_code == 200 and j.json()["session"]["phase"] == "REJECTED"


def test_validation(client):
    assert client.post("/api/shopping/plan", json={"text": ""}).status_code == 422
    assert client.post("/api/shopping/plan", json={"text": BASIC, "pincode": "12"}).status_code == 422


def test_fault_injection_endpoint(client):
    assert client.post("/api/dev/faults", json={"kind": "timeout", "platform": "blinkit"}).status_code == 200
    body = client.post("/api/shopping/plan", json={"text": BASIC}).json()
    prop = client.get(f"/api/shopping/{body['session_id']}/proposal").json()
    assert all(c["platform"] != "blinkit" for c in prop["carts"])
    assert client.delete("/api/dev/faults").status_code == 200


def test_bad_edits_are_4xx_and_proposal_stays_approvable(client):
    b = client.post("/api/shopping/plan", json={"text": "1 litre milk, budget 200, before 9 pm"}).json()
    sid, pid = b["session_id"], b["proposal_id"]
    assert client.post(f"/api/shopping/{sid}/edit", json={"proposal_id": pid, "changes": {"deadline": "8"}}).status_code == 422
    assert client.post(f"/api/shopping/{sid}/edit",
                       json={"proposal_id": pid, "changes": {"remove_items": ["milk"]}}).status_code == 422
    assert client.post(f"/api/shopping/{sid}/edit", json={"proposal_id": pid, "changes": {}}).status_code == 400
    r = client.post(f"/api/shopping/{sid}/approve", json={"proposal_id": pid, "remind_in_days": 7})
    assert r.status_code == 200, r.text


def test_reminders_and_pantry(client):
    b = client.post("/api/shopping/plan", json={"text": BASIC}).json()
    client.post(f"/api/shopping/{b['session_id']}/approve", json={"proposal_id": b["proposal_id"], "remind_in_days": 7})
    rems = client.get("/api/reminders").json()["reminders"]
    assert len(rems) == 1 and rems[0]["kind"] == "reorder"
    r = client.post("/api/reminders", json={"text": "Check atta price", "in_days": 2, "kind": "price_watch"})
    assert r.status_code == 201
    assert client.post("/api/reminders", json={"text": "x"}).status_code == 422          # no time given
    assert client.post("/api/reminders", json={"text": "x", "in_days": 1,
                                               "time": "2030-01-01T10:00:00+05:30"}).status_code == 422
    pantry = client.get("/api/memory/pantry").json()
    assert {"atta", "milk", "rice"} <= {i["item"] for i in pantry["items"]}


def test_fault_endpoint_disabled_without_flag(client):
    from app.core.config import get_settings
    get_settings().enable_fault_injection = False
    assert client.post("/api/dev/faults", json={"kind": "timeout", "platform": "blinkit"}).status_code == 403
