"""Session state storage: in-memory for speed, persisted to the database so a
session (and its trace) survives a server restart.
"""
from __future__ import annotations

import threading
from typing import Optional

from app.core.models import AgentState, utcnow
from app.memory.database import Database


class SessionStore:
    def __init__(self, db: Database):
        self.db = db
        self._cache: dict[str, AgentState] = {}
        self._lock = threading.RLock()

    def save(self, state: AgentState) -> None:
        with self._lock:
            state.updated_at = utcnow()
            self._cache[state.session_id] = state
            params = {"id": state.session_id, "u": state.user_id, "p": state.phase.value,
                      "j": state.model_dump_json(), "t": state.updated_at.isoformat()}
            if self.db.query("SELECT session_id FROM sessions WHERE session_id = :id", {"id": state.session_id}):
                self.db.execute("UPDATE sessions SET phase = :p, state_json = :j, updated_at = :t, user_id = :u "
                                "WHERE session_id = :id", params)
            else:
                self.db.execute("INSERT INTO sessions (session_id, user_id, phase, state_json, updated_at) "
                                "VALUES (:id, :u, :p, :j, :t)", params)

    def get(self, session_id: str) -> Optional[AgentState]:
        with self._lock:
            if session_id in self._cache:
                return self._cache[session_id]
            rows = self.db.query("SELECT state_json FROM sessions WHERE session_id = :id", {"id": session_id})
            if not rows:
                return None
            state = AgentState.model_validate_json(rows[0]["state_json"])
            self._cache[session_id] = state
            return state

    def find_by_proposal(self, proposal_id: str) -> Optional[AgentState]:
        with self._lock:
            for s in self._cache.values():
                if (s.proposal and s.proposal.proposal_id == proposal_id) or proposal_id in s.proposal_history:
                    return s
        rows = self.db.query("SELECT session_id FROM sessions WHERE state_json LIKE :p", {"p": f"%{proposal_id}%"})
        return self.get(rows[0]["session_id"]) if rows else None
