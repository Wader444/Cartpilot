"""Database access for memory + session persistence.

Uses SQLAlchemy (Core) when installed – works with SQLite or Postgres via
CARTPILOT_DATABASE_URL. If SQLAlchemy is not available it falls back to the
standard-library sqlite3 module with the same SQL, so the backend still runs.
All SQL uses named parameters (:name) and portable types.
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any, Optional

try:
    import sqlalchemy as sa
    from sqlalchemy.pool import StaticPool

    HAVE_SQLALCHEMY = True
except Exception:  # pragma: no cover - exercised only where SQLAlchemy is missing
    sa = None
    HAVE_SQLALCHEMY = False

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS preferences (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1, source TEXT, created_at TEXT, updated_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_pref_user ON preferences (user_id, kind, active)",
    """CREATE TABLE IF NOT EXISTS preference_history (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, kind TEXT NOT NULL, value TEXT NOT NULL,
        change TEXT NOT NULL, reason TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS episodes (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT NOT NULL, proposal_id TEXT NOT NULL,
        request_json TEXT, proposal_json TEXT, selected_products_json TEXT, platform_allocation_json TEXT,
        estimated_savings REAL, total_landed REAL, decision TEXT NOT NULL, edits_json TEXT,
        created_at TEXT, decided_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_episode_user ON episodes (user_id, created_at)",
    """CREATE TABLE IF NOT EXISTS reminders (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT, kind TEXT NOT NULL, text TEXT NOT NULL,
        remind_at TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_reminder_user ON reminders (user_id, remind_at)",
    """CREATE TABLE IF NOT EXISTS pantry (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, item TEXT NOT NULL, product_id TEXT, product_name TEXT,
        brand TEXT, quantity REAL, unit TEXT, packs INTEGER, platform TEXT, proposal_id TEXT, purchased_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS ix_pantry_user ON pantry (user_id, item, purchased_at)",
    """CREATE TABLE IF NOT EXISTS price_observations (
        id TEXT PRIMARY KEY, platform TEXT NOT NULL, product_id TEXT NOT NULL, price REAL NOT NULL, mrp REAL,
        in_stock INTEGER, session_id TEXT, observed_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS ix_price_obs ON price_observations (platform, product_id, observed_at)",
    """CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY, user_id TEXT, phase TEXT, state_json TEXT, updated_at TEXT)""",
]


class Database:
    def __init__(self, url: str = "sqlite:///./cartpilot.db", force_stdlib: bool = False):
        self.url = url
        self._lock = threading.RLock()
        self.backend = "sqlalchemy" if (HAVE_SQLALCHEMY and not force_stdlib) else "sqlite3"
        if self.backend == "sqlalchemy":
            kwargs: dict[str, Any] = {"future": True}
            if url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False}
                if url in ("sqlite://", "sqlite:///:memory:"):
                    kwargs["poolclass"] = StaticPool
            self.engine = sa.create_engine(url, **kwargs)
        else:
            if not url.startswith("sqlite"):
                raise RuntimeError("SQLAlchemy is required for non-SQLite databases")
            path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else ":memory:"
            self.conn = sqlite3.connect(path or ":memory:", check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
        self.create_all()

    def create_all(self) -> None:
        for stmt in SCHEMA:
            self.execute(stmt)

    def execute(self, sql: str, params: Optional[dict] = None) -> None:
        with self._lock:
            if self.backend == "sqlalchemy":
                with self.engine.begin() as c:
                    c.execute(sa.text(sql), params or {})
            else:
                self.conn.execute(sql, params or {})
                self.conn.commit()

    def query(self, sql: str, params: Optional[dict] = None) -> list[dict]:
        with self._lock:
            if self.backend == "sqlalchemy":
                with self.engine.connect() as c:
                    return [dict(r._mapping) for r in c.execute(sa.text(sql), params or {})]
            cur = self.conn.execute(sql, params or {})
            return [dict(r) for r in cur.fetchall()]

    def ping(self) -> bool:
        try:
            self.query("SELECT 1 AS ok")
            return True
        except Exception:
            return False


_db: Optional[Database] = None


def get_db(url: Optional[str] = None) -> Database:
    global _db
    if _db is None:
        from app.core.config import get_settings

        _db = Database(url or get_settings().database_url)
    return _db


def set_db(db: Optional[Database]) -> None:
    global _db
    _db = db
