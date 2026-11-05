"""TEMPORARY price-history source (Member B seeds the real one).

Interface: get_price_history(platform, sku_id) -> list[{"days_ago": int, "price": float}]
Return [] when no history exists – the Auditor then marks the check "unverified".
"""
from __future__ import annotations

from app.adapters.mock_store import CATALOG_BY_SKU, get_store
from app.core.models import Platform


def get_price_history(platform: Platform, sku_id: str) -> list[dict]:
    if sku_id not in CATALOG_BY_SKU:
        return []
    return get_store().price_history(platform, sku_id)
