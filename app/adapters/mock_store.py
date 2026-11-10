"""TEMPORARY mock store used until Member B's mock store API is ready.

* Same interface as Member B's PlatformAdapter, so it can be swapped out
  by setting CARTPILOT_STORE_URL (HTTP adapter) or replacing this module.
* Deterministic: the same query always gives the same prices, so tests and
  demos are repeatable.
* Fault injection for demos and tests: out-of-stock, price change, fee
  spike, ETA delay, timeout, invalid response, empty results.

All figures are MOCK data – realistic in shape, not live prices.
"""
from __future__ import annotations

import threading
import zlib
from dataclasses import dataclass, field
from typing import Optional

from app.core.models import DietTag, Platform

# sku, generic, name, brand, qty, unit, category, diet, base price (₹), mrp factor
_C = [
    ("atta-aash-5kg", "atta", "Aashirvaad Shudh Chakki Atta 5 kg", "Aashirvaad", 5, "kg", "staples", "vegan", 285, 1.12),
    ("atta-aash-10kg", "atta", "Aashirvaad Shudh Chakki Atta 10 kg", "Aashirvaad", 10, "kg", "staples", "vegan", 545, 1.10),
    ("atta-pills-1kg", "atta", "Pillsbury Chakki Fresh Atta 1 kg", "Pillsbury", 1, "kg", "staples", "vegan", 62, 1.08),
    ("atta-pills-5kg", "atta", "Pillsbury Chakki Fresh Atta 5 kg", "Pillsbury", 5, "kg", "staples", "vegan", 270, 1.10),
    ("rice-ig-1kg", "rice", "India Gate Basmati Rice Rozana 1 kg", "India Gate", 1, "kg", "staples", "vegan", 98, 1.15),
    ("rice-daawat-1kg", "rice", "Daawat Rozana Super Basmati 1 kg", "Daawat", 1, "kg", "staples", "vegan", 92, 1.12),
    ("rice-sona-5kg", "rice", "Fortune Sona Masoori Rice 5 kg", "Fortune", 5, "kg", "staples", "vegan", 365, 1.15),
    ("dal-tata-toor-1kg", "toor dal", "Tata Sampann Unpolished Toor Dal 1 kg", "Tata Sampann", 1, "kg", "staples", "vegan", 182, 1.15),
    ("dal-moong-500g", "moong dal", "Tata Sampann Moong Dal 500 g", "Tata Sampann", 500, "g", "staples", "vegan", 82, 1.10),
    ("sugar-madhur-1kg", "sugar", "Madhur Pure Sugar 1 kg", "Madhur", 1, "kg", "staples", "vegan", 54, 1.10),
    ("salt-tata-1kg", "salt", "Tata Salt Iodised 1 kg", "Tata", 1, "kg", "staples", "vegan", 28, 1.00),
    ("oil-fortune-1l", "oil", "Fortune Sunlite Refined Sunflower Oil 1 L", "Fortune", 1, "litre", "staples", "vegan", 152, 1.12),
    ("oil-saffola-1l", "oil", "Saffola Gold Blended Oil 1 L", "Saffola", 1, "litre", "staples", "vegan", 189, 1.65),
    ("milk-amul-1l", "milk", "Amul Taaza Toned Milk 1 L", "Amul", 1, "litre", "dairy", "veg", 66, 1.00),
    ("milk-amul-500ml", "milk", "Amul Taaza Toned Milk 500 ml", "Amul", 500, "ml", "dairy", "veg", 34, 1.00),
    ("milk-md-1l", "milk", "Mother Dairy Toned Milk 1 L", "Mother Dairy", 1, "litre", "dairy", "veg", 64, 1.00),
    ("milk-nandini-500ml", "milk", "Nandini Toned Milk 500 ml", "Nandini", 500, "ml", "dairy", "veg", 27, 1.00),
    ("milk-oat-1l", "oat milk", "Oatly Oat Drink 1 L", "Oatly", 1, "litre", "dairy alternatives", "vegan", 299, 1.05),
    ("curd-amul-400g", "curd", "Amul Masti Dahi 400 g", "Amul", 400, "g", "dairy", "veg", 35, 1.00),
    ("curd-md-400g", "curd", "Mother Dairy Classic Dahi 400 g", "Mother Dairy", 400, "g", "dairy", "veg", 38, 1.00),
    ("paneer-amul-200g", "paneer", "Amul Malai Paneer 200 g", "Amul", 200, "g", "dairy", "veg", 90, 1.05),
    ("paneer-milkymist-200g", "paneer", "Milky Mist Paneer 200 g", "Milky Mist", 200, "g", "dairy", "veg", 95, 1.05),
    ("tofu-200g", "tofu", "Urban Platter Firm Tofu 200 g", "Urban Platter", 200, "g", "dairy alternatives", "vegan", 99, 1.10),
    ("butter-amul-100g", "butter", "Amul Butter Pasteurised 100 g", "Amul", 100, "g", "dairy", "veg", 58, 1.00),
    ("bread-brit-400g", "bread", "Britannia Whole Wheat Bread 400 g", "Britannia", 1, "pcs", "bakery", "veg", 50, 1.00),
    ("bread-modern-400g", "bread", "Modern Sandwich Bread 400 g", "Modern", 1, "pcs", "bakery", "veg", 45, 1.00),
    ("eggs-6", "eggs", "Farm Fresh White Eggs 6 pcs", "Farm Fresh", 6, "pcs", "eggs", "egg", 54, 1.05),
    ("eggs-12", "eggs", "Farm Fresh White Eggs 12 pcs", "Farm Fresh", 12, "pcs", "eggs", "egg", 99, 1.05),
    ("chicken-500g", "chicken", "Licious Chicken Curry Cut 500 g", "Licious", 500, "g", "meat", "non_veg", 189, 1.10),
    ("tomato-1kg", "tomato", "Fresh Tomato 1 kg", "Farm Fresh", 1, "kg", "produce", "vegan", 38, 1.10),
    ("onion-1kg", "onion", "Fresh Onion 1 kg", "Farm Fresh", 1, "kg", "produce", "vegan", 42, 1.10),
    ("potato-1kg", "potato", "Fresh Potato 1 kg", "Farm Fresh", 1, "kg", "produce", "vegan", 36, 1.10),
    ("banana-6", "banana", "Robusta Banana 6 pcs", "Farm Fresh", 6, "pcs", "produce", "vegan", 45, 1.10),
    ("apple-4", "apple", "Shimla Apple 4 pcs", "Farm Fresh", 4, "pcs", "produce", "vegan", 120, 1.10),
    ("spinach-250g", "spinach", "Fresh Palak 250 g", "Farm Fresh", 250, "g", "produce", "vegan", 25, 1.10),
    ("tea-tata-500g", "tea", "Tata Tea Premium 500 g", "Tata Tea", 500, "g", "beverages", "vegan", 265, 1.08),
    ("tea-redlabel-500g", "tea", "Brooke Bond Red Label 500 g", "Red Label", 500, "g", "beverages", "vegan", 280, 1.06),
    ("coffee-bru-100g", "coffee", "Bru Instant Coffee 100 g", "Bru", 100, "g", "beverages", "vegan", 230, 1.05),
    ("biscuit-parleg-800g", "biscuits", "Parle-G Gold Biscuits 800 g", "Parle", 800, "g", "snacks", "veg", 95, 1.05),
    ("biscuit-goodday-600g", "biscuits", "Britannia Good Day Cashew 600 g", "Britannia", 600, "g", "snacks", "veg", 120, 1.08),
    ("chips-lays-90g", "chips", "Lay's Classic Salted 90 g", "Lay's", 90, "g", "snacks", "vegan", 30, 1.00),
    ("maggi-12", "noodles", "Maggi 2-Minute Masala Noodles 12-pack", "Maggi", 12, "pcs", "snacks", "veg", 168, 1.00),
    ("detergent-surf-1kg", "detergent", "Surf Excel Easy Wash 1 kg", "Surf Excel", 1, "kg", "household", "vegan", 135, 1.12),
    ("dishwash-vim-500ml", "dishwash", "Vim Dishwash Liquid Gel 500 ml", "Vim", 500, "ml", "household", "vegan", 115, 1.10),
]

CATALOG = [
    dict(sku_id=s, generic_name=g, name=n, brand=b, quantity_value=q, quantity_unit=u,
         category=c, diet_tag=DietTag(d), base_price=p, mrp_factor=m)
    for s, g, n, b, q, u, c, d, p, m in _C
]
CATALOG_BY_SKU = {c["sku_id"]: c for c in CATALOG}
KNOWN_BRANDS = sorted({c["brand"] for c in CATALOG})

SYNONYMS = {
    "wheat flour": "atta", "aata": "atta", "flour": "atta",
    "dahi": "curd", "yogurt": "curd", "yoghurt": "curd",
    "egg": "eggs", "anda": "eggs",
    "tomatoes": "tomato", "onions": "onion", "potatoes": "potato", "aloo": "potato", "pyaz": "onion",
    "bananas": "banana", "apples": "apple", "palak": "spinach",
    "cooking oil": "oil", "sunflower oil": "oil", "refined oil": "oil",
    "dal": "toor dal", "toor": "toor dal", "arhar dal": "toor dal",
    "biscuit": "biscuits", "maggi": "noodles", "noodle": "noodles",
    "detergent powder": "detergent", "washing powder": "detergent",
    "dishwash liquid": "dishwash", "dish wash": "dishwash", "chai": "tea", "chai patti": "tea",
}

PLATFORM_FACTOR = {Platform.BLINKIT: 1.00, Platform.ZEPTO: 0.985, Platform.INSTAMART: 1.01}

# Each app is cheapest in a different aisle, so splitting can pay off – but only
# when the saving beats the extra handling/delivery fees.
CATEGORY_FACTOR = {
    Platform.BLINKIT: {"produce": 0.80, "bakery": 0.88, "snacks": 0.90,
                       "dairy": 1.08, "staples": 1.08, "beverages": 1.06, "household": 1.05},
    Platform.ZEPTO: {"dairy": 0.88, "beverages": 0.88, "eggs": 0.90, "dairy alternatives": 0.92,
                     "produce": 1.12, "staples": 1.08, "household": 1.06},
    Platform.INSTAMART: {"staples": 0.84, "household": 0.86, "meat": 0.92,
                         "produce": 1.10, "dairy": 1.08, "snacks": 1.06, "bakery": 1.06},
}

FEE_SCHEDULES = {
    Platform.BLINKIT: dict(handling_fee=9, delivery_fee=30, free_delivery_threshold=199,
                           small_cart_fee=20, small_cart_threshold=99, surge_fee=0),
    Platform.ZEPTO: dict(handling_fee=11, delivery_fee=25, free_delivery_threshold=249,
                         small_cart_fee=35, small_cart_threshold=149, surge_fee=0),
    Platform.INSTAMART: dict(handling_fee=6, delivery_fee=35, free_delivery_threshold=299,
                             small_cart_fee=25, small_cart_threshold=149, surge_fee=0),
}

OFFERS = {
    Platform.BLINKIT: [
        dict(offer_id="BLK50", description="₹50 off on orders above ₹999", discount_type="flat", value=50, min_cart_value=999),
        dict(offer_id="BLKHDFC", description="10% off up to ₹75 with HDFC cards", discount_type="percent",
             value=10, min_cart_value=500, max_discount=75, requires_payment_method="HDFC card"),
    ],
    Platform.ZEPTO: [
        dict(offer_id="ZEP75", description="₹75 off on orders above ₹1,199", discount_type="flat", value=75, min_cart_value=1199),
        dict(offer_id="ZEP5", description="5% off up to ₹40 on orders above ₹499", discount_type="percent",
             value=5, min_cart_value=499, max_discount=40),
    ],
    Platform.INSTAMART: [
        dict(offer_id="IM60", description="₹60 off on orders above ₹799", discount_type="flat", value=60, min_cart_value=799),
        dict(offer_id="IMAXIS", description="₹100 off with Axis cards above ₹1,000", discount_type="flat",
             value=100, min_cart_value=1000, requires_payment_method="Axis card"),
    ],
}

BASE_ETA = {Platform.BLINKIT: 12, Platform.ZEPTO: 10, Platform.INSTAMART: 18}


def _h(*parts: str) -> int:
    return zlib.crc32("|".join(parts).encode())


@dataclass
class Fault:
    kind: str                      # out_of_stock | price_change | fee_spike | eta | timeout | invalid_response | empty_results
    platform: Platform
    sku_id: Optional[str] = None
    after_calls: int = 0           # fault becomes active after this many matching calls
    value: Optional[float] = None  # new price / extra fee / new ETA
    calls: int = 0

    def hit(self) -> bool:
        self.calls += 1
        return self.calls > self.after_calls


@dataclass
class MockStore:
    faults: list[Fault] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    cart_counter: int = 0

    # ------------------------------------------------------------------ faults
    def add_fault(self, fault: Fault) -> None:
        with self.lock:
            self.faults.append(fault)

    def clear_faults(self) -> None:
        with self.lock:
            self.faults.clear()

    def _faults(self, kind: str, platform: Platform, sku: Optional[str] = None) -> list[Fault]:
        return [f for f in self.faults if f.kind == kind and f.platform == platform
                and (f.sku_id is None or sku is None or f.sku_id == sku)]

    def check_platform_fault(self, platform: Platform, kind: str) -> Optional[Fault]:
        with self.lock:
            for f in self._faults(kind, platform):
                if f.hit():
                    return f
        return None

    # ------------------------------------------------------------------ data
    def base_price(self, sku: str, platform: Platform) -> float:
        c = CATALOG_BY_SKU[sku]
        delta = ((_h(sku, platform.value) % 9) - 4) / 100.0  # ±4 %
        cat = CATEGORY_FACTOR[platform].get(c["category"], 1.0)
        return float(round(c["base_price"] * PLATFORM_FACTOR[platform] * cat * (1 + delta)))

    def mrp(self, sku: str, platform: Platform) -> float:
        c = CATALOG_BY_SKU[sku]
        return float(max(round(c["base_price"] * c["mrp_factor"]), self.base_price(sku, platform)))

    def base_in_stock(self, sku: str, platform: Platform) -> bool:
        return _h(sku, platform.value, "stock") % 13 != 0

    def listing(self, sku: str, platform: Platform) -> dict:
        c = CATALOG_BY_SKU[sku]
        price = self.base_price(sku, platform)
        in_stock = self.base_in_stock(sku, platform)
        with self.lock:
            for f in self._faults("price_change", platform, sku):
                if f.sku_id == sku and f.hit() and f.value is not None:
                    price = float(f.value)
            for f in self._faults("out_of_stock", platform, sku):
                if f.sku_id == sku and f.hit():
                    in_stock = False
        product = {k: c[k] for k in ("sku_id", "name", "brand", "quantity_value", "quantity_unit",
                                     "category", "generic_name")}
        product["diet_tag"] = c["diet_tag"].value
        return dict(platform=platform.value, product_id=sku, product=product, price=price,
                    mrp=max(self.mrp(sku, platform), price), in_stock=in_stock, eta_mins=BASE_ETA[platform])

    def search(self, platform: Platform, query: str) -> list[dict]:
        q = " ".join(query.lower().split())
        q = SYNONYMS.get(q, q)
        tokens = [t for t in q.split() if len(t) > 1]
        hits = []
        for c in CATALOG:
            hay = f"{c['name']} {c['generic_name']} {c['brand']} {c['category']}".lower()
            if c["generic_name"] == q or (tokens and all(t in hay for t in tokens)):
                hits.append(self.listing(c["sku_id"], platform))
        return hits

    def cart_value(self, platform: Platform, cart: list[dict]) -> float:
        return round(sum(self.base_or_faulted_price(platform, l["product_id"]) * l["quantity"] for l in cart), 2)

    def base_or_faulted_price(self, platform: Platform, sku: str) -> float:
        # price faults that are already active also apply to cart valuation
        price = self.base_price(sku, platform)
        for f in self._faults("price_change", platform, sku):
            if f.sku_id == sku and f.calls > f.after_calls and f.value is not None:
                price = float(f.value)
        return price

    def fees(self, platform: Platform, cart_value: float) -> dict:
        sched = dict(FEE_SCHEDULES[platform])
        spike = self.check_platform_fault(platform, "fee_spike")
        if spike is not None:
            sched["surge_fee"] = sched.get("surge_fee", 0) + float(spike.value or 30)
        handling = sched["handling_fee"]
        delivery = 0.0 if (sched["free_delivery_threshold"] is not None
                           and cart_value >= sched["free_delivery_threshold"]) else sched["delivery_fee"]
        small = sched["small_cart_fee"] if (sched["small_cart_threshold"] is not None
                                            and 0 < cart_value < sched["small_cart_threshold"]) else 0.0
        surge = sched["surge_fee"]
        total = handling + delivery + small + surge
        return dict(platform=platform.value, cart_value=cart_value, handling=float(handling), delivery=float(delivery),
                    small_cart=float(small), surge=float(surge), total=float(total), schedule=sched)

    def offers(self, platform: Platform, cart_value: float) -> list[dict]:
        out = []
        for o in OFFERS[platform]:
            o = dict(o)
            o.setdefault("max_discount", None)
            o.setdefault("requires_payment_method", None)
            applicable = cart_value >= o["min_cart_value"] and not o["requires_payment_method"]
            if o["discount_type"] == "flat":
                amount = o["value"]
            else:
                amount = cart_value * o["value"] / 100
                if o["max_discount"] is not None:
                    amount = min(amount, o["max_discount"])
            o.update(platform=platform.value, applicable=applicable,
                     discount_amount=round(float(amount), 2) if applicable else 0.0)
            out.append(o)
        return out

    def eta(self, platform: Platform, cart: list[dict]) -> int:
        eta = BASE_ETA[platform] + sum(l["quantity"] for l in cart) // 8
        f = self.check_platform_fault(platform, "eta")
        if f is not None and f.value is not None:
            eta = int(f.value)
        return int(eta)

    def price_history(self, platform: Platform, sku: str, days: int = 28) -> list[dict]:
        """Seeded history around each app's normal (non-sale) price.

        Category sales (see CATEGORY_FACTOR) therefore show up as genuine
        discounts, while Saffola Gold – whose MRP is inflated and whose price
        never moved – shows up as a fake discount.
        """
        c = CATALOG_BY_SKU[sku]
        if c["mrp_factor"] >= 1.5:  # seeded fake discount: price has always been this low
            base = self.base_price(sku, platform)
        else:
            delta = ((_h(sku, platform.value) % 9) - 4) / 100.0
            base = float(round(c["base_price"] * PLATFORM_FACTOR[platform] * (1 + delta)))
        hist = []
        for d in range(days, 0, -1):
            wiggle = ((_h(sku, platform.value, str(d)) % 7) - 3) / 100.0
            hist.append({"days_ago": d, "price": round(base * (1 + wiggle), 2)})
        return hist

    def next_cart_ref(self, platform: Platform) -> str:
        with self.lock:
            self.cart_counter += 1
            return f"{platform.value.upper()}-CART-{self.cart_counter:05d}"


_store: Optional[MockStore] = None


def get_store() -> MockStore:
    global _store
    if _store is None:
        _store = MockStore()
    return _store


def reset_store() -> MockStore:
    global _store
    _store = MockStore()
    return _store
