from dataclasses import dataclass, field
from typing import Optional

def to_paise(rupees) -> int:
    return round(float(rupees) * 100)

@dataclass(frozen=True)
class CartLine:
    sku_id: str
    price: int          # paise, from Listing.price
    qty: int = 1
    quantity_value: float = 0.0   # from Product, for unit price
    quantity_unit: str = ""       # from Product

@dataclass(frozen=True)
class FeeSchedule:
    handling: int = 0
    delivery: int = 0
    small_cart_fee: int = 0
    small_cart_threshold: int = 0   # fee applies when item_total is below this
    surge: int = 0

@dataclass(frozen=True)
class Offer:
    label: str
    value: int                      # paise
    min_cart_value: int = 0         # paise; offer needs item_total >= this

@dataclass
class LandedCost:
    item_total: int
    handling: int
    delivery: int
    small_cart: int
    surge: int
    offers: int
    landed_total: int
    applied_offers: list = field(default_factory=list)
    skipped_offers: list = field(default_factory=list)