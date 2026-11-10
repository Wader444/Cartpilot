"""HTTP adapter for Member B's mock store API (enabled with CARTPILOT_STORE_URL).

Expected endpoints (agree with Member B; JSON bodies use the shared models):
  GET  {base}/{platform}/search?q=...          -> [Listing]
  POST {base}/{platform}/fees    {"cart": [CartLine]}               -> PlatformFees
  POST {base}/{platform}/eta     {"cart": [CartLine], "pincode": ""} -> EtaQuote
  POST {base}/{platform}/offers  {"cart": [CartLine]}               -> [Offer]
  POST {base}/{platform}/cart    {"cart": [CartLine]}               -> PreparedCart

Uses only the standard library so it adds no dependency.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pydantic import ValidationError

from app.adapters.base import InvalidPlatformResponse, PlatformError, PlatformTimeout
from app.core.models import CartLine, EtaQuote, Listing, Offer, Platform, PlatformFees, PreparedCart


class HttpPlatformAdapter:
    def __init__(self, platform: Platform, base_url: str, timeout_s: float = 5.0):
        self.platform = platform
        self.base = base_url.rstrip("/")
        self.timeout = timeout_s

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> Any:
        url = f"{self.base}/{self.platform.value}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode() or "null")
        except (socket.timeout, TimeoutError) as e:
            raise PlatformTimeout(f"{self.platform.value}: timeout calling {path}") from e
        except urllib.error.URLError as e:
            if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
                raise PlatformTimeout(f"{self.platform.value}: timeout calling {path}") from e
            raise PlatformError(f"{self.platform.value}: {e}") from e
        except json.JSONDecodeError as e:
            raise InvalidPlatformResponse(f"{self.platform.value}: non-JSON response from {path}") from e

    @staticmethod
    def _lines(cart: list[CartLine]) -> list[dict]:
        return [l.model_dump() for l in cart]

    def _validate(self, model, payload, many: bool = False):
        try:
            if many:
                if not isinstance(payload, list):
                    raise InvalidPlatformResponse(f"{self.platform.value}: expected a list")
                return [model.model_validate(p) for p in payload]
            return model.model_validate(payload)
        except ValidationError as e:
            raise InvalidPlatformResponse(f"{self.platform.value}: {e.error_count()} contract errors") from e

    def search_products(self, query: str) -> list[Listing]:
        return self._validate(Listing, self._call("GET", "search", params={"q": query}), many=True)

    def get_fees(self, cart: list[CartLine]) -> PlatformFees:
        return self._validate(PlatformFees, self._call("POST", "fees", {"cart": self._lines(cart)}))

    def get_eta(self, cart: list[CartLine], pincode: str) -> EtaQuote:
        return self._validate(EtaQuote, self._call("POST", "eta", {"cart": self._lines(cart), "pincode": pincode}))

    def get_offers(self, cart: list[CartLine]) -> list[Offer]:
        return self._validate(Offer, self._call("POST", "offers", {"cart": self._lines(cart)}), many=True)

    def build_cart(self, cart: list[CartLine]) -> PreparedCart:
        prepared = self._validate(PreparedCart, self._call("POST", "cart", {"cart": self._lines(cart)}))
        # Defence in depth: the backend never accepts a cart that claims payment happened.
        if prepared.payment_executed or prepared.order_placed:
            raise InvalidPlatformResponse("Store reported payment/order – CartPilot only prepares carts")
        return prepared
