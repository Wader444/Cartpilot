"""Preference memory: accepted/rejected brands, diet, never-substitute items and
preferred platforms – persisted across sessions.

Rules
  * read before planning (apply_to_request)
  * updated only after an explicit user approval, rejection or edit
  * never overwritten silently: every change is written to
    preference_history and returned to the caller so the API/UI can show it
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.core.models import DietPreference, Platform, ShoppingProposal, ShoppingRequest, new_id, utcnow
from app.memory.database import Database

KINDS = {"accepted_brand", "rejected_brand", "diet", "never_substitute", "preferred_platform"}
SINGLE_VALUED = {"diet"}
OPPOSITES = {"accepted_brand": "rejected_brand", "rejected_brand": "accepted_brand"}


class PreferenceChange(BaseModel):
    kind: str
    value: str
    action: str = Field(..., description="added | unchanged | superseded | removed")
    previous: Optional[str] = None
    reason: str = ""


class UserPreferences(BaseModel):
    user_id: str
    accepted_brands: list[str] = Field(default_factory=list)
    rejected_brands: list[str] = Field(default_factory=list)
    diet: Optional[DietPreference] = None
    never_substitute: list[str] = Field(default_factory=list)
    preferred_platforms: list[Platform] = Field(default_factory=list)


class PreferenceMemory:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ read
    def _active(self, user_id: str, kind: Optional[str] = None) -> list[dict]:
        sql = "SELECT * FROM preferences WHERE user_id = :u AND active = 1"
        params: dict = {"u": user_id}
        if kind:
            sql += " AND kind = :k"
            params["k"] = kind
        return self.db.query(sql + " ORDER BY created_at", params)

    def get(self, user_id: str) -> UserPreferences:
        rows = self._active(user_id)
        prefs = UserPreferences(user_id=user_id)
        for r in rows:
            k, v = r["kind"], r["value"]
            if k == "accepted_brand":
                prefs.accepted_brands.append(v)
            elif k == "rejected_brand":
                prefs.rejected_brands.append(v)
            elif k == "diet":
                prefs.diet = DietPreference(v)
            elif k == "never_substitute":
                prefs.never_substitute.append(v)
            elif k == "preferred_platform":
                try:
                    prefs.preferred_platforms.append(Platform(v))
                except ValueError:
                    pass
        return prefs

    def history(self, user_id: str, limit: int = 100) -> list[dict]:
        return self.db.query("SELECT * FROM preference_history WHERE user_id = :u ORDER BY created_at DESC LIMIT :n",
                             {"u": user_id, "n": limit})

    # ------------------------------------------------------------------ write
    def _log(self, user_id: str, kind: str, value: str, change: str, reason: str) -> None:
        self.db.execute("INSERT INTO preference_history (id, user_id, kind, value, change, reason, created_at) "
                        "VALUES (:id, :u, :k, :v, :c, :r, :t)",
                        {"id": new_id("ph"), "u": user_id, "k": kind, "v": value, "c": change, "r": reason,
                         "t": utcnow().isoformat()})

    def _deactivate(self, row: dict, reason: str) -> None:
        self.db.execute("UPDATE preferences SET active = 0, updated_at = :t WHERE id = :id",
                        {"t": utcnow().isoformat(), "id": row["id"]})
        self._log(row["user_id"], row["kind"], row["value"], "superseded", reason)

    def set(self, user_id: str, kind: str, value: str, source: str = "user", reason: str = "") -> PreferenceChange:
        if kind not in KINDS:
            raise ValueError(f"unknown preference kind {kind}")
        value = value.strip()
        norm = value.lower()
        existing = self._active(user_id, kind)
        if any(r["value"].lower() == norm for r in existing):
            return PreferenceChange(kind=kind, value=value, action="unchanged", reason=reason)

        previous = None
        if kind in SINGLE_VALUED and existing:
            for r in existing:
                previous = r["value"]
                self._deactivate(r, f"replaced by {value}: {reason}")
        opp = OPPOSITES.get(kind)
        if opp:
            for r in self._active(user_id, opp):
                if r["value"].lower() == norm:
                    previous = f"{opp}:{r['value']}"
                    self._deactivate(r, f"user now marks {value} as {kind}: {reason}")

        now = utcnow().isoformat()
        self.db.execute("INSERT INTO preferences (id, user_id, kind, value, active, source, created_at, updated_at) "
                        "VALUES (:id, :u, :k, :v, 1, :s, :t, :t)",
                        {"id": new_id("pref"), "u": user_id, "k": kind, "v": value, "s": source, "t": now})
        self._log(user_id, kind, value, "added" if previous is None else "added_replacing", reason)
        return PreferenceChange(kind=kind, value=value, action="added" if previous is None else "superseded",
                                previous=previous, reason=reason)

    def remove(self, user_id: str, kind: str, value: str, reason: str = "") -> PreferenceChange:
        rows = [r for r in self._active(user_id, kind) if r["value"].lower() == value.lower()]
        for r in rows:
            self.db.execute("UPDATE preferences SET active = 0, updated_at = :t WHERE id = :id",
                            {"t": utcnow().isoformat(), "id": r["id"]})
            self._log(user_id, kind, r["value"], "removed", reason)
        return PreferenceChange(kind=kind, value=value, action="removed" if rows else "unchanged", reason=reason)

    # ------------------------------------------------------------------ planning
    def apply_to_request(self, user_id: str, request: ShoppingRequest) -> tuple[ShoppingRequest, list[str]]:
        """Merge stored preferences into a request. The request (what the user said now) wins on conflict."""
        prefs = self.get(user_id)
        req = request.model_copy(deep=True)
        notes: list[str] = []
        said_prefer = {b.lower() for b in req.preferred_brands}
        said_avoid = {b.lower() for b in req.avoid_brands}

        for b in prefs.rejected_brands:
            if b.lower() in said_prefer:
                notes.append(f"Memory says avoid {b}, but you asked for it now – using {b} this time")
            elif b.lower() not in said_avoid:
                req.avoid_brands.append(b)
                notes.append(f"Avoiding {b} (you rejected it before)")
        for b in prefs.accepted_brands:
            if b.lower() not in said_avoid and b.lower() not in {x.lower() for x in req.preferred_brands}:
                req.preferred_brands.append(b)
        if prefs.accepted_brands:
            notes.append(f"Preferring brands you approved before: {', '.join(prefs.accepted_brands)}")
        if req.dietary_preference is None and prefs.diet is not None:
            req.dietary_preference = prefs.diet
            req.constraint_sources["dietary_preference"] = "memory"
            notes.append(f"Diet from memory: {prefs.diet.value}")
        elif prefs.diet is not None and req.dietary_preference != prefs.diet:
            notes.append(f"You asked for {req.dietary_preference.value} now; memory says {prefs.diet.value} "
                         f"– using {req.dietary_preference.value} for this order (memory unchanged)")
        for n in prefs.never_substitute:
            if n.lower() not in {x.lower() for x in req.never_substitute}:
                req.never_substitute.append(n)
                notes.append(f"Never substitute '{n}' (saved rule)")
        for p in prefs.preferred_platforms:
            if p not in req.preferred_platforms:
                req.preferred_platforms.append(p)
        return req, notes

    # ------------------------------------------------------------------ learning
    def learn_from_approval(self, user_id: str, proposal: ShoppingProposal) -> list[PreferenceChange]:
        changes = []
        req = proposal.request
        brands = sorted({it.listing.product.brand for c in proposal.carts for it in c.items if not it.is_substitute})
        for b in brands:
            if b.lower() not in {x.lower() for x in req.avoid_brands}:
                changes.append(self.set(user_id, "accepted_brand", b, "approval", f"approved {proposal.proposal_id}"))
        if req.dietary_preference and req.constraint_sources.get("dietary_preference") != "memory":
            changes.append(self.set(user_id, "diet", req.dietary_preference.value, "approval",
                                    f"stated in approved request {proposal.proposal_id}"))
        for n in req.never_substitute:
            changes.append(self.set(user_id, "never_substitute", n, "approval", f"approved {proposal.proposal_id}"))
        return [c for c in changes if c.action != "unchanged"]

    def learn_from_rejection(self, user_id: str, proposal_id: str, rejected_brands: list[str],
                             reason: str = "") -> list[PreferenceChange]:
        out = [self.set(user_id, "rejected_brand", b, "rejection", reason or f"rejected in {proposal_id}")
               for b in rejected_brands]
        return [c for c in out if c.action != "unchanged"]

    def learn_from_edit(self, user_id: str, proposal_id: str, reject_brands: list[str], prefer_brands: list[str],
                        never_substitute: list[str]) -> list[PreferenceChange]:
        out = []
        out += [self.set(user_id, "rejected_brand", b, "edit", f"edit of {proposal_id}") for b in reject_brands]
        out += [self.set(user_id, "accepted_brand", b, "edit", f"edit of {proposal_id}") for b in prefer_brands]
        out += [self.set(user_id, "never_substitute", n, "edit", f"edit of {proposal_id}") for n in never_substitute]
        return [c for c in out if c.action != "unchanged"]
