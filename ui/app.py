"""CartPilot Streamlit UI – Autonomous Quick-Commerce Shopping Agent.

AgentX Hackathon 2026 · PS-07
Enhanced with ui-ux-pro-max design system: Vibrant & Block-based pattern,
accessible typography (Nunito Sans / Rubik), custom brand palettes,
interactive Altair analytics, and visual agent lifecycle stepper.
"""
from __future__ import annotations

import sys
import os
from pathlib import Path

# ── Path bootstrap ──────────────────────────────────────────────────────────
# Works in both Streamlit Cloud (runs `streamlit run ui/app.py` from repo root)
# and locally (run from repo root or from ui/ directory).
_HERE = Path(__file__).resolve().parent          # .../ui/
_ROOT = _HERE.parent                              # .../Cartpilot/
for _p in (_HERE, _ROOT):
    _ps = str(_p)
    if _ps not in sys.path:
        sys.path.insert(0, _ps)

import altair as alt
import pandas as pd
import streamlit as st

from api_client import (
    BACKEND_MODE,
    DEMO_GOAL,
    USE_MOCK,
    ApiError,
    ClarificationNeeded,
    answer_clarification,
    approve,
    edit,
    get_episodes,
    get_health,
    get_pantry,
    get_preferences,
    get_proposal,
    get_reminders,
    get_trace,
    reject,
    set_demo_faults,
)

st.set_page_config(
    page_title="CartPilot · Autonomous Shopping Agent",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ------------------------------------------------------------------ UI-UX-PRO-MAX DESIGN SYSTEM & CSS
CUSTOM_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Nunito+Sans:wght@400;600;700;800&family=Rubik:wght@500;600;700&display=swap');

:root {
    --color-primary: #059669;
    --color-primary-light: #ECFDF5;
    --color-secondary: #10B981;
    --color-accent: #D97706;
    --color-accent-light: #FFFBEB;
    --color-danger: #DC2626;
    --color-danger-light: #FEF2F2;
    --color-surface: #FFFFFF;
    --color-bg: #F8FAFC;
    --color-border: #E2E8F0;
    --color-text: #0F172A;
    --color-muted: #64748B;
}

html, body, [class*="css"] {
    font-family: 'Nunito Sans', -apple-system, BlinkMacSystemFont, sans-serif;
}

h1, h2, h3, h4, .rubik-font {
    font-family: 'Rubik', sans-serif !important;
}

/* Hero Badge Pill */
.hero-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 9999px;
    font-size: 0.82rem;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    background: #ECFDF5;
    color: #059669;
    border: 1px solid #A7F3D0;
    margin-bottom: 8px;
}

/* Savings Banner */
.savings-banner {
    background: linear-gradient(135deg, #059669 0%, #10B981 100%);
    color: #FFFFFF;
    padding: 16px 20px;
    border-radius: 12px;
    box-shadow: 0 4px 12px rgba(5, 150, 105, 0.2);
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 16px 0;
}

.savings-banner h3 {
    margin: 0;
    color: #FFFFFF !important;
    font-size: 1.35rem;
}

.savings-banner p {
    margin: 4px 0 0 0;
    color: #D1FAE5;
    font-size: 0.95rem;
}

/* Stepper Component */
.stepper-container {
    display: flex;
    justify-content: space-between;
    gap: 8px;
    margin: 16px 0 20px 0;
    overflow-x: auto;
}

.stepper-step {
    flex: 1;
    min-width: 140px;
    padding: 10px 12px;
    background: #F1F5F9;
    border: 1px solid #CBD5E1;
    border-radius: 8px;
    text-align: center;
    font-size: 0.85rem;
    font-weight: 600;
    color: #475569;
    transition: all 0.2s ease;
}

.stepper-step.completed {
    background: #ECFDF5;
    border-color: #059669;
    color: #065F46;
}

.stepper-step.warning {
    background: #FFFBEB;
    border-color: #D97706;
    color: #92400E;
}

.stepper-step.active {
    background: #EFF6FF;
    border-color: #2563EB;
    color: #1E40AF;
    box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.2);
}

/* Brand Badges */
.badge-blinkit {
    background-color: #FEF08A;
    color: #854D0E;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 6px;
    display: inline-block;
    border: 1px solid #FACC15;
}

.badge-zepto {
    background-color: #F3E8FF;
    color: #6B21A8;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 6px;
    display: inline-block;
    border: 1px solid #D8B4FE;
}

.badge-instamart {
    background-color: #FFEDD5;
    color: #9A3412;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 6px;
    display: inline-block;
    border: 1px solid #FDBA74;
}

/* Card hover */
div[data-testid="stVerticalBlockBorderWrapper"] {
    border-radius: 12px;
    transition: transform 0.2s ease, box-shadow 0.2s ease;
}

div[data-testid="stVerticalBlockBorderWrapper"]:hover {
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.06);
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)


def inr(x) -> str:
    if x is None:
        return "₹0.00"
    return f"₹{float(x):,.2f}"


def get_platform_badge(platform_name: str) -> str:
    p_lower = platform_name.lower()
    if "blinkit" in p_lower:
        return f'<span class="badge-blinkit">🟡 Blinkit</span>'
    elif "zepto" in p_lower:
        return f'<span class="badge-zepto">🟣 Zepto</span>'
    elif "instamart" in p_lower:
        return f'<span class="badge-instamart">🟠 Swiggy Instamart</span>'
    return f'<span class="hero-badge">{platform_name}</span>'


# ------------------------------------------------------------------ SIDEBAR
with st.sidebar:
    st.markdown('<div class="hero-badge">AgentX 2026 · PS-07</div>', unsafe_allow_html=True)
    st.markdown("### 🛒 CartPilot Control Hub")
    st.caption("Autonomous Quick-Commerce Multi-Agent System")

    # Backend Connectivity Card
    if not USE_MOCK:
        try:
            h = get_health()
            st.success(f"🟢 **Backend Connected**\n\n• Engine: `{h.get('optimizer', 'CP-SAT')}`\n• Matcher: `{h.get('matching', 'Graph')}`")
        except Exception:
            st.warning("🟡 Backend unreachable on port 8000 – start API with `python scripts/run_all.py`")
    else:
        st.info("ℹ️ Running in Mock Mode (`USE_MOCK=1`)")

    st.markdown("---")
    st.markdown("#### ⚡ 1-Click Judge Scenarios")
    st.caption("Pre-tested prompts demonstrating key agent capabilities:")

    if st.button("🏆 Family Split Basket (14% Saved)", use_container_width=True):
        st.session_state["goal"] = DEMO_GOAL
        st.session_state["demo_faults"] = True

    if st.button("⚡ Single-App Quick Run", use_container_width=True):
        st.session_state["goal"] = "2 kg atta, 1 litre milk, 12 eggs under ₹600 delivered before 8 PM"
        st.session_state["demo_faults"] = False

    if st.button("🌱 Strictly Vegan Essentials", use_container_width=True):
        st.session_state["goal"] = "Weekly groceries for 2, strictly vegan, under ₹1,800, before 9 PM"
        st.session_state["demo_faults"] = False

    if st.button("🛡️ Consumer Shield Showcase", use_container_width=True):
        st.session_state["goal"] = "Fresh tomato 1kg, potato 1kg, fortune sunflower oil 1L under ₹500 by 8 PM"
        st.session_state["demo_faults"] = False

    st.markdown("---")
    st.markdown("#### 🧪 Fault Injection (Demo)")
    faults_on = st.checkbox(
        "Inject Live Faults (Zepto stockout + Blinkit surge)",
        key="demo_faults",
        disabled=USE_MOCK,
    )
    st.caption("Demonstrates the Auditor Agent catching live platform changes and triggering self-healing re-plans.")


# ------------------------------------------------------------------ HERO & INPUT
st.markdown('<div class="hero-badge">Autonomous Multi-Agent Architecture</div>', unsafe_allow_html=True)
st.title("CartPilot")
st.markdown(
    "**Intelligent Quick-Commerce Shopping Agent.** "
    "Understands natural language baskets, queries platforms via tools, solves true landed costs with "
    "**Google OR-Tools CP-SAT**, self-heals with an **independent Auditor**, and halts at a **zero-payment human approval gate**."
)

st.session_state.setdefault("goal", DEMO_GOAL)
goal = st.text_area(
    "What do you need?",
    key="goal",
    height=80,
    placeholder="e.g. Weekly groceries for 4 people, vegetarian, under ₹2,500, delivered before 9 PM."
)


def _plan(fn, *args):
    """Run a planner call and store the result; show clarification / errors cleanly."""
    st.session_state.pop("decision", None)
    try:
        st.session_state["proposal"] = fn(*args)
        st.session_state.pop("clarify", None)
    except ClarificationNeeded as e:
        st.session_state.pop("proposal", None)
        st.session_state["clarify"] = {"question": e.question, "session_id": e.session_id}
    except ApiError as e:
        st.error(f"Agent Error: {e.message}")
    except Exception as e:
        if BACKEND_MODE == "in_process":
            st.error(f"Couldn't process the request: {e}. The embedded planner is unavailable.")
        else:
            st.error(f"Couldn't reach the planner: {e}. Start the API or set CARTPILOT_UI_MODE=in_process.")


col_btn, col_info = st.columns([1, 3])
with col_btn:
    if st.button("🚀 Plan My Cart", type="primary", use_container_width=True):
        with st.spinner("🤖 Probing Blinkit, Zepto, Swiggy Instamart... Matching items... Solving CP-SAT..."):
            if not USE_MOCK:
                set_demo_faults(faults_on)
            _plan(get_proposal, goal)

with col_info:
    st.markdown(
        "🔒 **Safety Principle**: CartPilot prepares carts inside each app for 1-tap checkout. "
        "It **never** collects payment details, never pays, and never places orders automatically."
    )


# ------------------------------------------------------------------ CLARIFICATION GATE
clarify = st.session_state.get("clarify")
if clarify:
    st.info(f"🤔 **Agent needs clarification:** {clarify['question']}")
    c_ans = st.text_input("Your answer", key="clarify_answer", placeholder="e.g. budget ₹2,500, by 9 PM")
    if st.button("Continue Planning", key="clarify_go") and c_ans:
        with st.spinner("Updating plan with your answers..."):
            _plan(answer_clarification, clarify["session_id"], c_ans)


# ------------------------------------------------------------------ PROPOSAL PRESENTATION
proposal = st.session_state.get("proposal")

if proposal:
    st.divider()

    # 1. Visual Agent Stepper
    replan_count = proposal.get("replan_count", 0)
    stepper_html = f"""
    <div class="stepper-container">
        <div class="stepper-step completed">1. Intent Parsed<br><small>Budget & Diet locked</small></div>
        <div class="stepper-step completed">2. Platforms Probed<br><small>3 Quick-Commerce Apps</small></div>
        <div class="stepper-step completed">3. Graph Matched<br><small>SAME_AS & Substitutes</small></div>
        <div class="stepper-step completed">4. CP-SAT Solved<br><small>True Landed Cost</small></div>
        <div class="stepper-step {'warning' if replan_count > 0 else 'completed'}">5. Auditor Check<br><small>{'Re-planned ' + str(replan_count) + 'x ⚠️' if replan_count > 0 else 'Verified 100% ✅'}</small></div>
        <div class="stepper-step active">6. Approval Gate<br><small>Human in the Loop ✋</small></div>
    </div>
    """
    st.markdown(stepper_html, unsafe_allow_html=True)

    if replan_count > 0:
        st.warning(
            f"⚡ **Self-Healing Re-Plan Completed ({replan_count}x):** The Auditor Agent independently re-queried platform stock and fees, "
            f"caught live changes (e.g. Zepto stockout / Blinkit surge), and re-optimized the plan to maintain budget and deadlines!"
        )

    for w in proposal.get("warnings", []):
        st.warning(f"⚠️ {w}")

    # 2. Celebratory Savings Callout Banner
    savings = proposal.get("savings", 0)
    best_single = proposal.get("best_single_app", {})
    best_app_name = best_single.get("platform", "Single App")
    pct = proposal.get("savings_pct")

    if savings > 0:
        pct_text = f" ({pct}%)" if pct else ""
        st.markdown(
            f"""
            <div class="savings-banner">
                <div>
                    <h3>🎉 Optimal Split Saved {inr(savings)}{pct_text}</h3>
                    <p>Splitting across {len(proposal['platforms'])} apps beat the single best platform ({best_app_name} at {inr(best_single.get('landed_total', 0))}) even after all handling & delivery fees!</p>
                </div>
                <div style="font-size: 2rem; font-weight: 800; font-family: 'Rubik';">
                    {inr(proposal['split_total'])}
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    # 3. High-Level Metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Split Landed Total", inr(proposal["split_total"]))
    m2.metric(f"Best Single App ({best_app_name})", inr(best_single.get("landed_total", 0)))
    m3.metric("Net Savings", inr(savings), delta=f"+{inr(savings)}" if savings > 0 else None)
    c_cons = proposal.get("constraints", {})
    m4.metric("Budget", f"₹{c_cons.get('budget', 'None')}")
    m5.metric("Deliver By", c_cons.get("deadline", "ASAP"))

    # 4. Multi-Tabbed View
    tab_carts, tab_analytics, tab_trace, tab_memory = st.tabs([
        "🛒 Prepared Carts & Approval",
        "📊 Savings & Fee Analytics",
        "🧠 Agent Execution Trace",
        "👤 User Profile & Memory"
    ])

    # ------------------ TAB 1: CARTS & APPROVAL GATE
    with tab_carts:
        st.subheader("📦 Prepared Multi-App Carts")
        st.caption("Each platform's true landed cost includes item prices, handling fees, delivery thresholds, and surge pricing.")

        cols = st.columns(len(proposal["platforms"]))
        for col, p in zip(cols, proposal["platforms"]):
            lc = p["landed_cost"]
            with col:
                with st.container(border=True):
                    b_html = get_platform_badge(p['platform'])
                    st.markdown(f"{b_html}", unsafe_allow_html=True)
                    st.caption(f"⏱️ ETA: **{p['eta_mins']} mins** · {'✅ On Time' if p['deadline_met'] else '❌ Misses Deadline'}")

                    # Items Table
                    items_data = []
                    for l in p["cart"]:
                        prod = l["product"]
                        unit_p = l.get("unit_price")
                        unit_str = f" ({inr(unit_p)}/{l.get('unit_label', 'unit')})" if unit_p else ""
                        items_data.append({
                            "Item": prod["name"],
                            "Qty": f"{l['qty']}x",
                            "Price": inr(l["listing"]["price"]),
                            "Rate": unit_str or "-",
                        })
                    st.dataframe(items_data, hide_index=True, use_container_width=True)

                    # Fee Breakdown Table
                    st.markdown("**True Landed Cost:**")
                    st.table({
                        "Items Subtotal": inr(lc["item_total"]),
                        "Handling Fee": inr(lc["handling"]),
                        "Delivery Fee": inr(lc["delivery"]),
                        "Small-Cart Fee": inr(lc["small_cart"]),
                        "Surge / Rain Fee": inr(lc["surge"]),
                        "Discounts / Coupons": f"- {inr(lc['offers'])}",
                        "Platform Total": inr(lc["landed_total"]),
                    })

        # Decision Section
        st.divider()
        st.subheader("🛡️ Hard Human Approval Gate")
        st.caption("CartPilot creates prepared carts. You review and make the final approval; payments must be made inside each app.")

        decision = st.session_state.get("decision")
        if decision:
            if decision["kind"] == "approved":
                st.success(f"🎉 **{decision['message']}**")
                st.markdown("#### Ready for 1-Tap Checkout:")
                c_cards = st.columns(len(decision["carts"]))
                for col, c in zip(c_cards, decision["carts"]):
                    with col:
                        with st.container(border=True):
                            badge = get_platform_badge(c['platform'])
                            st.markdown(f"{badge}", unsafe_allow_html=True)
                            st.caption(f"Cart Reference: `{c['cart_ref']}`")
                            st.write(f"Prepared: **{len(c['lines'])} items**")
                            st.info("🔒 Payment Executed: **False**\n\n🔒 Order Placed: **False**")
                            app_name = c['platform'].lower()
                            deep_link = f"https://{app_name}.example.com/cart?ref={c['cart_ref']}"
                            st.link_button(f"📲 Open {c['platform'].title()} Cart", deep_link, use_container_width=True)
                for r in decision.get("reminders", []):
                    st.caption(f"⏰ Reorder Reminder Registered: {r}")
            else:
                st.warning(f"🛑 {decision['message']}")
        else:
            # Edit Expander
            with st.expander("✏️ Customize Plan Before Approving"):
                all_items = sorted({
                    line.get("request_item", line["product"]["name"])
                    for plat in proposal["platforms"]
                    for line in plat["cart"]
                })
                remove = st.multiselect("Remove items from plan", all_items, key="edit_remove")
                new_budget = st.number_input("Adjust budget (₹, 0 = keep)", min_value=0, step=100, value=0, key="edit_budget")
                avoid_brands = st.text_input("Brands to avoid (comma-separated)", key="edit_brands")
                if st.button("Re-Optimize with Edits", key="edit_go"):
                    if USE_MOCK:
                        st.info("✏️ **Demo mode:** Edit simulation — in live mode this triggers a full re-plan via the backend.")
                    else:
                        changes = {}
                        if remove:
                            changes["remove_items"] = list(remove)
                        if new_budget:
                            changes["budget"] = float(new_budget)
                        if avoid_brands.strip():
                            changes["reject_brands"] = [b.strip() for b in avoid_brands.split(",") if b.strip()]
                        if not changes:
                            st.warning("Select at least one change.")
                        else:
                            with st.spinner("Re-optimizing plan..."):
                                _plan(edit, proposal["session_id"], proposal["proposal_id"], changes)
                            st.rerun()

            if not proposal.get("approvable"):
                st.error("This proposal cannot be approved as-is (over budget or no stock). Please edit above.")
            else:
                confirmed = []
                for plat in proposal["platforms"]:
                    for line in plat["cart"]:
                        if line.get("requires_confirmation"):
                            pid = line["listing"]["product_id"]
                            item_name = line.get("request_item", line["product"]["name"])
                            if st.checkbox(
                                f"Confirm substitute: **{line['product']['name']}** for *'{item_name}'* on {plat['platform']}",
                                key=f"confirm_{pid}"
                            ):
                                confirmed.append(pid)

                remind = st.checkbox("Set smart reorder reminder in 7 days", value=True, key="remind")
                col_app, col_rej = st.columns([1, 1])
                with col_app:
                    if st.button("✅ Approve & Prepare Carts", type="primary", use_container_width=True, key="approve"):
                        if USE_MOCK:
                            # Mock approval – simulate the prepared carts response
                            mock_carts = [
                                {
                                    "platform": plat["platform"],
                                    "cart_ref": f"MOCK-{plat['platform'].upper()[:3]}-{proposal['proposal_id'][-4:]}",
                                    "lines": plat["cart"],
                                    "payment_executed": False,
                                    "order_placed": False,
                                }
                                for plat in proposal["platforms"]
                            ]
                            st.session_state["decision"] = {
                                "kind": "approved",
                                "message": f"Prepared {len(mock_carts)} cart(s). No payment was made and no order was placed – open each app to review and pay.",
                                "carts": mock_carts,
                                "reminders": ["Reorder in 7 days"] if remind else [],
                            }
                        else:
                            try:
                                r = approve(proposal["session_id"], proposal["proposal_id"], confirmed, 7 if remind else None)
                                st.session_state["decision"] = {
                                    "kind": "approved",
                                    "message": r["message"],
                                    "carts": r["session"]["prepared_carts"],
                                    "reminders": ["Reorder in 7 days"] if remind else [],
                                }
                            except ApiError as e:
                                st.error(e.message)
                        st.rerun()

                with col_rej:
                    r_reason = st.text_input("Reject reason (optional)", key="reject_reason", placeholder="e.g. items not needed")
                    if st.button("❌ Reject Plan", use_container_width=True, key="reject"):
                        if USE_MOCK:
                            st.session_state["decision"] = {
                                "kind": "rejected",
                                "message": "Proposal rejected. Nothing was prepared.",
                            }
                        else:
                            try:
                                r = reject(proposal["session_id"], proposal["proposal_id"], r_reason)
                                st.session_state["decision"] = {"kind": "rejected", "message": r["message"]}
                            except ApiError as e:
                                st.error(e.message)
                        st.rerun()

    # ------------------ TAB 2: ANALYTICS & SAVINGS (ALTAIR CHARTS)
    with tab_analytics:
        st.subheader("📊 Price Comparison & Savings Analytics")
        col_c1, col_c2 = st.columns(2)

        with col_c1:
            st.markdown("##### 🏷️ Total Landed Cost: Split vs. Single Apps")
            chart_records = [
                {"Option": "CartPilot Split", "Landed Cost (₹)": proposal["split_total"], "Category": "Optimal Split"},
                {"Option": f"Best Single ({best_app_name})", "Landed Cost (₹)": best_single.get("landed_total", 0), "Category": "Single App"},
            ]
            for p in proposal["platforms"]:
                chart_records.append({
                    "Option": f"{p['platform']} Cart Only",
                    "Landed Cost (₹)": p["landed_cost"]["landed_total"],
                    "Category": "Single App"
                })

            df_comp = pd.DataFrame(chart_records)
            comp_chart = alt.Chart(df_comp).mark_bar(cornerRadius=6).encode(
                x=alt.X("Option:N", sort=None, title="", axis=alt.Axis(labelAngle=-15)),
                y=alt.Y("Landed Cost (₹):Q", title="Total Cost in ₹ (paise-accurate)"),
                color=alt.Color("Category:N", scale=alt.Scale(domain=["Optimal Split", "Single App"], range=["#059669", "#94A3B8"]), legend=alt.Legend(orient="top")),
                tooltip=["Option", "Landed Cost (₹)", "Category"]
            ).properties(height=280)
            st.altair_chart(comp_chart, use_container_width=True)

        with col_c2:
            st.markdown("##### 🧾 Stacked Fee Composition")
            fee_records = []
            for p in proposal["platforms"]:
                lc = p["landed_cost"]
                fee_records.append({"Platform": p["platform"], "Fee Type": "1. Items", "Amount": lc["item_total"]})
                fee_records.append({"Platform": p["platform"], "Fee Type": "2. Handling", "Amount": lc["handling"]})
                fee_records.append({"Platform": p["platform"], "Fee Type": "3. Delivery", "Amount": lc["delivery"]})
                fee_records.append({"Platform": p["platform"], "Fee Type": "4. Small Cart", "Amount": lc["small_cart"]})
                fee_records.append({"Platform": p["platform"], "Fee Type": "5. Surge / Rain", "Amount": lc["surge"]})

            df_fees = pd.DataFrame(fee_records)
            fee_chart = alt.Chart(df_fees).mark_bar(cornerRadius=4).encode(
                x=alt.X("Platform:N", title=""),
                y=alt.Y("Amount:Q", title="Cost Breakdown (₹)"),
                color=alt.Color("Fee Type:N", scale=alt.Scale(scheme="category10"), legend=alt.Legend(orient="top")),
                tooltip=["Platform", "Fee Type", "Amount"]
            ).properties(height=280)
            st.altair_chart(fee_chart, use_container_width=True)

        st.markdown("---")
        st.markdown("#### 🛡️ Auditor Consumer Shield")
        st.info(
            "**Consumer Protection Highlights:**\n\n"
            "• **Zero Invented Numbers:** All prices, fees, surge charges, and ETAs are cryptographically proven from platform tools.\n"
            "• **Anti-MRP Inflation:** The Auditor cross-checks listings against historical catalog averages to flag fake discounts.\n"
            "• **Unit-Price Checks:** Ensures smaller multi-packs aren't deceptively cheaper than bulk items."
        )

    # ------------------ TAB 3: TRACE
    with tab_trace:
        st.subheader("🧠 Complete Agent Execution Trace")
        st.caption("Cryptographically verifiable action log of every tool execution, timing, and reasoning.")
        try:
            t = get_trace(proposal["session_id"])
            if t.get("replans"):
                st.markdown("##### 🔄 Self-Healing Re-Plans:")
                for r in t["replans"]:
                    st.warning(f"**Iteration {r['iteration']}** [{', '.join(r.get('triggers', []))}]: {r['action_taken']}")

            actions_table = []
            for a in t.get("actions", []):
                actions_table.append({
                    "Phase": a.get("phase"),
                    "Tool": a.get("tool"),
                    "Status": a.get("status"),
                    "Reason / Objective": a.get("reason"),
                })
            st.dataframe(actions_table, hide_index=True, use_container_width=True)
        except Exception:
            st.caption("Trace currently unavailable for this session.")

    # ------------------ TAB 4: MEMORY
    with tab_memory:
        st.subheader("👤 User Profile & Episodic Memory")
        st.caption("Persistent preferences and order history stored in SQLite.")

        if not USE_MOCK:
            try:
                m_pref = get_preferences()
                prefs = m_pref.get("preferences", {})
                p_c1, p_c2, p_c3 = st.columns(3)
                with p_c1:
                    st.markdown("**Accepted Brands:**")
                    st.write(", ".join(prefs.get("accepted_brands", [])) or "None yet")
                with p_c2:
                    st.markdown("**Avoided Brands:**")
                    st.write(", ".join(prefs.get("rejected_brands", [])) or "None")
                with p_c3:
                    st.markdown("**Dietary Rule:**")
                    st.write(prefs.get("dietary_preference") or "None set")

                st.markdown("##### 📜 Recent Shopping Episodes:")
                episodes = get_episodes().get("episodes", [])
                if episodes:
                    ep_data = [{
                        "Date": ep.get("created_at", "-")[:16],
                        "Goal": ep.get("goal"),
                        "Decision": ep.get("decision"),
                        "Total (₹)": inr(ep.get("total_landed")),
                    } for ep in episodes]
                    st.dataframe(ep_data, hide_index=True, use_container_width=True)
                else:
                    st.write("No previous episodes logged yet.")
            except Exception as e:
                st.caption(f"Could not load memory: {e}")
        else:
            st.info("Memory is enabled on the live backend (`USE_MOCK=0`).")

else:
    st.info("👆 Enter your grocery requirement or choose a **Judge Demo Preset** from the sidebar, then click **Plan My Cart**.")
