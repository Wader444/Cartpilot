"""set_reminder(time, text) – reorder and price-watch reminders.

Reminders are stored in the database and listed through the API (Member C's
UI or a calendar integration can deliver them). Setting a reminder is never
done silently: it only happens when the user asks, e.g. by passing
`remind_in_days` when approving, or through POST /api/reminders.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.core.models import new_id, utcnow
from app.core.trace import TraceRecorder
from app.memory.database import Database


class Reminder(BaseModel):
    reminder_id: str
    user_id: str
    session_id: Optional[str] = None
    kind: str = Field("reorder", description="reorder | price_watch | custom")
    text: str
    remind_at: datetime
    status: str = "scheduled"
    created_at: datetime = Field(default_factory=utcnow)


class ReminderError(ValueError):
    pass


class ReminderTools:
    def __init__(self, db: Database):
        self.db = db

    def set_reminder(self, user_id: str, time: datetime, text: str, now: datetime, kind: str = "reorder",
                     session_id: Optional[str] = None, rec: Optional[TraceRecorder] = None) -> Reminder:
        if time.tzinfo is None:
            time = time.replace(tzinfo=now.tzinfo)
        if time <= now:
            raise ReminderError("Reminder time must be in the future")
        text = text.strip()
        if not text or len(text) > 300:
            raise ReminderError("Reminder text must be 1–300 characters")
        r = Reminder(reminder_id=new_id("rem"), user_id=user_id, session_id=session_id, kind=kind, text=text,
                     remind_at=time)
        self.db.execute("INSERT INTO reminders (id, user_id, session_id, kind, text, remind_at, status, created_at) "
                        "VALUES (:id, :u, :s, :k, :t, :at, :st, :c)",
                        {"id": r.reminder_id, "u": user_id, "s": session_id, "k": kind, "t": text,
                         "at": r.remind_at.isoformat(), "st": r.status, "c": r.created_at.isoformat()})
        if rec is not None:
            rec.note("set_reminder", "User asked for a reminder", input={"time": r.remind_at.isoformat(), "text": text},
                     output=r.model_dump(mode="json"))
        return r

    def list(self, user_id: str, include_past: bool = True, now: Optional[datetime] = None) -> list[Reminder]:
        rows = self.db.query("SELECT * FROM reminders WHERE user_id = :u ORDER BY remind_at", {"u": user_id})
        out = [Reminder(reminder_id=r["id"], user_id=r["user_id"], session_id=r["session_id"], kind=r["kind"],
                        text=r["text"], remind_at=datetime.fromisoformat(r["remind_at"]), status=r["status"],
                        created_at=datetime.fromisoformat(r["created_at"])) for r in rows]
        if not include_past and now is not None:
            out = [r for r in out if r.remind_at > now]
        return out
