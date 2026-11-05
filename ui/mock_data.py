# SAMPLE DATA ONLY. Names follow the frozen contract (Product, Listing,
# landed-cost components). PLACEHOLDER fields are not in the contract yet.

def line(platform, sku_id, name, brand, qv, qu, category, diet, price, mrp, eta, qty):
    return {
        "product": {"sku_id": sku_id, "name": name, "brand": brand,
                    "quantity_value": qv, "quantity_unit": qu,
                    "category": category, "diet_tag": diet},
        "listing": {"platform": platform, "product_id": sku_id,
                    "price": price, "mrp": mrp, "in_stock": True,
                    "eta_mins": eta},
        "qty": qty,  # PLACEHOLDER
    }

SAMPLE_PROPOSAL = {
    "goal": "Weekly groceries for 2, veg, under ₹1,200, before 8 PM",
    "constraints": {"budget": 1200, "deadline": "20:00"},
    "platforms": [
        {
            "platform": "Blinkit",
            "eta_mins": 18,
            "deadline_met": True,  # PLACEHOLDER
            "cart": [
                line("Blinkit", "SKU001", "Atta", "Aashirvaad", 5, "kg", "staples", "veg", 265, 285, 18, 1),
                line("Blinkit", "SKU002", "Toned Milk", "Amul", 1, "l", "dairy", "veg", 68, 68, 18, 2),
                line("Blinkit", "SKU003", "Sunflower Oil", "Fortune", 1, "l", "staples", "veg", 145, 160, 18, 1),
            ],
            # 265 + 2*68 + 145 = 546; 546 + 2 + 0 + 0 + 3 - 15 = 536
            "landed_cost": {"item_total": 546, "handling": 2, "delivery": 0,
                            "small_cart": 0, "surge": 3, "offers": 15,
                            "landed_total": 536},
        },
        {
            "platform": "Zepto",
            "eta_mins": 22,
            "deadline_met": True,  # PLACEHOLDER
            "cart": [
                line("Zepto", "SKU010", "Tomato", "Local", 1, "kg", "produce", "veg", 32, 40, 22, 1),
                line("Zepto", "SKU011", "Paneer", "Amul", 200, "g", "dairy", "veg", 82, 95, 22, 1),
            ],
            # 32 + 82 = 114; 114 + 2 + 20 + 0 + 0 - 0 = 136
            "landed_cost": {"item_total": 114, "handling": 2, "delivery": 20,
                            "small_cart": 0, "surge": 0, "offers": 0,
                            "landed_total": 136},
        },
    ],
    # PLACEHOLDERS for the Proposal summary:
    # split = 536 + 136 = 672; best single app (Blinkit for all five items) = 701
    "split_total": 672,
    "best_single_app": {"platform": "Blinkit", "landed_total": 701},
    "savings": 29,
    "last_checked": "2026-11-05 18:42",
    "warnings": ["Auditor: Zepto delivery fee is higher than on the first check."],
}