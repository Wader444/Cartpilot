"""Intent Agent: free text -> ShoppingRequest.

Two parsers, same output contract:
  * LLM parser (optional) – any OpenAI-compatible endpoint (Ollama, Groq, ...).
    Its output is validated and every constraint is checked against the raw
    text, so the LLM cannot invent a budget, deadline, brand or item.
  * Deterministic parser – regex/rule based, always available, used when no
    LLM is configured or the LLM fails.

Missing budget or deadline -> exactly one clarification question. Missing
constraints are never filled in by guessing.
"""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from pydantic import ValidationError

from app.core.config import Clock, Settings, get_settings
from app.core.models import DietPreference, IntentResult, ItemRequest, ShoppingRequest
from app.core.units import normalize_unit

NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "half": 0.5, "couple": 2, "dozen": 12}
_UNIT_RE = (r"kgs?|kilos?|kilograms?|grams?|gms?|g|gr|litres?|liters?|ltrs?|lt|l|ml|mls|millilitres?|pcs?|pieces?"
            r"|packs?|packets?|pkts?|bottles?|loaf|loaves|units?|nos|dozen")
_NUM = r"\d+(?:\.\d+)?|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True))

FILLER = re.compile(r"^(?:(?:please|pls|i|we|also|and|then|just|kindly)\s+)*(?:(?:need|want|buy|get|order|add|"
                    r"would like|'d like|like|grab|pick up|me|some|to buy|to order)\s+)*", re.I)
NON_ITEMS = {"groceries", "grocery", "weekly groceries", "weekly grocery", "stuff", "things", "items", "essentials",
             "the week", "week", "vegetarian", "veg", "vegan", "delivery", "it", "them", "everything", "basics",
             "weekly", "monthly groceries", "people", "home", "order", "tonight", "today", "tomorrow", "asap",
             "now", "this evening", "this morning", "urgently", "quickly", "fast"}


class IntentAgent:
    def __init__(self, settings: Optional[Settings] = None, clock: Optional[Clock] = None,
                 known_brands: Optional[list[str]] = None,
                 llm_client: Optional[Callable[[str], str]] = None):
        self.settings = settings or get_settings()
        self.clock = clock or Clock(self.settings.timezone, self.settings.fixed_now)
        self.known_brands = sorted(known_brands or [], key=len, reverse=True)
        self._llm = llm_client or (self._http_llm if self.settings.llm_enabled else None)

    # ------------------------------------------------------------------ public
    def parse(self, text: str, pincode: Optional[str] = None) -> IntentResult:
        text = (text or "").strip()
        result: Optional[IntentResult] = None
        if self._llm is not None:
            result = self._parse_llm(text)
        if result is None:
            req, notes = self._parse_deterministic(text)
            result = IntentResult(request=req, parser="deterministic", notes=notes)
        if pincode:
            result.request.pincode = pincode
        return self._finalise(result)

    def merge_clarification(self, request: ShoppingRequest, answer: str) -> IntentResult:
        """Fill ONLY the missing pieces from the user's answer."""
        extra, notes = self._parse_deterministic(answer)
        merged = request.model_copy(deep=True)
        merged.raw_text = f"{request.raw_text} | {answer}".strip(" |")
        if merged.budget is None and extra.budget is not None:
            merged.budget = extra.budget
            merged.constraint_sources["budget"] = "user"
        if merged.deadline is None and extra.deadline is not None:
            merged.deadline = extra.deadline
            merged.constraint_sources["deadline"] = "user"
        if not merged.items and extra.items:
            merged.items = extra.items
        if merged.dietary_preference is None and extra.dietary_preference is not None:
            merged.dietary_preference = extra.dietary_preference
        for f in ("preferred_brands", "avoid_brands", "never_substitute"):
            getattr(merged, f).extend(x for x in getattr(extra, f) if x not in getattr(merged, f))
        return self._finalise(IntentResult(request=merged, parser="deterministic", notes=notes))

    # ------------------------------------------------------------------ helpers
    def _finalise(self, result: IntentResult) -> IntentResult:
        req = result.request
        missing = req.missing_constraints()
        wants_template = bool(re.search(r"grocer|essentials|weekly|monthly|basics", req.raw_text, re.I))
        if not req.items and not wants_template:
            missing = ["items"] + missing
        result.missing = missing
        result.clarification_question = self._question(missing)
        return result

    @staticmethod
    def _question(missing: list[str]) -> Optional[str]:
        if not missing:
            return None
        parts = {"items": "which items you need", "budget": "your budget (₹)",
                 "deadline": "the latest delivery time (e.g. 8 PM)"}
        asked = [parts[m] for m in missing]
        if len(asked) == 1:
            body = asked[0]
        else:
            body = ", ".join(asked[:-1]) + " and " + asked[-1]
        return f"Before I plan this, please tell me {body}."

    # ------------------------------------------------------------------ deterministic
    def _parse_deterministic(self, text: str) -> tuple[ShoppingRequest, list[str]]:
        """Pure function of the text (thread-safe): returns the request and parser notes."""
        notes: list[str] = []
        s = " " + text.replace("’", "'").replace("–", "-") + " "
        consumed: list[tuple[int, int]] = []

        def take(m: re.Match) -> None:
            consumed.append(m.span())

        # budget -------------------------------------------------------------
        budget = None
        amount = r"\s*[:\-]?\s*(?:rs\.?|inr|₹|\?|rupees)?\s*(\d[\d,]*(?:\.\d+)?)\s*(k\b)?(?!\s*(?:min|hour|hr|h\b|kg|g\b|l\b|ml|litre|liter|pcs))\s*(?:rs\.?|rupees|inr|/-|bucks)?"
        bre_strong = re.compile(r"budget(?:\s+(?:of|is))?" + amount, re.I)
        bre = re.compile(r"(?:under|below|within|max(?:imum)?|up\s*to|upto|less\s+than|"
                         r"not\s+more\s+than|spend(?:ing)?(?:\s+at\s+most)?)" + amount, re.I)
        m = bre_strong.search(s) or bre.search(s)
        if m:
            budget = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
            take(m)
        else:
            m = re.search(r"(?:₹|\?|rs\.?\s*|inr\s*)(\d[\d,]*(?:\.\d+)?)\s*(?:budget)", s, re.I)
            if m:
                budget = float(m.group(1).replace(",", ""))
                take(m)

        # deadline -----------------------------------------------------------
        deadline = None
        now = self.clock.now()
        dre = re.compile(r"(?:deliver(?:ed|y)?\s+)?(?:before|by|until|till|no\s+later\s+than|latest)\s+"
                         r"(\d{1,2})(?:[:.](\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?(?=\W)", re.I)
        m = dre.search(s)
        if m:
            hh, mm, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
            if ap == "pm" and hh < 12:
                hh += 12
            elif ap == "am" and hh == 12:
                hh = 0
            evening = re.compile(r"\s*(tonight|tonite|this\s+evening|in\s+the\s+evening)\b", re.I).match(s, m.end())
            morning = re.compile(r"\s*(this\s+morning|in\s+the\s+morning)\b", re.I).match(s, m.end())
            if not ap and 1 <= hh <= 11 and evening:      # "by 9 tonight" -> 21:00
                ap, hh = "pm", hh + 12
                take(evening)
            elif not ap and 1 <= hh <= 11 and morning:    # "by 8 this morning" -> 08:00
                ap = "am"
                take(morning)
            if ap or hh >= 13 or m.group(2):
                if 0 <= hh <= 23:
                    deadline = f"{hh:02d}:{mm:02d}"
            else:
                notes.append(f"Deadline '{m.group(0).strip()}' is ambiguous (AM or PM?) – not assumed")
            take(m)
        else:
            m = re.search(r"(?:before|by)\s+(noon|midnight)", s, re.I)
            if m:
                deadline = "12:00" if m.group(1).lower() == "noon" else "23:59"
                take(m)
            else:
                m = re.search(r"(?:within|in(?:\s+the\s+next)?)\s+(\d+)\s*(min(?:ute)?s?|h(?:ou)?rs?|hours?)\b", s, re.I)
                if m:
                    n = int(m.group(1))
                    delta = timedelta(hours=n) if m.group(2).lower().startswith("h") else timedelta(minutes=n)
                    t = now + delta
                    deadline = f"{t.hour:02d}:{t.minute:02d}"
                    notes.append(f"Relative deadline '{m.group(0).strip()}' -> {deadline}")
                    take(m)
        m = re.search(r"deliver(?:ed|y)?\s*(?:asap|today|tonight)?", s, re.I)
        if m:
            take(m)

        # diet ---------------------------------------------------------------
        diet = None
        for pattern, value in [(r"\bvegan\b", DietPreference.VEGAN),
                               (r"\bnon[\s-]?veg(?:etarian)?\b", DietPreference.NON_VEGETARIAN),
                               (r"\beggetarian\b", DietPreference.EGGETARIAN),
                               (r"\b(?:pure\s+)?veg(?:etarian)?\b|\bjain\b", DietPreference.VEGETARIAN)]:
            m = re.search(pattern, s, re.I)
            if m:
                diet = value
                take(m)
                if "jain" in m.group(0).lower():
                    notes.append("Jain diet treated as vegetarian; root vegetables are not filtered automatically")
                break

        # household ----------------------------------------------------------
        household = None
        m = re.search(r"for\s+(\d{1,2}|one|two|three|four|five|six)\s*(?:people|persons?|ppl|adults?|members?|of\s+us)\b"
                      r"|family\s+of\s+(\d{1,2}|one|two|three|four|five|six)\b", s, re.I)
        if m:
            raw = (m.group(1) or m.group(2)).lower()
            household = int(NUM_WORDS.get(raw, raw))
            take(m)

        # brand rules --------------------------------------------------------
        brand_alt = "|".join(re.escape(b) for b in self.known_brands) or r"(?!x)x"
        avoid, prefer, never = [], [], []
        for m in re.finditer(rf"(?:no|avoid|not|don'?t\s+want|without|except|never)\s+({brand_alt})\b", s, re.I):
            avoid.append(self._brand_case(m.group(1)))
            take(m)
        for m in re.finditer(rf"(?:prefer(?:ably)?|preferred\s+brand(?:\s+is)?)\s+({brand_alt})\b", s, re.I):
            prefer.append(self._brand_case(m.group(1)))
            take(m)
        for m in re.finditer(rf"(?:only|strictly|must\s+be|exactly)\s+({brand_alt})\s+([a-z ]+?)"
                             rf"(?:\s+\d[\w. ]*)?\s*(?=,|\.(?!\d)|;|\band\b|$)",
                             s, re.I):
            never.append(f"{m.group(1).lower()} {m.group(2).strip().lower()}")
        for m in re.finditer(r"(?:never|don'?t|do\s+not|no)\s+substitut\w*\s+(?:for\s+)?(?:the\s+)?([a-z ]+?)(?=,|\.|;|\band\b|$)",
                             s, re.I):
            never.append(m.group(1).strip().lower())
            take(m)

        # items --------------------------------------------------------------
        masked = list(s)
        for a, b in consumed:
            for i in range(a, b):
                masked[i] = ","
        rest = "".join(masked)
        rest = re.sub(r"\b(?:only|strictly|must\s+be|exactly)\s+", " ", rest, flags=re.I)
        items: list[ItemRequest] = []
        for chunk in re.split(r"[,;\n.+&]|\band\b|\bplus\b|\bwith\b", rest, flags=re.I):
            it = self._parse_item(chunk)
            if it and not any(x.name == it.name and x.brand == it.brand for x in items):
                items.append(it)

        sources = {}
        if budget is not None:
            sources["budget"] = "user"
        if deadline is not None:
            sources["deadline"] = "user"
        return ShoppingRequest(raw_text=text, items=items, household_size=household, dietary_preference=diet,
                               budget=budget, deadline=deadline, preferred_brands=prefer, avoid_brands=avoid,
                               never_substitute=never, constraint_sources=sources), notes

    def _brand_case(self, raw: str) -> str:
        for b in self.known_brands:
            if b.lower() == raw.lower():
                return b
        return raw

    def _parse_item(self, chunk: str) -> Optional[ItemRequest]:
        c = " ".join(chunk.strip().split())
        c = re.sub(r"^[^\w₹]+|[^\w)]+$", "", c)
        c = FILLER.sub("", c).strip()
        c = re.sub(r"\b(?:weekly|monthly|daily)\s+", "", c, flags=re.I)
        if not c or not re.search(r"[a-z]", c, re.I):
            return None
        qty, unit, name, assumed = 1.0, "pcs", c, True
        m = re.match(rf"^({_NUM})\s*({_UNIT_RE})?\.?\s+(?:of\s+)?(.+)$", c, re.I)
        if m:
            q = m.group(1).lower()
            qty = float(NUM_WORDS.get(q, q)) if not re.match(r"\d", q) else float(q)
            unit = normalize_unit(m.group(2)) if m.group(2) else "pcs"
            name, assumed = m.group(3), False
        else:
            m = re.match(rf"^(.+?)\s+({_NUM})\s*({_UNIT_RE})\.?$", c, re.I)
            if m:
                q = m.group(2).lower()
                qty = float(NUM_WORDS.get(q, q)) if not re.match(r"\d", q) else float(q)
                unit, name, assumed = normalize_unit(m.group(3)), m.group(1), False
            else:
                m = re.match(r"^(\d+(?:\.\d+)?)(kg|g|l|ml)\s+(.+)$", c, re.I)
                if m:
                    qty, unit, name, assumed = float(m.group(1)), normalize_unit(m.group(2)), m.group(3), False
        if unit == "dozen":
            qty, unit = qty * 12, "pcs"
        name = re.sub(r"^(?:of|the|some|fresh)\s+", "", name.strip(), flags=re.I).strip().lower()
        name = re.sub(r"\s+(?:please|pls)$", "", name)
        if (not name or name in NON_ITEMS or len(name) > 40
                or re.search(r"\d{3,}|budget|deliver|people|person|grocer|essentials", name)):
            return None
        brand = None
        for b in self.known_brands:
            if name.startswith(b.lower() + " "):
                brand, name = b, name[len(b) + 1:].strip()
                break
        if not name or name in NON_ITEMS:
            return None
        return ItemRequest(name=name, quantity=qty, unit=unit, brand=brand, quantity_assumed=assumed)

    # ------------------------------------------------------------------ LLM
    _SYSTEM = (
        "Extract a grocery shopping request as JSON with keys: items (list of {name, quantity, unit, brand}), "
        "household_size, dietary_preference (vegetarian|vegan|eggetarian|non_vegetarian|null), budget (number "
        "or null), deadline (HH:MM 24h or null), preferred_brands, avoid_brands, never_substitute. "
        "Units: kg, g, litre, ml, pcs. Use null for anything the user did not state. Never guess a budget, "
        "deadline or price. Return JSON only.")

    def _http_llm(self, text: str) -> str:
        s = self.settings
        body = json.dumps({"model": s.llm_model, "temperature": 0,
                           "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": self._SYSTEM},
                                        {"role": "user", "content": text}]}).encode()
        headers = {"Content-Type": "application/json"}
        if s.llm_api_key:
            headers["Authorization"] = f"Bearer {s.llm_api_key}"
        req = urllib.request.Request(s.llm_base_url.rstrip("/") + "/chat/completions", data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=s.llm_timeout_s) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]

    def _parse_llm(self, text: str) -> Optional[IntentResult]:
        try:
            raw: dict[str, Any] = json.loads(self._llm(text))
            items = []
            for it in raw.get("items") or []:
                if not it.get("name"):
                    continue
                items.append(ItemRequest(name=it["name"], quantity=float(it.get("quantity") or 1),
                                         unit=normalize_unit(it.get("unit")), brand=it.get("brand"),
                                         quantity_assumed=it.get("quantity") is None))
            req = ShoppingRequest(
                raw_text=text, items=items, household_size=raw.get("household_size"),
                dietary_preference=raw.get("dietary_preference"), budget=raw.get("budget"),
                deadline=raw.get("deadline"), preferred_brands=raw.get("preferred_brands") or [],
                avoid_brands=raw.get("avoid_brands") or [], never_substitute=raw.get("never_substitute") or [])
        except (ValueError, ValidationError, KeyError, TypeError, OSError):
            return None
        notes = self._ground_llm_output(req, text)
        for f in ("budget", "deadline"):
            if getattr(req, f) is not None:
                req.constraint_sources[f] = "user"
        return IntentResult(request=req, parser="llm", notes=notes)

    def _ground_llm_output(self, req: ShoppingRequest, text: str) -> list[str]:
        """Drop anything the LLM produced that is not supported by the user's words."""
        notes, low = [], text.lower()
        digits = {d.replace(",", "") for d in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
        if req.budget is not None and not any(abs(float(d) - req.budget) < 0.01 or abs(float(d) * 1000 - req.budget) < 0.01
                                              for d in digits if d.replace(".", "", 1).isdigit()):
            notes.append(f"LLM budget {req.budget} not found in the text – discarded")
            req.budget = None
        if req.deadline is not None:
            hh = int(req.deadline.split(":")[0])
            h12 = hh - 12 if hh > 12 else (12 if hh == 0 else hh)
            if not re.search(rf"\b0?({hh}|{h12})\b|noon|midnight|within|\bin\s+\d", low):
                notes.append(f"LLM deadline {req.deadline} not found in the text – discarded")
                req.deadline = None
        for f in ("preferred_brands", "avoid_brands"):
            kept = [b for b in getattr(req, f) if b.lower() in low]
            if len(kept) != len(getattr(req, f)):
                notes.append(f"LLM {f} not found in the text – discarded")
            setattr(req, f, kept)
        kept_items = [i for i in req.items if any(tok in low for tok in i.name.split())]
        if len(kept_items) != len(req.items):
            notes.append("LLM produced item(s) not in the text – discarded")
        req.items = kept_items
        return notes
