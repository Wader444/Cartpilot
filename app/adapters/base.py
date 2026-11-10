"""PlatformAdapter interface (owned by Member B).

Every platform – mock or real – implements exactly these methods. The planner
never talks to a platform any other way. There is deliberately NO payment or
order-placement method: build_cart only prepares a cart.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.core.models import CartLine, EtaQuote, Listing, Offer, Platform, PlatformFees, PreparedCart


class PlatformError(Exception):
    """Platform returned an error or is unavailable."""


class PlatformTimeout(PlatformError):
    """Platform did not answer within the timeout."""


class InvalidPlatformResponse(PlatformError):
    """Platform answered with data that fails the shared contract."""


@runtime_checkable
class PlatformAdapter(Protocol):
    platform: Platform

    def search_products(self, query: str) -> list[Listing]: ...

    def get_fees(self, cart: list[CartLine]) -> PlatformFees: ...

    def get_eta(self, cart: list[CartLine], pincode: str) -> EtaQuote: ...

    def get_offers(self, cart: list[CartLine]) -> list[Offer]: ...

    def build_cart(self, cart: list[CartLine]) -> PreparedCart: ...
