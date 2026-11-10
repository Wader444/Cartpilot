"""Evaluation runner for the deck metrics (plan section 10).

    python scripts/evaluate.py                       # all 20 baskets
    python scripts/evaluate.py --labels tests/baskets/match_labels.json

Writes reports/eval_results.json and reports/eval_results.md. Every figure is
measured on the mock store – quote only these numbers, labelled as mock data.

Metrics
  savings            avg % saved vs the best single-app landed cost (verified)
  constraints        plans within budget AND meeting the deadline / plans proposed
  replan success     injected faults recovered (proposal reached, faulty choice avoided) / faults injected
  guardrails         runs stopping at the approval gate; invented numbers found
  matching accuracy  only with a hand-labelled file (Member B)
  optimizer check    only for baskets with an 'expected' split filled in
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from statistics import mean

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.adapters.mock_store import Fault, reset_store  # noqa: E402
from app.core.config import Clock, reset_settings  # noqa: E402
from app.core.guardrails import verify_proposal_numbers  # noqa: E402
from app.core.models import ItemRequest, Phase, Platform, ProposalStatus  # noqa: E402
from app.memory.database import Database  # noqa: E402
from app.planner.orchestrator import CartPilotOrchestrator  # noqa: E402
from app.tools.matching_tools import MatchConstraints, MatchingTools  # noqa: E402


def orchestrator() -> CartPilotOrchestrator:
    s = reset_settings(database_url="sqlite://", tool_timeout_s=2.0)
    return CartPilotOrchestrator(settings=s, db=Database("sqlite://"), clock=Clock(fixed_now="17:00"))


def chosen(proposal, item=None):
    for c in proposal.carts:
        for i in c.items:
            if item is None or i.request_item == item:
                return c.platform, i.listing.product_id, i.listing.price
    return None


def inject(fault: dict, text: str) -> tuple[bool, dict]:
    """Install the fault; returns (injected, context used to judge recovery)."""
    store = reset_store()
    kind = fault["kind"]
    ctx: dict = {"kind": kind}
    if kind == "out_of_stock_all":
        for sku in fault["skus"]:
            for p in Platform:
                store.add_fault(Fault(kind="out_of_stock", platform=p, sku_id=sku))
        ctx["skus"] = fault["skus"]
        return True, ctx
    if "platform" in fault:
        store.add_fault(Fault(kind=kind, platform=Platform(fault["platform"]), after_calls=fault.get("after_calls", 0),
                              value=fault.get("value")))
        ctx["platform"] = fault["platform"]
        return True, ctx
    # target chosen in a clean dry run
    dry = orchestrator().start(text)
    if dry.proposal is None or not dry.proposal.carts:
        return False, ctx
    target = fault["target"]
    if target.startswith("chosen:"):
        hit = chosen(dry.proposal, target.split(":", 1)[1])
        if hit is None:
            return False, ctx
        platform, sku, price = hit
    else:
        platform, sku, price = dry.proposal.carts[0].platform, None, None
    store = reset_store()
    value = fault.get("value")
    if kind == "price_change":
        value = price + fault.get("delta", 40)
    store.add_fault(Fault(kind=kind, platform=platform, sku_id=sku, after_calls=fault.get("after_calls", 0),
                          value=value))
    ctx.update(platform=platform.value, sku=sku)
    return True, ctx


def recovered(state, ctx) -> bool:
    p = state.proposal
    if state.phase != Phase.AWAITING_APPROVAL or p is None:
        return False
    kind = ctx["kind"]
    used = {(c.platform.value, i.listing.product_id) for c in p.carts for i in c.items}
    if kind == "out_of_stock":
        return (ctx["platform"], ctx["sku"]) not in used and any("OUT_OF_STOCK" in r.triggers for r in state.replans)
    if kind == "out_of_stock_all":
        return not any(sku in {u[1] for u in used} for sku in ctx["skus"])
    if kind in ("eta", "timeout", "invalid_response"):
        return all(c.platform.value != ctx["platform"] for c in p.carts)
    if kind in ("price_change", "fee_spike"):
        return verify_proposal_numbers(p, state.trace) == [] and bool(state.replans)
    return True


def matching_accuracy(path: str) -> dict:
    data = json.load(open(path))
    o = orchestrator()
    st = o.start("1 litre milk, budget 100, before 9 pm")
    tools = MatchingTools(o.recorder(st), o.settings.matching_module)
    from app.tools.platform_tools import PlatformTools

    pt = PlatformTools(o.adapters, o.recorder(st))
    correct = 0
    rows = []
    for pair in data["pairs"]:
        listings = [l for p in Platform for l in (pt.search_products(p, pair["product_id"].split("-")[0]) or [])
                    if l.product_id == pair["product_id"]]
        listings = listings or [l for p in Platform for l in (pt.search_products(p, pair["item"]) or [])
                                if l.product_id == pair["product_id"]]
        if not listings:
            rows.append({**pair, "predicted": None})
            continue
        m = tools.match_products(ItemRequest(name=pair["item"]), listings[:1], MatchConstraints())
        predicted = bool(m.candidates) and m.candidates[0].band.value == "auto_accept"
        correct += predicted == pair["correct"]
        rows.append({**pair, "predicted": predicted})
    return {"pairs": len(rows), "accuracy_pct": round(100 * correct / max(1, len(rows)), 1), "rows": rows,
            "source": os.path.basename(path)}


def run(baskets_path: str, labels: str | None) -> dict:
    baskets = json.load(open(baskets_path))["baskets"]
    results = []
    for b in baskets:
        fault = b.get("fault")
        ctx = None
        if fault:
            ok, ctx = inject(fault, b["text"])
            if not ok:
                ctx = {"kind": fault["kind"], "skipped": True}
        else:
            reset_store()
        s = orchestrator().start(b["text"])
        p = s.proposal
        row = {"id": b["id"], "phase": s.phase.value, "errors": s.errors[:1], "fault": fault["kind"] if fault else None}
        if p is not None:
            row.update(
                total=p.total_landed, best_single=p.best_single_total, platforms=[c.platform.value for c in p.carts],
                savings_pct=p.savings_pct, within_budget=p.within_budget, deadline_met=bool(p.deadline_met),
                status=p.status.value, replans=p.replan_count, unresolved=[u["item"] for u in p.unresolved_items],
                invented_numbers=len(verify_proposal_numbers(p, s.trace)))
            if b.get("expected"):
                got = {i.request_item: c.platform.value for c in p.carts for i in c.items}
                row["matches_expected_split"] = got == b["expected"]
        row["stopped_at_gate"] = (s.phase in (Phase.AWAITING_APPROVAL, Phase.AWAITING_CLARIFICATION, Phase.FAILED)
                                  and not any(a.tool == "build_cart" for a in s.trace))
        if ctx is not None and not ctx.get("skipped"):
            row["recovered"] = recovered(s, ctx)
        results.append(row)

    proposed = [r for r in results if r.get("total") is not None and r.get("platforms")]
    with_savings = [r["savings_pct"] for r in proposed if r.get("savings_pct") is not None]
    faults = [r for r in results if "recovered" in r]
    expected = [r for r in results if "matches_expected_split" in r]
    summary = {
        "baskets": len(results),
        "proposals": len(proposed),
        "avg_savings_pct_vs_best_single_app": round(mean(with_savings), 2) if with_savings else None,
        "max_savings_pct": max(with_savings) if with_savings else None,
        "baskets_where_split_won": f"{sum(1 for r in proposed if len(r.get('platforms', [])) > 1 and (r.get('savings_pct') or 0) > 0)}/{len(proposed)}",
        "avg_savings_pct_when_split_won": (round(mean(x for r in proposed for x in [r.get("savings_pct") or 0]
                                                      if len(r.get("platforms", [])) > 1 and x > 0), 2)
                                           if any(len(r.get("platforms", [])) > 1 and (r.get("savings_pct") or 0) > 0
                                                  for r in proposed) else None),
        "constraint_satisfaction_pct": round(100 * sum(r["within_budget"] and r["deadline_met"] for r in proposed)
                                             / max(1, len(proposed)), 1),
        "replan_success_pct": round(100 * sum(r["recovered"] for r in faults) / max(1, len(faults)), 1),
        "faults_injected": len(faults),
        "stopped_at_approval_gate_pct": round(100 * sum(r["stopped_at_gate"] for r in results) / len(results), 1),
        "invented_numbers_found": sum(r.get("invented_numbers", 0) for r in results),
        "optimizer_matches_labelled_split": (f"{sum(r['matches_expected_split'] for r in expected)}/{len(expected)}"
                                             if expected else "no labelled baskets yet (Member B)"),
        "data": "mock store – realistic but not live prices",
    }
    out = {"summary": summary, "results": results}
    if labels:
        out["matching"] = matching_accuracy(labels)
        summary["matching_accuracy_pct"] = out["matching"]["accuracy_pct"]
    return out


def to_markdown(out: dict) -> str:
    s = out["summary"]
    lines = ["# CartPilot evaluation (mock data)", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k.replace('_', ' ')} | {v} |" for k, v in s.items()]
    lines += ["", "| Basket | Phase | Fault | Total ₹ | Best single ₹ | Saving % | Apps | Budget | Deadline | "
                  "Re-plans | Recovered | Gate |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in out["results"]:
        lines.append(f"| {r['id']} | {r['phase']} | {r['fault'] or ''} | {r.get('total', '')} | "
                     f"{r.get('best_single', '')} | {r.get('savings_pct', '')} | {','.join(r.get('platforms', []))} | "
                     f"{'ok' if r.get('within_budget') else 'over' if 'within_budget' in r else ''} | "
                     f"{'ok' if r.get('deadline_met') else ''} | {r.get('replans', '')} | "
                     f"{r.get('recovered', '')} | {'✓' if r['stopped_at_gate'] else '✗'} |")
    if "matching" in out:
        lines += ["", f"Matching accuracy on {out['matching']['pairs']} labelled pairs "
                      f"({out['matching']['source']}): **{out['matching']['accuracy_pct']}%**"]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baskets", default=os.path.join(ROOT, "tests", "baskets", "baskets.json"))
    ap.add_argument("--labels", default=None, help="hand-labelled match pairs (JSON)")
    ap.add_argument("--out", default=os.path.join(ROOT, "reports"))
    a = ap.parse_args()
    out = run(a.baskets, a.labels)
    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "eval_results.json"), "w"), indent=2, ensure_ascii=False, default=str)
    md = to_markdown(out)
    open(os.path.join(a.out, "eval_results.md"), "w").write(md)
    print(md)


if __name__ == "__main__":
    main()
