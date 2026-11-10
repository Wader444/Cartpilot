"""OR-Tools optimizer: correctness against brute force, and never worse than the split search."""
import itertools
import json

import pytest

from app.core.guardrails import verify_proposal_numbers
from app.core.models import Phase
from app.core.units import packs_needed
from app.optimizer import optimizer as opt
from app.tools.optimizer_tools import _usable, fallback_optimize_cart, landed

from tests.conftest import make_orchestrator

BASKETS = [b["text"] for b in json.load(open("tests/baskets/baskets.json"))["baskets"] if not b["fault"]]


def _plan_inputs(text):
    """Run the planner once and capture what it hands the optimizer."""
    o = make_orchestrator(optimizer_module="app.optimizer.optimizer")
    captured = {}
    real = opt.optimize_cart

    def spy(items, c, pref=None):
        captured["args"] = (items, c, pref)
        return real(items, c, pref)

    opt.optimize_cart = spy
    try:
        s = o.start(text)
    finally:
        opt.optimize_cart = real
    return s, captured.get("args")


def test_without_ortools_falls_back_cleanly(monkeypatch):
    import app.optimizer.cpsat as cpsat
    monkeypatch.setattr(cpsat, "solve", lambda *a, **k: None)
    s, _ = _plan_inputs(BASKETS[0])
    assert s.phase == Phase.AWAITING_APPROVAL and s.optimization.solver == "split search"
    assert any("Member C landed-cost engine verified" in n for n in s.optimization.notes)


@pytest.mark.parametrize("text", BASKETS)
def test_cpsat_never_worse_and_numbers_verified(text):
    pytest.importorskip("ortools")
    s, (items, c, pref) = _plan_inputs(text)
    search = fallback_optimize_cart(items, c, pref)
    assert s.optimization.solver == "OR-Tools CP-SAT", s.optimization.notes
    assert s.optimization.total_landed <= search.total_landed + 0.01
    assert s.phase == Phase.AWAITING_APPROVAL and verify_proposal_numbers(s.proposal, s.trace) == []


BRUTE = ["1 kg rice, 1 litre milk, 1 kg tomato, 6 bananas, budget 900, before 9 pm",
         "2 kg atta, 400 g curd, 1 kg onion, 1 bread, 200 g paneer, budget 900, before 9 pm",
         "500 g tea, 1 kg sugar, 1 L oil, 6 eggs, budget 900, before 9 pm, eggetarian"]


def _brute_force(items, c):
    """True optimum by trying every assignment of every item to every usable listing."""
    allowed = opt._allowed(c)
    usable = {i: [x for x in _usable(m, set()) if x.listing.platform in allowed] for i, m in enumerate(items)}
    active = [i for i, cs in usable.items() if cs]
    lines = {}
    for i in active:
        it = items[i].request_item
        for k, x in enumerate(usable[i]):
            p = x.listing.product
            lines[(i, k)] = x.listing.price * packs_needed(it.quantity, it.unit, p.quantity_value, p.quantity_unit)
    best = None
    for ks in itertools.product(*[range(len(usable[i])) for i in active]):
        per = {}
        for i, k in zip(active, ks):
            plat = usable[i][k].listing.platform
            per[plat] = per.get(plat, 0) + lines[(i, k)]
        total = round(sum(landed(p, round(v, 2), c)[0] for p, v in per.items()), 2)
        best = total if best is None or total < best else best
    return best, usable, allowed


@pytest.mark.parametrize("text", BRUTE)
def test_split_search_matches_brute_force(text):
    s, (items, c, pref) = _plan_inputs(text)
    best, _, _ = _brute_force(items, c)
    assert fallback_optimize_cart(items, c).total_landed == pytest.approx(best, abs=0.02)


@pytest.mark.parametrize("text", BRUTE)
def test_cpsat_matches_brute_force_optimum(text):
    pytest.importorskip("ortools")
    from app.optimizer import cpsat
    s, (items, c, pref) = _plan_inputs(text)
    best, usable, allowed = _brute_force(items, c)
    assign = cpsat.solve(items, usable, allowed, c)
    got = opt._result_from(items, assign, c, fallback_optimize_cart(items, c), "cpsat").total_landed
    assert got == pytest.approx(best, abs=0.02)
