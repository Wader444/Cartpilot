from app.optimizer.models import CartLine, FeeSchedule, Offer, LandedCost

def landed_cost(cart: list[CartLine], fees: FeeSchedule, offers: list[Offer]) -> LandedCost:
    item_total = sum(l.price * l.qty for l in cart)

    small_cart = (fees.small_cart_fee
                  if fees.small_cart_threshold and item_total < fees.small_cart_threshold
                  else 0)

    applied, skipped = [], []
    for o in offers:
        if item_total >= o.min_cart_value:
            applied.append(o)
        else:
            skipped.append(o)
    offers_total = sum(o.value for o in applied)

    landed_total = (item_total + fees.handling + fees.delivery
                    + small_cart + fees.surge - offers_total)

    return LandedCost(
        item_total=item_total, handling=fees.handling, delivery=fees.delivery,
        small_cart=small_cart, surge=fees.surge, offers=offers_total,
        landed_total=landed_total,
        applied_offers=[o.label for o in applied],
        skipped_offers=[o.label for o in skipped],
    )