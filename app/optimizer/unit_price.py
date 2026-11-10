# Normalize to a base unit: kg, L or piece. Returns paise per base unit.
_FACTORS = {
    "g": ("kg", 0.001), "kg": ("kg", 1.0),
    "ml": ("L", 0.001), "l": ("L", 1.0),
    "piece": ("piece", 1.0), "pcs": ("piece", 1.0),
}

def unit_price(price_paise: int, quantity_value: float, quantity_unit: str):
    key = str(quantity_unit).strip().lower()
    if key not in _FACTORS or not quantity_value:
        return None                       # unknown unit: show nothing, never guess
    label, factor = _FACTORS[key]
    return round(price_paise / (quantity_value * factor)), label