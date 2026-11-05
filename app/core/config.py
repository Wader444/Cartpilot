"""Runtime configuration from environment variables (see .env.example).

No secrets are hard-coded. Everything has a safe local default so the backend
runs with zero configuration and no paid APIs.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

try:  # optional: load a local .env if python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # storage
    database_url: str = field(default_factory=lambda: os.getenv("CARTPILOT_DATABASE_URL", "sqlite:///./cartpilot.db"))

    # time
    timezone: str = field(default_factory=lambda: os.getenv("CARTPILOT_TIMEZONE", "Asia/Kolkata"))
    fixed_now: Optional[str] = field(default_factory=lambda: os.getenv("CARTPILOT_FIXED_NOW"))  # "HH:MM" for demos

    # platform data
    store_url: Optional[str] = field(default_factory=lambda: os.getenv("CARTPILOT_STORE_URL"))  # Member B's mock API
    tool_timeout_s: float = field(default_factory=lambda: float(os.getenv("CARTPILOT_TOOL_TIMEOUT_S", "5")))
    tool_max_retries: int = field(default_factory=lambda: int(os.getenv("CARTPILOT_TOOL_MAX_RETRIES", "2")))

    # teammate modules (dotted paths); empty = use built-in fallback
    matching_module: str = field(default_factory=lambda: os.getenv("CARTPILOT_MATCHING_MODULE", "app.agents.matching_agent"))
    optimizer_module: str = field(default_factory=lambda: os.getenv("CARTPILOT_OPTIMIZER_MODULE", "app.optimizer.optimizer"))
    price_history_module: str = field(default_factory=lambda: os.getenv("CARTPILOT_PRICE_HISTORY_MODULE", "app.adapters.price_history"))

    # agent behaviour
    max_replans: int = field(default_factory=lambda: int(os.getenv("CARTPILOT_MAX_REPLANS", "3")))
    fee_anomaly_pct: float = field(default_factory=lambda: float(os.getenv("CARTPILOT_FEE_ANOMALY_PCT", "0.25")))
    fee_anomaly_abs: float = field(default_factory=lambda: float(os.getenv("CARTPILOT_FEE_ANOMALY_ABS", "10")))
    match_auto_threshold: float = 0.85
    match_confirm_threshold: float = 0.60

    # optional LLM (OpenAI-compatible: Ollama, Groq free tier, OpenAI, ...)
    llm_base_url: Optional[str] = field(default_factory=lambda: os.getenv("LLM_BASE_URL"))
    llm_api_key: Optional[str] = field(default_factory=lambda: os.getenv("LLM_API_KEY"))
    llm_model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "llama3.1"))
    llm_timeout_s: float = field(default_factory=lambda: float(os.getenv("LLM_TIMEOUT_S", "20")))

    # dev / demo
    enable_fault_injection: bool = field(default_factory=lambda: _bool("CARTPILOT_ENABLE_FAULTS", False))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_base_url)

    @property
    def sqlite_path(self) -> str:
        url = self.database_url
        if url.startswith("sqlite:///"):
            return url[len("sqlite:///"):] or ":memory:"
        if url == "sqlite://":
            return ":memory:"
        raise ValueError("The stdlib fallback only supports sqlite URLs")


class Clock:
    """Injectable clock so deadlines are testable and demos are repeatable."""

    def __init__(self, tz: str = "Asia/Kolkata", fixed_now: Optional[str] = None,
                 now_fn: Optional[Callable[[], datetime]] = None):
        self.tz = ZoneInfo(tz)
        self._fixed = fixed_now
        self._now_fn = now_fn

    def now(self) -> datetime:
        if self._now_fn:
            return self._now_fn()
        real = datetime.now(self.tz)
        if self._fixed:
            hh, mm = (int(x) for x in self._fixed.split(":"))
            return real.replace(hour=hh, minute=mm, second=0, microsecond=0)
        return real


_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(**overrides) -> Settings:
    """Used by tests to start from a clean configuration."""
    global _settings
    _settings = Settings()
    for k, v in overrides.items():
        setattr(_settings, k, v)
    return _settings
