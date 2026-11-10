"""3-minute demo without a server: python scripts/demo.py

Runs the judging script end to end against the mock store:
goal -> plan -> an item goes out of stock during verification -> re-plan ->
fee spike flagged -> proposal -> approval -> carts prepared (no payment) ->
second session remembers preferences.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.adapters.mock_store import Fault, reset_store  # noqa: E402
from app.core.config import Clock, reset_settings  # noqa: E402
from app.core.models import ApprovalDecision, ApprovalRequest, Platform  # noqa: E402
from app.memory.database import Database  # noqa: E402
from app.planner.orchestrator import CartPilotOrchestrator  # noqa: E402

GOAL = "Weekly groceries for 2 people, vegetarian, under ₹1,200, delivered before 8 PM."


def show(p):
    print(f"\n  {p.summary}")
    for c in p.carts:
        print(f"  ┌ {c.platform.value:<10} items ₹{c.item_total:>7.2f}  handling ₹{c.handling:.0f}  delivery "
              f"₹{c.delivery:.0f}  small-cart ₹{c.small_cart:.0f}  surge ₹{c.surge:.0f}  offer -₹{c.offer_discount:.0f}"
              f"  = ₹{c.landed_total:.2f}  ({c.eta_mins} min)")
        for i in c.items:
            print(f"  │   {i.request_item:<9} {i.listing.product.name:<42} x{i.packs}  ₹{i.line_total:.2f}")
    for w in p.warnings:
        print(f"  ! {w.code}: {w.message}")


def main():
    store = reset_store()
    settings = reset_settings(database_url="sqlite://")
    o = CartPilotOrchestrator(settings=settings, db=Database("sqlite://"), clock=Clock(fixed_now="17:00"))

    # Faults for the story: milk goes out of stock and Blinkit adds a ₹40 rain fee at verification.
    store.add_fault(Fault(kind="out_of_stock", platform=Platform.ZEPTO, sku_id="milk-nandini-500ml", after_calls=1))
    store.add_fault(Fault(kind="fee_spike", platform=Platform.BLINKIT, after_calls=1, value=40))

    print(f"GOAL: {GOAL}")
    s = o.start(GOAL)
    print(f"\nPHASE: {s.phase.value}   trace actions: {len(s.trace)}")
    for r in s.replans:
        print(f"  ↻ re-plan {r.iteration} [{', '.join(r.triggers)}]: {r.action_taken}")
    show(s.proposal)

    s, changes = o.approve_proposal(s.session_id, ApprovalRequest(
        proposal_id=s.proposal.proposal_id, decision=ApprovalDecision.APPROVE,
        confirmed_items=s.proposal.requires_confirmation))
    print(f"\nAPPROVED → {s.phase.value}")
    for c in s.prepared_carts:
        print(f"  cart {c.cart_ref}: {len(c.lines)} lines, payment_executed={c.payment_executed}, "
              f"order_placed={c.order_placed}")
    print("  learned:", ", ".join(f"{c.kind}={c.value}" for c in changes))

    store.clear_faults()
    s2 = o.start("Weekly groceries, under ₹1,200, before 8 PM")
    print(f"\nNEXT WEEK: '{s2.request.raw_text}' → items from {s2.request.items_source}, "
          f"diet={s2.request.dietary_preference.value if s2.request.dietary_preference else None}")
    print("  memory notes:", *s2.memory_notes[:3], sep="\n   - ")


if __name__ == "__main__":
    main()
