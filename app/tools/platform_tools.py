"""Platform tools: the only way the planner reads prices, fees, offers and ETAs.

  search_products(platform, query)
  get_fees(platform, cart)
  get_eta(platform, cart, pincode)
  get_offers(platform, cart)

Each call goes through the TraceRecorder (timeout, retries, action log) and the
response is validated against the shared contract. build_cart is NOT exposed
here – it lives behind the approval gate (tools/approval_tools.py).
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError

from app.adapters.base import InvalidPlatformResponse, PlatformAdapter, PlatformError
from app.core.config import Settings, get_settings
from app.core.guardrails import assert_no_payment_capability
from app.core.models import CartLine, EtaQuote, Listing, Offer, Platform, PlatformFees
from app.core.trace import ToolCallError, TraceRecorder

_RETRYABLE = (PlatformError, TimeoutError, ConnectionError, OSError)


def build_adapters(settings: Optional[Settings] = None) -> dict[Platform, PlatformAdapter]:
    """HTTP adapters for Member B's API when CARTPILOT_STORE_URL is set, else the in-process mock."""
    settings = settings or get_settings()
    if settings.store_url:
        from app.adapters.http_adapter import HttpPlatformAdapter

        adapters = {p: HttpPlatformAdapter(p, settings.store_url, settings.tool_timeout_s) for p in Platform}
    else:
        from app.adapters.mock_adapter import MockPlatformAdapter

        adapters = {p: MockPlatformAdapter(p) for p in Platform}
    for a in adapters.values():
        # build_cart is allowed (prepare only); anything that looks like paying is not.
        assert_no_payment_capability(a)
    return adapters


def _coerce(model, value: Any, many: bool = False):
    """Accept models or dicts from teammates' adapters; reject anything off-contract."""
    try:
        if many:
            if value is None:
                return []
            if not isinstance(value, list):
                raise InvalidPlatformResponse(f"expected list for {model.__name__}, got {type(value).__name__}")
            return [v if isinstance(v, model) else model.model_validate(v) for v in value]
        return value if isinstance(value, model) else model.model_validate(value)
    except ValidationError as e:
        raise InvalidPlatformResponse(f"{model.__name__} failed contract validation ({e.error_count()} errors)") from e


class PlatformTools:
    def __init__(self, adapters: dict[Platform, PlatformAdapter], recorder: TraceRecorder):
        self.adapters = adapters
        self.rec = recorder

    def platforms(self) -> list[Platform]:
        return list(self.adapters.keys())

    # ------------------------------------------------------------------ tools
    def search_products(self, platform: Platform, query: str, reason: str = "") -> Optional[list[Listing]]:
        adapter = self.adapters[platform]

        def post(result, action):
            listings = _coerce(Listing, result, many=True)
            for l in listings:
                if l.platform != platform:
                    raise InvalidPlatformResponse(f"listing from {l.platform} returned by {platform}")
                l.source_action_id = action.action_id
            return listings

        try:
            listings, _ = self.rec.call("search_products", {"platform": platform.value, "query": query},
                                        lambda: adapter.search_products(query),
                                        reason or f"Find '{query}' on {platform.value}",
                                        retry_on=_RETRYABLE, postprocess=post)
            return listings
        except ToolCallError:
            return None

    def get_fees(self, platform: Platform, cart: list[CartLine], reason: str = "") -> Optional[PlatformFees]:
        adapter = self.adapters[platform]

        def post(result, action):
            fees = _coerce(PlatformFees, result)
            fees.source_action_id = action.action_id
            return fees

        try:
            fees, _ = self.rec.call("get_fees", {"platform": platform.value, "cart": [l.model_dump() for l in cart]},
                                    lambda: adapter.get_fees(cart), reason or f"Fees on {platform.value}",
                                    retry_on=_RETRYABLE, postprocess=post)
            return fees
        except ToolCallError:
            return None

    def get_offers(self, platform: Platform, cart: list[CartLine], cart_value: float,
                   reason: str = "") -> Optional[list[Offer]]:
        adapter = self.adapters[platform]

        def post(result, action):
            offers = _coerce(Offer, result, many=True)
            for o in offers:
                o.source_action_id = action.action_id
            return offers

        try:
            offers, _ = self.rec.call("get_offers", {"platform": platform.value, "cart_value": round(cart_value, 2),
                                                     "cart": [l.model_dump() for l in cart]},
                                      lambda: adapter.get_offers(cart), reason or f"Offers on {platform.value}",
                                      retry_on=_RETRYABLE, postprocess=post)
            return offers
        except ToolCallError:
            return None

    def get_eta(self, platform: Platform, cart: list[CartLine], pincode: str,
                minutes_to_deadline: Optional[int] = None, reason: str = "") -> Optional[EtaQuote]:
        adapter = self.adapters[platform]

        def post(result, action):
            eta = _coerce(EtaQuote, result)
            if minutes_to_deadline is not None:
                eta.deadline_met = eta.eta_mins <= minutes_to_deadline
            eta.source_action_id = action.action_id
            return eta

        try:
            eta, _ = self.rec.call("get_eta", {"platform": platform.value, "pincode": pincode,
                                               "cart": [l.model_dump() for l in cart],
                                               "minutes_to_deadline": minutes_to_deadline},
                                   lambda: adapter.get_eta(cart, pincode), reason or f"ETA on {platform.value}",
                                   retry_on=_RETRYABLE, postprocess=post)
            return eta
        except ToolCallError:
            return None
