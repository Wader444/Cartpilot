"""Unit normalisation shared by the intent parser, matching fallback and auditor."""
from __future__ import annotations

import math
from typing import Optional

UNIT_ALIASES = {
    "kg": "kg", "kgs": "kg", "kilo": "kg", "kilos": "kg", "kilogram": "kg", "kilograms": "kg",
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g", "gr": "g",
    "l": "litre", "lt": "litre", "ltr": "litre", "ltrs": "litre", "litre": "litre", "litres": "litre",
    "liter": "litre", "liters": "litre",
    "ml": "ml", "millilitre": "ml", "milliliter": "ml", "mls": "ml",
    "pc": "pcs", "pcs": "pcs", "piece": "pcs", "pieces": "pcs", "nos": "pcs", "no": "pcs",
    "pack": "pcs", "packs": "pcs", "packet": "pcs", "packets": "pcs", "pkt": "pcs", "pkts": "pcs",
    "bottle": "pcs", "bottles": "pcs", "loaf": "pcs", "loaves": "pcs", "unit": "pcs", "units": "pcs",
    "dozen": "dozen",
}

# unit -> (dimension, factor to base unit)
_BASE = {
    "kg": ("mass", 1000.0), "g": ("mass", 1.0),
    "litre": ("volume", 1000.0), "ml": ("volume", 1.0),
    "pcs": ("count", 1.0), "dozen": ("count", 12.0),
}

BASE_LABEL = {"mass": "kg", "volume": "litre", "count": "pc"}


def normalize_unit(unit: Optional[str]) -> str:
    if not unit:
        return "pcs"
    return UNIT_ALIASES.get(unit.strip().lower().rstrip("."), unit.strip().lower())


def to_base(value: float, unit: str) -> tuple[str, float]:
    """Return (dimension, value in g / ml / pieces)."""
    u = normalize_unit(unit)
    dim, factor = _BASE.get(u, ("count", 1.0))
    return dim, value * factor


def same_dimension(unit_a: str, unit_b: str) -> bool:
    return to_base(1, unit_a)[0] == to_base(1, unit_b)[0]


def packs_needed(req_qty: float, req_unit: str, pack_qty: float, pack_unit: str) -> int:
    """How many packs cover the requested quantity. Mismatched dimensions -> 1 pack per requested unit."""
    rdim, rbase = to_base(req_qty, req_unit)
    pdim, pbase = to_base(pack_qty, pack_unit)
    if rdim != pdim or pbase <= 0:
        return 1  # cannot convert (e.g. "3 tomatoes" vs a 1 kg pack): one pack, flagged by the auditor
    return max(1, int(math.ceil(round(rbase / pbase, 6))))


def unit_price(price: float, pack_qty: float, pack_unit: str) -> tuple[float, str]:
    """Price per kg / litre / piece."""
    dim, base = to_base(pack_qty, pack_unit)
    if base <= 0:
        return price, BASE_LABEL[dim]
    per = price / base * (1000.0 if dim in ("mass", "volume") else 1.0)
    return round(per, 2), BASE_LABEL[dim]
