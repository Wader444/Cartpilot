import streamlit as st
from api_client import (DEMO_GOAL, USE_MOCK, ApiError, ClarificationNeeded, answer_clarification, approve,
                        edit, get_proposal, get_trace, reject, set_demo_faults)

st.set_page_config(page_title="CartPilot", layout="wide")

def inr(x) -> str:
    return f"₹{x:,.2f}"   # display only: values arrive from tools already computed

def basket_view(platforms: list) -> None:
    rows = []
    for p in platforms:
        for l in p["cart"]:
            prod = l["product"]
            rows.append({
                "Item": prod["name"], "Brand": prod["brand"],
                "Pack": f'{prod["quantity_value"]} {prod["quantity_unit"]}',
                "Qty": l["qty"], "Bought from": p["platform"],
            })
    st.subheader("Basket")
    st.dataframe(rows, hide_index=True, use_container_width=True)

def cost_card(p: dict) -> None:
    lc = p["landed_cost"]
    with st.container(border=True):
        a, b = st.columns([3, 1])
        a.subheader(p["platform"])
        b.caption(f"ETA {p['eta_mins']} min | "
                  f"{'Meets deadline' if p['deadline_met'] else 'Misses deadline'}")
        st.dataframe(
            [{"Item": l["product"]["name"],
              "Pack": f'{l["product"]["quantity_value"]} {l["product"]["quantity_unit"]}',
              "Qty": l["qty"], "Price": inr(l["listing"]["price"])}
             for l in p["cart"]],
            hide_index=True, use_container_width=True)
        st.table({
            "Item total": inr(lc["item_total"]),
            "Handling fee": inr(lc["handling"]),
            "Delivery fee": inr(lc["delivery"]),
            "Small-cart fee": inr(lc["small_cart"]),
            "Surge / rain fee": inr(lc["surge"]),
            "Offers": f"- {inr(lc['offers'])}",
            "Landed total": inr(lc["landed_total"]),
        })

st.title("CartPilot")
st.caption("Demo data is mock, realistic, and swappable through the PlatformAdapter.")

with st.sidebar:
    st.header("Demo")
    if st.button("Load demo basket", key="demo_basket"):
        st.session_state["goal"] = DEMO_GOAL
    faults_on = st.checkbox("Inject demo faults (curd out of stock on Zepto, ₹15 Blinkit surge)",
                            key="demo_faults", disabled=USE_MOCK)
    st.caption("Faults need the API started with CARTPILOT_ENABLE_FAULTS=true.")

st.session_state.setdefault("goal", "Weekly groceries for 2, veg, under ₹1,200, before 8 PM")
goal = st.text_area("What do you need?", key="goal", height=80)

def _plan(fn, *args):
    """Run a planner call and store the result; show clarification / errors in plain words."""
    st.session_state.pop("decision", None)
    try:
        st.session_state["proposal"] = fn(*args)
        st.session_state.pop("clarify", None)
    except ClarificationNeeded as e:
        st.session_state.pop("proposal", None)
        st.session_state["clarify"] = {"question": e.question, "session_id": e.session_id}
    except ApiError as e:
        st.error(e.message)
    except Exception:
        st.error("Couldn't reach the planner. Please try again.")


if st.button("Plan my cart", type="primary"):
    with st.spinner("Planning..."):
        if not USE_MOCK and not set_demo_faults(faults_on) and faults_on:
            st.warning("The API has fault injection disabled – planning without demo faults.")
        _plan(get_proposal, goal)

clarify = st.session_state.get("clarify")
if clarify:
    st.info(clarify["question"])
    answer = st.text_input("Your answer", key="clarify_answer", placeholder="e.g. budget 1200, by 8 pm")
    if st.button("Continue", key="clarify_go") and answer:
        with st.spinner("Planning..."):
            _plan(answer_clarification, clarify["session_id"], answer)

proposal = st.session_state.get("proposal")

if not proposal:
    st.info("Enter a goal and press **Plan my cart** to see the cost breakdown.")
else:
    c = proposal["constraints"]
    m1, m2, m3 = st.columns(3)
    m1.metric("Budget", f"₹{c['budget']:,}")
    m2.metric("Deliver by", c["deadline"])
    m3.metric("Last checked", proposal["last_checked"])

    for w in proposal["warnings"]:
        st.warning(w)

    basket_view(proposal["platforms"])

    s1, s2, s3 = st.columns(3)
    s1.metric("Split total", inr(proposal["split_total"]))
    s2.metric(f"Best single app ({proposal['best_single_app']['platform']})",
              inr(proposal["best_single_app"]["landed_total"]))
    s3.metric("Savings", inr(proposal["savings"]))

    cols = st.columns(len(proposal["platforms"]))
    for col, p in zip(cols, proposal["platforms"]):
        with col:
            cost_card(p)


# ------------------------------------------------------------------ approval (live backend only)
def approval_panel(p: dict) -> None:
    st.divider()
    st.subheader("Your decision")
    if p.get("summary"):
        st.caption(p["summary"])
    decision = st.session_state.get("decision")
    if decision:
        if decision["kind"] == "approved":
            st.success(decision["message"])
            st.dataframe([{"Cart": c["cart_ref"], "App": c["platform"], "Lines": len(c["lines"]),
                           "Payment made": c["payment_executed"], "Order placed": c["order_placed"]}
                          for c in decision["carts"]], hide_index=True, use_container_width=True)
            for r in decision.get("reminders", []):
                st.caption(f"Reminder set: {r}")
        else:
            st.warning(decision["message"])
        return
    edit_panel(p)
    if not p.get("approvable"):
        st.error("This plan can't be approved as it is (over budget or nothing could be sourced). "
                 "Edit it below or change the goal and plan again.")
        return
    confirmed = []
    for plat in p["platforms"]:
        for line in plat["cart"]:
            if line.get("requires_confirmation"):
                pid = line["listing"]["product_id"]
                if st.checkbox(f"Use substitute {line['product']['name']} for '{line['request_item']}' "
                               f"({plat['platform']})", key=f"confirm_{pid}"):
                    confirmed.append(pid)
    remind = st.checkbox("Remind me to reorder in 7 days", value=True, key="remind")
    a, b = st.columns(2)
    if a.button("Approve and prepare carts", type="primary", key="approve"):
        try:
            r = approve(p["session_id"], p["proposal_id"], confirmed, 7 if remind else None)
            st.session_state["decision"] = {"kind": "approved", "message": r["message"],
                                            "carts": r["session"]["prepared_carts"],
                                            "reminders": ["reorder in 7 days"] if remind else []}
        except ApiError as e:
            st.error(e.message)
        st.rerun()
    reason = b.text_input("Reason (optional)", key="reject_reason", label_visibility="collapsed",
                          placeholder="Reason for rejecting (optional)")
    if b.button("Reject", key="reject"):
        try:
            r = reject(p["session_id"], p["proposal_id"], reason)
            st.session_state["decision"] = {"kind": "rejected", "message": r["message"]}
        except ApiError as e:
            st.error(e.message)
        st.rerun()


def edit_panel(p: dict) -> None:
    with st.expander("Edit this plan"):
        items = sorted({line["request_item"] for plat in p["platforms"] for line in plat["cart"]})
        remove = st.multiselect("Remove items", items, key="edit_remove")
        budget = st.number_input("New budget (₹, 0 = keep)", min_value=0, step=100, value=0, key="edit_budget")
        brands = st.text_input("Brands to avoid (comma separated)", key="edit_brands")
        if st.button("Re-plan with changes", key="edit_go"):
            changes = {}
            if remove:
                changes["remove_items"] = list(remove)
            if budget:
                changes["budget"] = float(budget)
            if brands.strip():
                changes["reject_brands"] = [b.strip() for b in brands.split(",") if b.strip()]
            if not changes:
                st.warning("Choose at least one change.")
                return
            try:
                with st.spinner("Re-planning..."):
                    st.session_state["proposal"] = edit(p["session_id"], p["proposal_id"], changes)
                st.session_state.pop("decision", None)
            except ApiError as e:
                st.error(e.message)
                return
            st.rerun()


def trace_panel(session_id: str) -> None:
    with st.expander("Agent trace – every tool call and decision"):
        try:
            t = get_trace(session_id)
        except Exception:
            st.caption("Trace unavailable.")
            return
        for r in t.get("replans", []):
            st.warning(f"Re-plan {r['iteration']} [{', '.join(r['triggers'])}]: {r['action_taken']}")
        st.dataframe([{"Phase": a["phase"], "Tool": a["tool"], "Status": a["status"], "Why": a["reason"]}
                      for a in t.get("actions", [])], hide_index=True, use_container_width=True)


if proposal:
    if USE_MOCK or "proposal_id" not in proposal:
        st.info("Approve / Reject and the agent trace need the live backend (run with USE_MOCK=0).")
    else:
        approval_panel(proposal)
        trace_panel(proposal["session_id"])

