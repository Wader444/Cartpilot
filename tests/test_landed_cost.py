from app.optimizer.models import CartLine, FeeSchedule, Offer
from app.optimizer.landed_cost import landed_cost
from app.optimizer.unit_price import unit_price

# Basket 1: free delivery, offer applies.
# items: 26500 + 2*6800 + 14500 = 54600
# fees: 200 + 0 + 0 + 300 = 500; offer 1500
# landed: 54600 + 500 - 1500 = 53600
def test_basket_1_offer_applies():
    cart = [CartLine("SKU001", 26500), CartLine("SKU002", 6800, 2), CartLine("SKU003", 14500)]
    fees = FeeSchedule(handling=200, delivery=0, small_cart_fee=0,
                       small_cart_threshold=0, surge=300)
    offers = [Offer("FIRST15", 1500, min_cart_value=20000)]
    r = landed_cost(cart, fees, offers)
    assert r.item_total == 54600
    assert r.offers == 1500
    assert r.landed_total == 53600

# Basket 2: small cart below threshold, so small-cart fee applies.
# items: 3200 + 8200 = 11400 (< threshold 15000), so small_cart = 3000
# fees: 200 + 2000 + 3000 + 0 = 5200; no offers
# landed: 11400 + 5200 = 16600
def test_basket_2_small_cart_fee():
    cart = [CartLine("SKU010", 3200), CartLine("SKU011", 8200)]
    fees = FeeSchedule(handling=200, delivery=2000, small_cart_fee=3000,
                       small_cart_threshold=15000, surge=0)
    r = landed_cost(cart, fees, [])
    assert r.small_cart == 3000
    assert r.landed_total == 16600

# Basket 3: offer needs a minimum cart value the basket does not reach.
# items: 9000 (< min 20000), so the offer is skipped
# fees: 200 + 2500 + 0 + 0 = 2700
# landed: 9000 + 2700 = 11700
def test_basket_3_offer_below_minimum_not_applied():
    cart = [CartLine("SKU020", 9000)]
    fees = FeeSchedule(handling=200, delivery=2500)
    offers = [Offer("SAVE50", 5000, min_cart_value=20000)]
    r = landed_cost(cart, fees, offers)
    assert r.offers == 0
    assert r.skipped_offers == ["SAVE50"]
    assert r.landed_total == 11700

def test_threshold_boundary_is_not_small_cart():
    # exactly at the threshold: fee does not apply
    cart = [CartLine("SKU030", 15000)]
    fees = FeeSchedule(small_cart_fee=3000, small_cart_threshold=15000)
    assert landed_cost(cart, fees, []).small_cart == 0

def test_unit_price():
    assert unit_price(26500, 5, "kg") == (5300, "kg")      # 265 / 5 kg = 53.00 per kg
    assert unit_price(9000, 200, "g") == (45000, "kg")     # 90 / 0.2 kg = 450.00 per kg
    assert unit_price(100, 1, "bundle") is None            # unknown unit