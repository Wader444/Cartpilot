import streamlit as st
from api_client import get_proposal

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

goal = st.text_area("What do you need?",
                    value="Weekly groceries for 2, veg, under ₹1,200, before 8 PM",
                    height=80)

if st.button("Plan my cart", type="primary"):
    with st.spinner("Planning..."):
        try:
            st.session_state["proposal"] = get_proposal(goal)
        except Exception:
            st.error("Couldn't reach the planner. Please try again.")

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