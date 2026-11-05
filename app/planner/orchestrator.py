"""Planner / Orchestrator – runs the agent loop.

PLAN     read memory, build the basket, compute time to deadline, write a plan
ACT      probe each platform (ETA, fee schedule, offers) and search every item
OBSERVE  match listings across platforms; low-confidence/unavailable -> substitutes
ACT      optimize the split (Member C interface)
VERIFY   Auditor re-checks everything; triggers -> re-plan (bounded)
PROPOSE  build the proposal ONLY from verified tool outputs; zero-invented-numbers check
APPROVAL wait for the user (approve / edit / reject)
FINALIZE approved carts are prepared – never paid, never ordered
"""
from __future__ import annotations

import math
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from app.adapters.base import PlatformAdapter
from app.agents.auditor_agent import AuditorAgent
from app.agents.intent_agent import IntentAgent
from app.core.config import Clock, Settings, get_settings
from app.core.guardrails import GuardrailViolation, assert_no_invented_numbers, r2, within_budget
from app.core.models import (
    ActionStatus,
    AgentState,
    ApprovalDecision,
    ApprovalRequest,
    AuditReport,
    AuditWarning,
    CartItem,
    DietPreference,
    FeeSchedule,
    ItemRequest,
    Listing,
    MatchBand,
    MatchCandidate,
    MatchedProduct,
    NumberProvenance,
    Offer,
    OptimizationConstraints,
    OptimizationResult,
    Phase,
    Platform,
    PlatformBreakdown,
    ProposalEdit,
    ProposalStatus,
    ReplanEvent,
    ShoppingProposal,
    ShoppingRequest,
    WarningSeverity,
)
from app.core.trace import TraceRecorder
from app.memory.database import Database, get_db
from app.memory.episode_memory import EpisodeMemory
from app.memory.pantry_memory import PantryMemory
from app.memory.price_memory import PriceMemory
from app.memory.preference_memory import PreferenceChange, PreferenceMemory
from app.planner.state import SessionStore
from app.planner.workflow import transition
from app.tools.approval_tools import ApprovalError, ApprovalGate
from app.tools.matching_tools import MatchConstraints, MatchingTools, band_for
from app.tools.optimizer_tools import OptimizerTools
from app.tools.platform_tools import PlatformTools, build_adapters
from app.tools.reminder_tools import ReminderError, ReminderTools
from app.core.units import unit_price
from app.agents.auditor_agent import load_price_history

# Starter basket for "weekly groceries for 2" – quantities scale with household size.
WEEKLY_TEMPLATE = [
    ("atta", 2, "kg"), ("rice", 2, "kg"), ("toor dal", 1, "kg"), ("milk", 3, "litre"), ("curd", 800, "g"),
    ("bread", 1, "pcs"), ("tomato", 1, "kg"), ("onion", 1, "kg"), ("potato", 1, "kg"), ("banana", 6, "pcs"),
    ("paneer", 200, "g"), ("spinach", 250, "g"),
]
VEGAN_SWAPS = {"milk": None, "curd": None, "paneer": ("tofu", 200, "g")}


class PlanningError(Exception):
    pass


class CartPilotOrchestrator:
    def __init__(self, settings: Optional[Settings] = None, db: Optional[Database] = None,
                 adapters: Optional[dict[Platform, PlatformAdapter]] = None, clock: Optional[Clock] = None,
                 llm_client: Optional[Callable[[str], str]] = None, known_brands: Optional[list[str]] = None):
        self.settings = settings or get_settings()
        self.db = db or get_db(self.settings.database_url)
        self.prefs = PreferenceMemory(self.db)
        self.episodes = EpisodeMemory(self.db)
        self.pantry = PantryMemory(self.db)
        self.prices = PriceMemory(self.db)
        self.reminders = ReminderTools(self.db)
        self.sessions = SessionStore(self.db)
        self.adapters = adapters or build_adapters(self.settings)
        self.clock = clock or Clock(self.settings.timezone, self.settings.fixed_now)
        if known_brands is None:
            known_brands = self._discover_brands()
        self.known_brands = known_brands
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()
        self.intent = IntentAgent(self.settings, self.clock, known_brands, llm_client)
        self.gate = ApprovalGate()

    def _discover_brands(self) -> list[str]:
        """Brands for the intent parser: from adapters that expose known_brands(), else the mock catalog."""
        brands: set[str] = set()
        for a in self.adapters.values():
            fn = getattr(a, "known_brands", None)
            if callable(fn):
                try:
                    brands.update(fn())
                except Exception:
                    pass
        if not brands:
            from app.adapters.mock_store import KNOWN_BRANDS  # TEMP until Member B's API exposes brands

            brands.update(KNOWN_BRANDS)
        return sorted(brands)

    @contextmanager
    def _session_lock(self, session_id: str):
        """One request at a time per session (two clicks on Approve cannot both run)."""
        with self._locks_guard:
            lock = self._locks.setdefault(session_id, threading.RLock())
        with lock:
            yield

    # =================================================================== API
    def recorder(self, state: AgentState) -> TraceRecorder:
        return TraceRecorder(state, self.settings.tool_timeout_s, self.settings.tool_max_retries)

    def start(self, text: str, user_id: str = "demo-user", pincode: Optional[str] = None) -> AgentState:
        state = AgentState(user_id=user_id)
        rec = self.recorder(state)
        intent, _ = rec.call("intent_agent.parse", {"text": text}, lambda: self.intent.parse(text, pincode),
                             "Turn the request into structured constraints", max_retries=0)
        state.request = intent.request
        state.memory_notes.extend(intent.notes)
        if intent.clarification_question:
            state.clarification_question = intent.clarification_question
            transition(state, Phase.AWAITING_CLARIFICATION, rec,
                       f"Missing {', '.join(intent.missing)} – asking one question instead of guessing")
            self.sessions.save(state)
            return state
        return self._run(state, rec)

    def clarify(self, session_id: str, answer: str) -> AgentState:
        with self._session_lock(session_id):
            return self._clarify_locked(session_id, answer)

    def _clarify_locked(self, session_id: str, answer: str) -> AgentState:
        state = self._get(session_id)
        if state.phase != Phase.AWAITING_CLARIFICATION:
            raise ApprovalError("NOT_AWAITING_CLARIFICATION", f"Session is in {state.phase.value}", 409)
        rec = self.recorder(state)
        intent, _ = rec.call("intent_agent.merge_clarification", {"answer": answer},
                             lambda: self.intent.merge_clarification(state.request, answer),
                             "Fill the missing constraints from the user's answer", max_retries=0)
        state.request = intent.request
        if intent.clarification_question:
            state.clarification_question = intent.clarification_question
            rec.note("clarification", "Still missing information", output={"missing": intent.missing})
            self.sessions.save(state)
            return state
        state.clarification_question = None
        transition(state, Phase.PLAN, rec, "Constraints complete – planning")
        return self._run(state, rec)

    def get(self, session_id: str) -> AgentState:
        return self._get(session_id)

    # ---- approval API (approve_proposal / reject_proposal / edit_proposal) ----
    def approve_proposal(self, session_id: str, req: ApprovalRequest) -> tuple[AgentState, list[PreferenceChange]]:
        with self._session_lock(session_id):
            return self._approve_proposal_locked(session_id, req)

    def _approve_proposal_locked(self, session_id: str, req: ApprovalRequest) -> tuple[AgentState, list[PreferenceChange]]:
        state = self._get(session_id)
        rec = self.recorder(state)
        try:
            token = self.gate.approve(state, req, rec)
        finally:
            self.sessions.save(state)
        transition(state, Phase.FINALIZE, rec, "User approved – preparing carts only", _gate=True)
        try:
            state.prepared_carts = self.gate.prepare_carts(state, token, self.adapters, rec)
        except (ApprovalError, GuardrailViolation) as e:
            state.errors.append(str(e))
            transition(state, Phase.FAILED, rec, f"Cart preparation failed: {e}")
            self.sessions.save(state)
            raise
        changes = self.prefs.learn_from_approval(state.user_id, state.proposal)
        self.episodes.record_decision(state.proposal.proposal_id, "approved",
                                      {"confirmed_items": req.confirmed_items} if req.confirmed_items else None)
        n = self.pantry.record_purchase(state.user_id, state.proposal)
        rec.note("memory.update", "Learned from approval", output={"preference_changes": [c.model_dump() for c in changes],
                                                                   "pantry_rows": n})
        if req.remind_in_days:
            now = self.clock.now()
            items = ", ".join(i.name for i in state.request.items[:5])
            try:
                self.reminders.set_reminder(state.user_id, now + timedelta(days=req.remind_in_days),
                                            f"Time to reorder: {items}" + ("…" if len(state.request.items) > 5 else ""),
                                            now, kind="reorder", session_id=state.session_id, rec=rec)
            except ReminderError as e:
                rec.note("set_reminder", f"Reminder not set: {e}", status=ActionStatus.FAILED)
        transition(state, Phase.DONE, rec, "Carts prepared. No payment made, no order placed – the user pays in-app")
        self.sessions.save(state)
        return state, changes

    def reject_proposal(self, session_id: str, req: ApprovalRequest) -> tuple[AgentState, list[PreferenceChange]]:
        with self._session_lock(session_id):
            return self._reject_proposal_locked(session_id, req)

    def _reject_proposal_locked(self, session_id: str, req: ApprovalRequest) -> tuple[AgentState, list[PreferenceChange]]:
        state = self._get(session_id)
        rec = self.recorder(state)
        p = self.gate.reject(state, req, rec)
        changes = self.prefs.learn_from_rejection(state.user_id, p.proposal_id, req.rejected_brands, req.reason or "")
        self.episodes.record_decision(p.proposal_id, "rejected",
                                      {"reason": req.reason, "rejected_brands": req.rejected_brands})
        rec.note("memory.update", "Learned from rejection", output=[c.model_dump() for c in changes])
        transition(state, Phase.REJECTED, rec, "User rejected the proposal – nothing prepared")
        self.sessions.save(state)
        return state, changes

    def edit_proposal(self, session_id: str, req: ApprovalRequest) -> tuple[AgentState, list[PreferenceChange]]:
        with self._session_lock(session_id):
            return self._edit_proposal_locked(session_id, req)

    def _edit_proposal_locked(self, session_id: str, req: ApprovalRequest) -> tuple[AgentState, list[PreferenceChange]]:
        state = self._get(session_id)
        rec = self.recorder(state)
        old = self.gate.check_editable(state, req)       # 1. validate – nothing changes yet
        ch = req.changes
        new_request = self._apply_edit(state.request, ch)  # 2. build the edited request (may raise -> 422)
        if not new_request.items:
            raise ApprovalError("EMPTY_BASKET", "The edit would remove every item – reject the proposal instead", 422)
        old = self.gate.supersede(state, req, rec)       # 3. only now commit side effects
        self.episodes.record_decision(old.proposal_id, "edited", ch.model_dump(mode="json"))
        changes = self.prefs.learn_from_edit(state.user_id, old.proposal_id, ch.reject_brands, ch.prefer_brands,
                                             ch.never_substitute)
        state.request = new_request
        state.proposal_history.append(old.proposal_id)
        state.listings, state.matches, state.optimization, state.audit = [], [], None, None
        rec.note("memory.update", "Learned from edit", output=[c.model_dump() for c in changes])
        transition(state, Phase.PLAN, rec, "Re-planning with the user's edits")
        self._run(state, rec)
        return state, changes

    @staticmethod
    def _apply_edit(request: ShoppingRequest, ch: ProposalEdit) -> ShoppingRequest:
        r = request.model_copy(deep=True)
        remove = {x.lower().strip() for x in ch.remove_items}
        unknown = remove - {i.name for i in r.items}
        if unknown:
            raise ApprovalError("UNKNOWN_ITEM", f"Not in the basket: {', '.join(sorted(unknown))}", 422)
        r.items = [i for i in r.items if i.name not in remove]
        for name, qty in ch.update_quantities.items():
            target = [i for i in r.items if i.name == name.lower().strip()]
            if not target:
                raise ApprovalError("UNKNOWN_ITEM", f"Not in the basket: {name}", 422)
            if qty <= 0:
                raise ApprovalError("BAD_QUANTITY", f"Quantity for {name} must be positive – use remove_items", 422)
            for i in target:
                i.quantity, i.quantity_assumed = qty, False
        for it in ch.add_items:
            if not any(i.name == it.name for i in r.items):
                r.items.append(it)
        if ch.budget is not None:
            r.budget = ch.budget
        if ch.deadline is not None:
            r.deadline = ch.deadline  # already normalised by ProposalEdit
        for b in ch.reject_brands:
            if b not in r.avoid_brands:
                r.avoid_brands.append(b)
            r.preferred_brands = [x for x in r.preferred_brands if x.lower() != b.lower()]
        r.preferred_brands += [b for b in ch.prefer_brands if b not in r.preferred_brands]
        r.excluded_platforms += [p for p in ch.exclude_platforms if p not in r.excluded_platforms]
        r.never_substitute += [n for n in ch.never_substitute if n not in r.never_substitute]
        if remove or ch.add_items:
            r.items_source = "user"
        return ShoppingRequest.model_validate(r.model_dump())

    def decide(self, session_id: str, req: ApprovalRequest):
        if req.decision == ApprovalDecision.APPROVE:
            return self.approve_proposal(session_id, req)
        if req.decision == ApprovalDecision.REJECT:
            return self.reject_proposal(session_id, req)
        return self.edit_proposal(session_id, req)

    # =================================================================== loop
    def _get(self, session_id: str) -> AgentState:
        state = self.sessions.get(session_id)
        if state is None:
            raise ApprovalError("UNKNOWN_SESSION", f"No session {session_id}", 404)
        return state

    def _run(self, state: AgentState, rec: TraceRecorder) -> AgentState:
        try:
            self._plan_and_propose(state, rec)
        except (PlanningError, GuardrailViolation) as e:
            state.errors.append(str(e))
            rec.note("planner.error", str(e), status=ActionStatus.FAILED)
            if state.phase != Phase.FAILED:
                transition(state, Phase.FAILED, rec, str(e))
        except Exception as e:  # never crash the API; record what happened
            state.errors.append(f"{type(e).__name__}: {e}")
            rec.note("planner.error", f"Unexpected error: {type(e).__name__}: {e}", status=ActionStatus.FAILED)
            if state.phase != Phase.FAILED:
                state.phase = Phase.FAILED
        self.sessions.save(state)
        return state

    # ------------------------------------------------------------------ PLAN
    def _deadline_minutes(self, req: ShoppingRequest, warnings: list[AuditWarning]) -> Optional[int]:
        if not req.deadline:
            return None
        now = self.clock.now()
        hh, mm = (int(x) for x in req.deadline.split(":"))
        dl = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if dl <= now:
            dl += timedelta(days=1)
            warnings.append(AuditWarning(code="DEADLINE_ROLLED", severity=WarningSeverity.INFO,
                                         message=f"{req.deadline} has already passed today – planning for "
                                                 f"{req.deadline} tomorrow"))
        return int((dl - now).total_seconds() // 60)

    def _basket(self, state: AgentState, req: ShoppingRequest, rec: TraceRecorder) -> None:
        if req.items:
            return
        last = self.episodes.last_approved(state.user_id)
        if last and last.get("request", {}).get("items"):
            req.items = [ItemRequest.model_validate(i) for i in last["request"]["items"]]
            req.items_source = "episode"
            rec.note("episode_memory.reuse_basket", "No items given – reusing your last approved basket",
                     output={"episode": last["episode_id"], "items": [i.name for i in req.items]})
            return
        scale = (req.household_size or 2) / 2
        items = []
        for name, q, unit in WEEKLY_TEMPLATE:
            if req.dietary_preference == DietPreference.VEGAN and name in VEGAN_SWAPS:
                swap = VEGAN_SWAPS[name]
                if swap is None:
                    continue
                name, q, unit = swap
            qty = q * scale
            qty = math.ceil(qty) if unit in ("pcs", "kg", "litre") else int(round(qty / 50.0) * 50) or q
            items.append(ItemRequest(name=name, quantity=qty, unit=unit))
        if req.dietary_preference in (DietPreference.EGGETARIAN, DietPreference.NON_VEGETARIAN):
            items.append(ItemRequest(name="eggs", quantity=6 * math.ceil(scale), unit="pcs"))
        req.items, req.items_source = items, "template"
        rec.note("planner.basket", f"No items listed – starter weekly basket for {req.household_size or 2} "
                                   f"(you can edit it before approving)", output=[i.model_dump() for i in items])

    def _plan_and_propose(self, state: AgentState, rec: TraceRecorder) -> None:
        if state.phase != Phase.PLAN:
            transition(state, Phase.PLAN, rec)
        replans_start = len(state.replans)
        req, notes = self.prefs.apply_to_request(state.user_id, state.request)
        rec.note("preference_memory.read", "Apply saved preferences before planning", output=notes)
        recent = self.episodes.list(state.user_id, limit=5)
        rec.note("episode_memory.read", "Recall recent shopping episodes",
                 output=[{"proposal_id": e["proposal_id"], "decision": e["decision"],
                          "total_landed": e["total_landed"]} for e in recent])
        state.memory_notes.extend(notes)
        self._basket(state, req, rec)
        if not req.items:
            raise PlanningError("The basket is empty – tell me which items you need")
        carried: list[AuditWarning] = []
        minutes = self._deadline_minutes(req, carried)
        state.request = req
        state.plan_steps = [
            f"Search {len(req.items)} item(s) on {len(self.adapters)} platforms",
            "Probe ETA, fee schedule and offers on each platform",
            "Match equivalent products; find substitutes for anything unavailable or uncertain",
            f"Optimize the split under budget ₹{req.budget:g}" + (f" and {minutes} min to deadline" if minutes else ""),
            "Auditor re-verifies prices, stock, fees, offers and ETA; re-plan if anything changed",
            "Propose with full landed-cost breakdown and wait for explicit approval",
        ]
        rec.note("planner.plan", "Plan created", output={"steps": state.plan_steps,
                                                         "constraints": {"budget": req.budget, "deadline": req.deadline,
                                                                         "minutes_to_deadline": minutes,
                                                                         "diet": req.dietary_preference,
                                                                         "avoid_brands": req.avoid_brands,
                                                                         "never_substitute": req.never_substitute}})

        tools = PlatformTools(self.adapters, rec)
        matching = MatchingTools(rec, self.settings.matching_module)
        optimizer = OptimizerTools(rec, self.settings.optimizer_module)
        seeded = load_price_history(self.settings.price_history_module)
        sid = state.session_id

        def history(platform, sku):  # Member B's seeded history first, then our own past observations
            h = seeded(platform, sku) if seeded else []
            return h or self.prices.history(platform, sku, exclude_session=sid)

        auditor = AuditorAgent(tools, rec, self.settings, price_history=history)
        excluded: set[Platform] = set(req.excluded_platforms)

        # ---------------- ACT: probe platforms ----------------
        transition(state, Phase.ACT, rec, "Probe platforms and search every item")
        schedules: dict[Platform, FeeSchedule] = {}
        offers: dict[Platform, list[Offer]] = {}
        etas: dict[Platform, int] = {}
        for p in tools.platforms():
            if p in excluded:
                continue
            eta = tools.get_eta(p, [], req.pincode, minutes, reason=f"Delivery estimate from {p.value}")
            fees = tools.get_fees(p, [], reason=f"Fee schedule on {p.value}") if eta else None
            offs = tools.get_offers(p, [], 0, reason=f"Offer rules on {p.value}") if fees else None
            if eta is None or fees is None or offs is None:
                excluded.add(p)
                self._replan(state, rec, ["PLATFORM_FAILURE"], f"{p.value} is not responding – planning without it")
                continue
            etas[p], schedules[p], offers[p] = eta.eta_mins, fees.schedule, offs
        live = [p for p in tools.platforms() if p not in excluded]
        if not live:
            raise PlanningError("No platform is reachable right now – nothing to compare. Try again shortly.")

        listings: dict[str, list[Listing]] = {}
        for item in req.items:
            listings[item.name] = []
            for p in list(live):
                res = tools.search_products(p, item.name, reason=f"Find '{item.name}' on {p.value}")
                if res is None:
                    live.remove(p)
                    excluded.add(p)
                    self._replan(state, rec, ["PLATFORM_FAILURE"], f"{p.value} search failed after retries – "
                                                                   f"dropping it for this plan")
                    continue
                listings[item.name].extend(res)
        if not live:
            raise PlanningError("Every platform failed during search – nothing to compare.")
        for name in listings:
            listings[name] = [l for l in listings[name] if l.platform not in excluded]
        state.listings = [l for ls in listings.values() for l in ls]
        stored = self.prices.record_listings(state.listings, state.session_id)
        rec.note("price_memory.write", "Store observed prices (tool outputs only) for future price checks",
                 output={"observations": stored})

        # ---------------- OBSERVE: match ----------------
        transition(state, Phase.OBSERVE, rec, "Match equivalent products across platforms")
        mc = MatchConstraints(diet=req.dietary_preference, avoid_brands=req.avoid_brands,
                              preferred_brands=req.preferred_brands, never_substitute=req.never_substitute,
                              known_brands=self.known_brands)
        matches = [matching.match_products(item, listings[item.name], mc) for item in req.items]
        tried: set[str] = set()
        for m in matches:
            self._ensure_candidates(state, rec, m, tools, matching, mc, live, tried)
        state.matches = matches

        # ---------------- ACT: optimize, VERIFY, re-plan ----------------
        transition(state, Phase.ACT, rec, "Optimize the split across platforms")
        report: Optional[AuditReport] = None
        opt: Optional[OptimizationResult] = None
        triggered: list[AuditWarning] = []
        verify_replans = 0  # only verification re-plans count against the limit, per proposal version
        while True:
            cons = OptimizationConstraints(budget=req.budget, minutes_to_deadline=minutes, platform_eta=etas,
                                           excluded_platforms=sorted(excluded, key=lambda p: p.value),
                                           preferred_platforms=req.preferred_platforms,
                                           fee_schedules=schedules, offers=offers)
            opt = optimizer.optimize_cart(matches, cons, req.preferred_brands)
            if opt is None:
                raise PlanningError("The optimizer failed – cannot build a plan")
            state.optimization = opt
            if not opt.per_platform:
                break
            transition(state, Phase.VERIFY, rec, "Auditor re-verifies the plan")
            report = auditor.audit(req, opt, matches, minutes, dict(schedules))
            state.audit = report
            if not report.needs_replan:
                break
            if verify_replans >= self.settings.max_replans:
                rec.note("planner.replan_limit", "Re-plan limit reached – proposing with warnings",
                         status=ActionStatus.SKIPPED)
                break
            verify_replans += 1
            trig = [w for w in report.warnings if w.triggers_replan]
            triggered.extend(trig)
            self._apply_audit(report, trig, matches, excluded, schedules, offers)
            for m in matches:
                self._ensure_candidates(state, rec, m, tools, matching, mc, [p for p in live if p not in excluded],
                                        tried)
            self._replan(state, rec, sorted({w.code for w in trig}),
                         "Re-optimizing with verified data: " + "; ".join(w.message for w in trig[:4]))
            transition(state, Phase.ACT, rec, "Re-plan")

        # ---------------- PROPOSE ----------------
        if state.phase != Phase.VERIFY:
            transition(state, Phase.VERIFY, rec, "Nothing to verify")
        transition(state, Phase.PROPOSE, rec, "Build the proposal from verified tool outputs only")
        proposal = self._build_proposal(state, req, opt, report, matches, carried, triggered, replans_start)
        assert_no_invented_numbers(proposal, state.trace)
        rec.note("guardrail.zero_invented_numbers", "Every number traced to a tool output or derived from one",
                 output={"checked_carts": len(proposal.carts), "provenance_entries": len(proposal.provenance)})
        state.proposal = proposal
        self.episodes.record_proposal(state.user_id, proposal)
        transition(state, Phase.AWAITING_APPROVAL, rec,
                   "Waiting for explicit user approval – nothing is prepared, paid or ordered yet")

    # ------------------------------------------------------------------ helpers
    def _replan(self, state: AgentState, rec: TraceRecorder, triggers: list[str], action: str) -> None:
        ev = ReplanEvent(iteration=len(state.replans) + 1, triggers=triggers, action_taken=action)
        state.replans.append(ev)
        rec.note("planner.replan", action, output=ev.model_dump(mode="json"))

    @staticmethod
    def _usable(m: MatchedProduct) -> list[MatchCandidate]:
        return [c for c in m.candidates if c.listing.in_stock and c.band != MatchBand.ASK]

    def _ensure_candidates(self, state: AgentState, rec: TraceRecorder, m: MatchedProduct, tools: PlatformTools,
                           matching: MatchingTools, mc: MatchConstraints, live: list[Platform],
                           tried: set[str]) -> None:
        """If an item has no usable candidate, look for substitutes – never silently."""
        if self._usable(m) or m.request_item.name in tried:
            return
        tried.add(m.request_item.name)
        reason = ("LOW_CONFIDENCE" if any(c.band == MatchBand.ASK for c in m.candidates) and
                  not any(c.band != MatchBand.ASK for c in m.candidates) else "UNAVAILABLE")
        subs = matching.find_substitutes(m.request_item, mc)
        added = 0
        for s in subs:
            for p in live:
                res = tools.search_products(p, s.query, reason=f"Search substitute '{s.query}' for '{m.request_item.name}'")
                if not res:
                    continue
                sub_item = ItemRequest(name=s.query, quantity=m.request_item.quantity, unit=m.request_item.unit)
                sm = matching.match_products(sub_item, res, mc, reason=f"Validate substitute '{s.query}'")
                for c in sm.candidates:
                    if c.band == MatchBand.AUTO and not any(x.listing.key == c.listing.key for x in m.candidates):
                        conf = round(min(c.confidence, s.confidence), 2)
                        m.candidates.append(MatchCandidate(listing=c.listing, confidence=conf, band=band_for(conf),
                                                           is_substitute=True,
                                                           reason=f"substitute: {s.reason}"))
                        added += 1
        m.best_confidence = max((c.confidence for c in m.candidates if c.listing.in_stock), default=0)
        m.band = band_for(m.best_confidence)
        usable = self._usable(m)
        if usable:
            action = (f"'{m.request_item.name}' {reason.lower().replace('_', ' ')} – found {len(usable)} substitute "
                      f"option(s); they need your confirmation")
        elif subs:
            action = f"'{m.request_item.name}' {reason.lower().replace('_', ' ')} – substitutes are too uncertain; asking you"
        else:
            action = f"'{m.request_item.name}' {reason.lower().replace('_', ' ')} – no allowed substitute; asking you"
        self._replan(state, rec, [reason], action)

    @staticmethod
    def _apply_audit(report: AuditReport, trig: list[AuditWarning], matches: list[MatchedProduct],
                     excluded: set[Platform], schedules: dict, offers: dict) -> None:
        for m in matches:
            for c in m.candidates:
                fresh = report.verified_listings.get(c.listing.key)
                if fresh is not None:
                    c.listing = fresh
        for w in trig:
            if w.code == "OUT_OF_STOCK" and w.platform and w.product_id:
                for m in matches:
                    for c in m.candidates:
                        if c.listing.platform == w.platform and c.listing.product_id == w.product_id:
                            c.listing = c.listing.model_copy(update={"in_stock": False})
            elif w.code in ("ETA_MISS", "UNVERIFIED") and w.platform:
                excluded.add(w.platform)
            elif w.code == "FEE_ANOMALY" and w.platform in report.verified_fees:
                schedules[w.platform] = report.verified_fees[w.platform].schedule
            elif w.code == "OFFER_INVALID" and w.platform in report.verified_offers:
                offers[w.platform] = report.verified_offers[w.platform]

    def _build_proposal(self, state: AgentState, req: ShoppingRequest, opt: OptimizationResult,
                        report: Optional[AuditReport], matches: list[MatchedProduct],
                        carried: list[AuditWarning], triggered: list[AuditWarning],
                        replans_start: int = 0) -> ShoppingProposal:
        version = len(state.proposal_history) + 1
        proposal = ShoppingProposal(session_id=state.session_id, version=version, request=req,
                                    budget=req.budget, deadline=req.deadline,
                                    replan_count=len(state.replans) - replans_start)
        prov: list[NumberProvenance] = []
        carts: list[PlatformBreakdown] = []
        unverifiable: list[str] = []
        if report is not None:
            for p, planned in opt.per_platform.items():
                fees = report.verified_fees.get(p)
                offs = report.verified_offers.get(p)
                eta = report.verified_etas.get(p)
                items: list[CartItem] = []
                for it in planned.items:
                    fresh = report.verified_listings.get(it.listing.key)
                    if fresh is None or not fresh.in_stock:
                        unverifiable.append(f"{p.value}:{it.listing.product_id}")
                        continue
                    up, label = unit_price(fresh.price, fresh.product.quantity_value, fresh.product.quantity_unit)
                    items.append(it.model_copy(update={"listing": fresh, "line_total": r2(fresh.price * it.packs),
                                                       "unit_price": up, "unit_label": label}))
                    prov.append(NumberProvenance(field=f"{p.value}.{fresh.product_id}.price", value=fresh.price,
                                                 source=f"tool:{fresh.source_action_id}"))
                if fees is None or offs is None or not items:
                    unverifiable.append(f"{p.value}:fees/offers")
                    continue
                item_total = r2(sum(i.line_total for i in items))
                applicable = [o for o in offs if o.applicable]
                best = max(applicable, key=lambda o: o.discount_amount, default=None)
                disc = best.discount_amount if best else 0.0
                landed = r2(item_total + fees.total - disc)
                carts.append(PlatformBreakdown(platform=p, item_total=item_total, handling=fees.handling,
                                               delivery=fees.delivery, small_cart=fees.small_cart, surge=fees.surge,
                                               offer_discount=disc, offer_id=best.offer_id if best else None,
                                               landed_total=landed, eta_mins=eta.eta_mins if eta else None,
                                               items=items))
                src = f"tool:{fees.source_action_id}"
                prov += [NumberProvenance(field=f"{p.value}.{k}", value=getattr(fees, k), source=src)
                         for k in ("handling", "delivery", "small_cart", "surge")]
                if best:
                    prov.append(NumberProvenance(field=f"{p.value}.offer_discount", value=disc,
                                                 source=f"tool:{best.source_action_id}"))
                if eta:
                    prov.append(NumberProvenance(field=f"{p.value}.eta_mins", value=eta.eta_mins,
                                                 source=f"tool:{eta.source_action_id}"))
                prov += [NumberProvenance(field=f"{p.value}.item_total", value=item_total, source="derived"),
                         NumberProvenance(field=f"{p.value}.landed_total", value=landed, source="derived")]
        if unverifiable:
            raise PlanningError(f"Could not verify {', '.join(unverifiable)} after "
                                f"{proposal.replan_count} re-plan(s) – not proposing unverified numbers")

        proposal.carts = carts
        proposal.total_landed = r2(sum(c.landed_total for c in carts))
        prov.append(NumberProvenance(field="total_landed", value=proposal.total_landed, source="derived"))
        if report and report.baseline_fees is not None and opt.best_single_platform:
            bdisc = max([o.discount_amount for o in report.baseline_offers if o.applicable] or [0])
            proposal.best_single_platform = opt.best_single_platform
            proposal.best_single_total = r2(report.baseline_fees.cart_value + report.baseline_fees.total - bdisc)
            proposal.savings_vs_single = r2(proposal.best_single_total - proposal.total_landed)
            if proposal.best_single_total > 0:
                proposal.savings_pct = round(100 * proposal.savings_vs_single / proposal.best_single_total, 1)
            prov += [NumberProvenance(field="best_single_total", value=proposal.best_single_total, source="derived"),
                     NumberProvenance(field="savings_vs_single", value=proposal.savings_vs_single, source="derived")]
        proposal.provenance = prov
        proposal.within_budget = within_budget(proposal.total_landed, req.budget)
        etas_ok = [c.eta_mins is not None for c in carts]
        failing_eta = report and any(w.code == "ETA_MISS" for w in report.warnings)
        proposal.deadline_met = (all(etas_ok) and not failing_eta) if carts else None
        proposal.last_checked_at = report.checked_at if report else None

        # warnings: final audit + earlier issues the re-plan already handled
        warnings = list(carried)
        seen = set()
        for w in triggered:
            k = (w.code, w.platform, w.product_id)
            if k in seen:
                continue
            seen.add(k)
            warnings.append(w.model_copy(update={"severity": WarningSeverity.INFO, "triggers_replan": False,
                                                 "message": w.message + " – handled by re-planning"}))
        if report:
            warnings += [w for w in report.warnings]
        for m in matches:
            chosen = [c for c in proposal.carts for i in c.items if i.request_item == m.request_item.name]
            if not chosen:
                usable = self._usable(m)
                question = (f"'{m.request_item.name}' isn't available within your rules. Remove it, allow a "
                            f"substitute, or change a constraint?")
                proposal.unresolved_items.append({"item": m.request_item.name, "reason": m.explanation,
                                                  "options": [c.listing.product.name for c in usable][:3],
                                                  "question": question})
                warnings.append(AuditWarning(code="ITEM_UNAVAILABLE", message=question))
        for c in proposal.carts:
            for i in c.items:
                if i.requires_confirmation or i.is_substitute:
                    proposal.requires_confirmation.append(i.listing.product_id)
                    warnings.append(AuditWarning(
                        code="LOW_CONFIDENCE", platform=c.platform, product_id=i.listing.product_id,
                        message=f"'{i.request_item}' → {i.listing.product.name} is a substitute or uncertain match "
                                f"(confidence {i.match_confidence:.2f}) – confirm it before approving"))
        proposal.warnings = warnings
        proposal.status = ProposalStatus.PENDING_APPROVAL if proposal.within_budget else ProposalStatus.OVER_BUDGET
        proposal.summary = self._summary(proposal)
        return proposal

    @staticmethod
    def _summary(p: ShoppingProposal) -> str:
        if not p.carts:
            return "I could not source any item within your rules. See the open questions."
        parts = [f"{c.platform.value} ({len(c.items)} items, ₹{c.landed_total:.2f})" for c in p.carts]
        s = f"Split across {len(p.carts)} app(s): " + ", ".join(parts) + f". Landed total ₹{p.total_landed:.2f}"
        if p.savings_vs_single is not None and p.best_single_platform:
            s += (f", ₹{p.savings_vs_single:.2f} vs the best single app "
                  f"({p.best_single_platform.value}, ₹{p.best_single_total:.2f})")
        if p.budget is not None:
            s += f". Budget ₹{p.budget:.0f}: {'within' if p.within_budget else 'EXCEEDED – cannot be approved'}"
        if p.deadline:
            s += f". Deadline {p.deadline}: {'met' if p.deadline_met else 'check ETA'}"
        if p.requires_confirmation:
            s += f". {len(p.requires_confirmation)} item(s) need your confirmation"
        if p.unresolved_items:
            s += f". {len(p.unresolved_items)} item(s) unresolved"
        return s + "."
