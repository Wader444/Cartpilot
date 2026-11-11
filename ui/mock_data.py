"""Mock data for Streamlit Cloud / USE_MOCK=1 mode.

All fields match the exact shape ui/app.py expects so the UI renders
completely without a backend. Names follow the frozen contract.
"""
import uuid


def _line(platform, sku_id, name, brand, qv, qu, category, diet, price, mrp, eta, qty,
          request_item=None, unit_price=None, unit_label=None, requires_confirmation=False,
          is_substitute=False):
    return {
        "product": {
            "sku_id": sku_id,
            "name": name,
            "brand": brand,
            "quantity_value": qv,
            "quantity_unit": qu,
            "category": category,
            "diet_tag": diet,
        },
        "listing": {
            "platform": platform,
            "product_id": sku_id,
            "price": price,
            "mrp": mrp,
            "in_stock": True,
            "eta_mins": eta,
        },
        "qty": qty,
        "line_total": round(price * qty, 2),
        "request_item": request_item or name,
        "unit_price": unit_price,
        "unit_label": unit_label,
        "requires_confirmation": requires_confirmation,
        "is_substitute": is_substitute,
    }


_SESSION_ID = "mock-session-001"
_PROPOSAL_ID = "mock-proposal-001"

SAMPLE_PROPOSAL = {
    # ── identity ──────────────────────────────────────────────────────────
    "session_id": _SESSION_ID,
    "proposal_id": _PROPOSAL_ID,
    "version": 1,
    "status": "pending_approval",

    # ── goal & constraints ────────────────────────────────────────────────
    "goal": "Weekly groceries for 4 people, vegetarian, under \u20b92,500, delivered before 9 PM.",
    "constraints": {"budget": 2500, "deadline": "21:00"},

    # ── agent state ───────────────────────────────────────────────────────
    "replan_count": 1,          # Auditor found Zepto stockout → self-healed once
    "approvable": True,
    "within_budget": True,
    "deadline_met": True,
    "savings_pct": "14",
    "summary": "Split across Blinkit (staples) and Zepto (produce & dairy) saves \u20b9344 vs. single-app checkout on Blinkit.",

    # ── platforms ─────────────────────────────────────────────────────────
    "platforms": [
        {
            "platform": "Blinkit",
            "platform_id": "blinkit",
            "eta_mins": 18,
            "deadline_met": True,
            "cart": [
                _line("Blinkit", "SKU001", "Aashirvaad Atta 5kg", "Aashirvaad",
                      5, "kg", "staples", "veg", 265, 285, 18, 1,
                      request_item="atta 5kg",
                      unit_price=53.0, unit_label="kg"),
                _line("Blinkit", "SKU002", "Amul Toned Milk 1L", "Amul",
                      1, "l", "dairy", "veg", 68, 68, 18, 2,
                      request_item="milk 1 litre",
                      unit_price=68.0, unit_label="l"),
                _line("Blinkit", "SKU003", "Fortune Sunflower Oil 1L", "Fortune",
                      1, "l", "staples", "veg", 145, 160, 18, 1,
                      request_item="sunflower oil 1L",
                      unit_price=145.0, unit_label="l"),
                _line("Blinkit", "SKU004", "Tata Salt 1kg", "Tata",
                      1, "kg", "staples", "veg", 22, 24, 18, 2,
                      request_item="salt 1kg",
                      unit_price=22.0, unit_label="kg"),
                _line("Blinkit", "SKU005", "Basmati Rice 5kg", "India Gate",
                      5, "kg", "staples", "veg", 390, 420, 18, 1,
                      request_item="basmati rice 5kg",
                      unit_price=78.0, unit_label="kg"),
            ],
            # 265 + 2*68 + 145 + 2*22 + 390 = 980; 980 + 2 + 0 + 0 + 8 - 50 = 940
            "landed_cost": {
                "item_total": 980,
                "handling": 2,
                "delivery": 0,
                "small_cart": 0,
                "surge": 8,
                "offers": 50,
                "landed_total": 940,
            },
        },
        {
            "platform": "Zepto",
            "platform_id": "zepto",
            "eta_mins": 22,
            "deadline_met": True,
            "cart": [
                _line("Zepto", "SKU010", "Tomato (Local) 1kg", "Local Farm",
                      1, "kg", "produce", "veg", 32, 40, 22, 1,
                      request_item="tomatoes 1kg",
                      unit_price=32.0, unit_label="kg"),
                _line("Zepto", "SKU011", "Amul Paneer 200g", "Amul",
                      200, "g", "dairy", "veg", 82, 95, 22, 2,
                      request_item="paneer 200g",
                      unit_price=410.0, unit_label="kg"),
                _line("Zepto", "SKU012", "Fresh Spinach 250g", "Local Farm",
                      250, "g", "produce", "veg", 25, 30, 22, 1,
                      request_item="spinach",
                      unit_price=100.0, unit_label="kg"),
                _line("Zepto", "SKU013", "Dahi / Curd 400g", "Nestle",
                      400, "g", "dairy", "veg", 48, 52, 22, 1,
                      request_item="curd 400g",
                      requires_confirmation=True,
                      is_substitute=True,
                      unit_price=120.0, unit_label="kg"),
            ],
            # 32 + 2*82 + 25 + 48 = 269; 269 + 2 + 20 + 0 + 0 - 10 = 281
            "landed_cost": {
                "item_total": 269,
                "handling": 2,
                "delivery": 20,
                "small_cart": 0,
                "surge": 0,
                "offers": 10,
                "landed_total": 281,
            },
        },
        {
            "platform": "Swiggy Instamart",
            "platform_id": "instamart",
            "eta_mins": 35,
            "deadline_met": True,
            "cart": [
                _line("Swiggy Instamart", "SKU020", "Haldiram Namkeen 400g", "Haldiram",
                      400, "g", "snacks", "veg", 110, 120, 35, 1,
                      request_item="namkeen snacks",
                      unit_price=275.0, unit_label="kg"),
                _line("Swiggy Instamart", "SKU021", "Real Fruit Juice 1L", "Real",
                      1, "l", "beverages", "veg", 90, 100, 35, 2,
                      request_item="fruit juice 1L",
                      unit_price=90.0, unit_label="l"),
            ],
            # 110 + 2*90 = 290; 290 + 10 + 30 + 0 + 0 - 20 = 310
            "landed_cost": {
                "item_total": 290,
                "handling": 10,
                "delivery": 30,
                "small_cart": 0,
                "surge": 0,
                "offers": 20,
                "landed_total": 310,
            },
        },
    ],

    # ── totals ────────────────────────────────────────────────────────────
    # split = 940 + 281 + 310 = 1531
    # best single (Blinkit for all items) ≈ 1875
    "split_total": 1531,
    "best_single_app": {"platform": "Blinkit", "landed_total": 1875},
    "savings": 344,

    # ── audit trail ───────────────────────────────────────────────────────
    "last_checked": "2026-11-11 18:42",
    "warnings": [
        "Auditor: Zepto curd (Amul) went out of stock after first check — substituted with Nestle Dahi (confirm required).",
        "Auditor: Blinkit surge fee spiked \u20b98 between planning and verification — plan re-optimised.",
    ],

    # ── extra UI fields ───────────────────────────────────────────────────
    "requires_confirmation": ["SKU013"],   # product_ids needing user sign-off
    "unresolved_items": [],
    "warning_details": [],
}
