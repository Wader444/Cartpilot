import json

from app.adapters.mock_store import KNOWN_BRANDS
from app.agents.intent_agent import IntentAgent
from app.core.config import Clock, reset_settings
from app.core.models import DietPreference, Phase

from tests.conftest import BASIC, WEEKLY


def agent(llm=None):
    return IntentAgent(reset_settings(), Clock(fixed_now="17:00"), KNOWN_BRANDS, llm_client=llm)


def test_spec_example_parses_exactly():
    r = agent().parse(BASIC)
    q = r.request
    assert [(i.name, i.quantity, i.unit) for i in q.items] == [("atta", 2, "kg"), ("milk", 1, "litre"), ("rice", 1, "kg")]
    assert q.dietary_preference == DietPreference.VEGETARIAN
    assert q.budget == 1200 and q.deadline == "20:00"
    assert q.preferred_brands == [] and q.never_substitute == []
    assert r.clarification_question is None and r.parser == "deterministic"


def test_weekly_goal_without_items_is_valid():
    r = agent().parse(WEEKLY)
    assert r.request.items == [] and r.request.household_size == 2 and r.clarification_question is None


def test_missing_budget_asks_one_question():  # scenario 2
    r = agent().parse("2 kg atta and milk, deliver before 8 pm")
    assert r.missing == ["budget"] and r.request.budget is None
    assert r.clarification_question.count("?") == 0 and "budget" in r.clarification_question


def test_missing_deadline_asks_one_question():  # scenario 3
    r = agent().parse("2 kg atta under 500")
    assert r.missing == ["deadline"] and r.request.deadline is None
    assert "delivery time" in r.clarification_question


def test_ambiguous_time_is_not_assumed():
    r = agent().parse("milk and bread, budget 200, by 8")
    assert r.request.deadline is None and "deadline" in r.missing
    assert any("ambiguous" in n for n in r.notes)


def test_units_brands_and_rules():
    r = agent().parse("get amul butter, a dozen eggs, onions 1 kg, 500 ml curd; avoid Nandini; only Amul milk; "
                      "never substitute paneer; under 800 by 21:30, eggetarian")
    q = r.request
    items = {i.name: i for i in q.items}
    assert items["butter"].brand == "Amul"
    assert (items["eggs"].quantity, items["eggs"].unit) == (12, "pcs")
    assert (items["onions"].quantity, items["onions"].unit) == (1, "kg")
    assert (items["curd"].quantity, items["curd"].unit) == (500, "ml")
    assert q.avoid_brands == ["Nandini"] and "amul milk" in q.never_substitute and "paneer" in q.never_substitute
    assert q.budget == 800 and q.deadline == "21:30" and q.dietary_preference == DietPreference.EGGETARIAN


def test_relative_deadline_and_budget_not_confused():
    q = agent().parse("6 bananas, 1kg sugar within 30 minutes budget 300").request
    assert q.budget == 300 and q.deadline == "17:30"


def test_llm_output_is_grounded_against_text():
    def fake_llm(_):
        return json.dumps({"items": [{"name": "milk", "quantity": 1, "unit": "litre"},
                                     {"name": "caviar", "quantity": 1, "unit": "pcs"}],
                           "dietary_preference": "vegetarian", "budget": 5000, "deadline": "20:00",
                           "preferred_brands": ["Amul"], "avoid_brands": [], "never_substitute": []})
    r = agent(fake_llm).parse("1 litre milk, veg, deliver before 8 pm")
    assert r.parser == "llm"
    assert r.request.budget is None, "LLM invented a budget that is not in the text"
    assert [i.name for i in r.request.items] == ["milk"]
    assert r.request.preferred_brands == []
    assert r.missing == ["budget"]


def test_llm_failure_falls_back_to_deterministic():
    def broken(_):
        raise OSError("no LLM")
    r = agent(broken).parse(BASIC)
    assert r.parser == "deterministic" and r.request.budget == 1200


def test_clarification_flow(orch):  # scenarios 2 + 3 through the planner
    s = orch.start("2 kg atta and 1 litre milk")
    assert s.phase == Phase.AWAITING_CLARIFICATION and s.proposal is None
    assert not any(a.tool == "search_products" for a in s.trace), "must not search before constraints are known"
    s = orch.clarify(s.session_id, "budget 600")
    assert s.phase == Phase.AWAITING_CLARIFICATION and "delivery time" in s.clarification_question
    s = orch.clarify(s.session_id, "by 9 pm")
    assert s.phase == Phase.AWAITING_APPROVAL and s.request.budget == 600 and s.request.deadline == "21:00"
