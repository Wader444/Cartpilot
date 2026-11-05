"""TEMPORARY in-process adapter over the mock store (Member B replaces/extends).

Implements the PlatformAdapter protocol. No payment or order methods exist.
"""
from __future__ import annotations

from app.adapters.base import InvalidPlatformResponse, PlatformTimeout
from app.adapters.mock_store import MockStore, get_store
from app.core.models import CartLine, EtaQuote, Listing, Offer, Platform, PlatformFees, PreparedCart


class MockPlatformAdapter:
    def __init__(self, platform: Platform, store: MockStore | None = None):
        self.platform = platform
        self._store = store

    @property
    def store(self) -> MockStore:
        return self._store or get_store()

    # ---------------------------------------------------------------- helpers
    def _guard(self) -> None:
        if self.store.check_platform_fault(self.platform, "timeout") is not None:
            raise PlatformTimeout(f"{self.platform.value} did not respond in time")

    def _maybe_corrupt(self, payload):
        if self.store.check_platform_fault(self.platform, "invalid_response") is not None:
            raise InvalidPlatformResponse(f"{self.platform.value} returned malformed data")
        return payload

    def known_brands(self) -> list[str]:
        from app.adapters.mock_store import KNOWN_BRANDS

        return list(KNOWN_BRANDS)

    # ---------------------------------------------------------------- contract
    def search_products(self, query: str) -> list[Listing]:
        self._guard()
        if self.store.check_platform_fault(self.platform, "empty_results") is not None:
            return []
        raw = self._maybe_corrupt(self.store.search(self.platform, query))
        return [Listing.model_validate(r) for r in raw]

    def get_fees(self, cart: list[CartLine]) -> PlatformFees:
        self._guard()
        lines = [l.model_dump() for l in cart]
        value = self.store.cart_value(self.platform, lines)
        return PlatformFees.model_validate(self.store.fees(self.platform, value))

    def get_eta(self, cart: list[CartLine], pincode: str) -> EtaQuote:
        self._guard()
        return EtaQuote(platform=self.platform, eta_mins=self.store.eta(self.platform, [l.model_dump() for l in cart]))

    def get_offers(self, cart: list[CartLine]) -> list[Offer]:
        self._guard()
        value = self.store.cart_value(self.platform, [l.model_dump() for l in cart])
        return [Offer.model_validate(o) for o in self.store.offers(self.platform, value)]

    def build_cart(self, cart: list[CartLine]) -> PreparedCart:
        """Prepares a cart only. Never pays, never places an order."""
        self._guard()
        return PreparedCart(cart_ref=self.store.next_cart_ref(self.platform), platform=self.platform, lines=cart)
