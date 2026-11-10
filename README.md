# CartPilot backend (Member A: Agent & Backend)

**AgentX Hackathon 2026 · PS-07.** An autonomous shopping agent for quick commerce. You tell it what you need. It plans the basket and checks Blinkit, Zepto and Swiggy Instamart through tools. It works out the true landed cost, splits the cart across apps when that is cheaper, and re-verifies everything. Then it **waits for your approval**.

> **It never pays and never places orders.** Approved carts are only *prepared*.
> **Every price, fee and ETA comes from a platform tool.** The data is a mock store: realistic, and swappable through the `PlatformAdapter` interface. We do not scrape live apps.

## Run it on macOS

```bash
cd cartpilot
python3 -m venv .venv && source .venv/bin/activate      # Python 3.11+
pip install -r requirements.txt
cp .env.example .env                                     # optional, everything has defaults
uvicorn app.main:app --reload                            # http://127.0.0.1:8000/docs  (Swagger)
```

Other commands:

```bash
pytest -q                     # full test suite (includes the HTTP tests)
python scripts/demo.py        # 3-minute judging story in the terminal, no server needed
python scripts/evaluate.py    # deck metrics on the 20 fixed baskets -> reports/eval_results.md
CARTPILOT_FIXED_NOW=17:00 uvicorn app.main:app   # pin the clock so "before 8 PM" works at any hour
```

## The agent loop

```
PLAN ─▶ ACT (probe + search) ─▶ OBSERVE (match) ─▶ ACT (optimize) ─▶ VERIFY (Auditor)
                                                        ▲                │ out of stock / price change /
                                                        └── re-plan ◀────┘ fee anomaly / ETA miss / bad offer
VERIFY ─▶ PROPOSE (zero-invented-numbers check) ─▶ AWAITING_APPROVAL ─▶ FINALIZE (prepare carts) ─▶ DONE
                                                        │ edit ─▶ PLAN (new proposal version)
                                                        └ reject ─▶ REJECTED
```

`app/planner/workflow.py` holds the explicit transition table. **Only the approval gate can enter FINALIZE.** Every step and tool call goes into the trace with: action id, phase, tool, input, output, timestamp, reason, status (success / retried / failed / blocked / skipped), duration and attempt number.

## Member A modules

| Module | What it does |
|---|---|
| `core/models.py` | **Shared contracts** for all three members: ShoppingRequest, Product, Listing, Platform, CartItem, PlatformFees, Offer, MatchedProduct, OptimizationResult, ShoppingProposal, ApprovalRequest, AgentAction, AgentState |
| `agents/intent_agent.py` | Turns free text into a ShoppingRequest. Uses a deterministic parser, plus an optional LLM (OpenAI-compatible) whose output is **checked against the user's words** so it can't invent a budget, deadline, brand or item. Asks **one** question if the budget or deadline is missing. An ambiguous time like "by 8" is asked about, never assumed. |
| `planner/orchestrator.py` | Runs the loop: memory → basket → probes → search → match → substitutes → optimize → audit → bounded re-plan (default 3) → proposal. With no items listed, it reuses your last approved basket, or uses a starter weekly basket scaled to household size. |
| `tools/platform_tools.py` | `search_products`, `get_fees`, `get_eta`, `get_offers`. Each call has a hard timeout, retries (2 by default), contract validation and trace logging. A platform that keeps failing is dropped and logged as a re-plan. |
| `tools/matching_tools.py` | Interface to Member B: `match_products`, `find_substitutes`. Includes a conservative rule-based fallback. Confidence ≥ 0.85 is accepted automatically; 0.60–0.84 needs your confirmation; below 0.60 the agent asks you and never uses it. |
| `tools/optimizer_tools.py` | Interface to Member C: `optimize_cart`. The fallback is exact for 3 apps: it tries every set of apps and costs the whole split, so extra fees can't hide. |
| `agents/auditor_agent.py` | Re-queries price, stock, fees, offers and ETA on the actual carts. Flags fee jumps, a bulk pack that costs more per kg, quantities that can't be converted, and **fake discounts** (inflated MRP vs. 4-week price history). Anything it can't check is reported as UNVERIFIED. |
| `tools/approval_tools.py` | **Approval gate.** Approval is tied to one proposal id plus a content fingerprint, and issues a single-use token. Only `actor="user"` can approve. It blocks proposals that are over budget, stale (edited) or have unconfirmed substitutes. `build_cart` only runs with a valid token. |
| `memory/preference_memory.py` | Stores accepted and rejected brands, diet, never-substitute rules and preferred platforms. Read before planning; updated only after approve, reject or edit. Every change is written to `preference_history` and returned, so nothing is overwritten silently. |
| `memory/episode_memory.py` | Stores one row per proposal: the request, proposal, selected products, platform split, savings, decision, edits and timestamps. Kept in a separate table from preference memory. |
| `core/guardrails.py` | Budget cap, diet rules, brand rules, never-substitute rules, the **zero-invented-numbers** check (every number must match a tool output or be calculated from one) and the no-payment check. |
| `api/` + `main.py` | FastAPI routes (kept thin) and a service layer that holds the logic. Swagger is at `/docs`. |

## API

| Method & path | Purpose |
|---|---|
| `POST /api/shopping/plan` | Start a workflow: `{"text": "...", "user_id": "demo-user", "pincode": "560001"}` → 201 |
| `POST /api/shopping/{id}/clarify` | Answer the clarification question: `{"answer": "budget 600 by 9 pm"}` |
| `GET /api/shopping/{id}` | Workflow state: phase, plan, re-plans, next actions |
| `GET /api/shopping/{id}/trace` | Full trace of every action |
| `GET /api/shopping/{id}/proposal` | Proposal: carts, fees, savings, warnings, provenance |
| `POST /api/shopping/{id}/approve` | `{"proposal_id": "...", "confirmed_items": [...], "remind_in_days": 7}` → prepares carts only; optional reorder reminder |
| `POST /api/shopping/{id}/reject` | `{"proposal_id": "...", "reason": "...", "rejected_brands": ["X"]}` |
| `POST /api/shopping/{id}/edit` | `{"proposal_id": "...", "changes": {remove_items, add_items, update_quantities, budget, deadline, reject_brands, prefer_brands, exclude_platforms, never_substitute}}` → new proposal version |
| `GET /api/memory/preferences?user_id=` | Saved preferences + change history |
| `GET /api/memory/episodes?user_id=&limit=` | Previous sessions |
| `GET /api/memory/pantry?user_id=` | What was bought (approved carts only) and what is due for a refill |
| `POST /api/reminders` | `set_reminder`: `{"user_id", "text", "time": ISO \| "in_days": 7, "kind": "reorder\|price_watch\|custom"}` → 201 |
| `GET /api/reminders?user_id=&upcoming_only=` | List reminders |
| `GET /health` | Health, which parts are real and which are fallbacks |
| `POST/DELETE /api/dev/faults` | Demo fault injection on the mock store (**off by default**, enable with `CARTPILOT_ENABLE_FAULTS=true`): `out_of_stock`, `price_change`, `fee_spike`, `eta`, `timeout`, `invalid_response`, `empty_results` |

Error codes: `404` unknown session or proposal · `409` stale proposal / not awaiting approval · `422` over budget, confirmation required, or validation · `403` not the user, or a guardrail violation.

### curl walkthrough

```bash
B=http://127.0.0.1:8000
SID=$(curl -s -X POST $B/api/shopping/plan -H 'Content-Type: application/json' \
  -d '{"text":"Weekly groceries for 2 people, vegetarian, under ₹1,200, delivered before 8 PM."}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
curl -s $B/api/shopping/$SID/proposal | python3 -m json.tool | head -60
PID=$(curl -s $B/api/shopping/$SID | python3 -c 'import sys,json;print(json.load(sys.stdin)["proposal_id"])')

# edit, then approve the NEW proposal id
curl -s -X POST $B/api/shopping/$SID/edit -H 'Content-Type: application/json' \
  -d "{\"proposal_id\":\"$PID\",\"changes\":{\"remove_items\":[\"spinach\"],\"reject_brands\":[\"Nandini\"]}}" > /dev/null
PID=$(curl -s $B/api/shopping/$SID | python3 -c 'import sys,json;print(json.load(sys.stdin)["proposal_id"])')
curl -s -X POST $B/api/shopping/$SID/approve -H 'Content-Type: application/json' -d "{\"proposal_id\":\"$PID\"}"
curl -s $B/api/shopping/$SID/trace | python3 -m json.tool | less

# demo fault: milk goes out of stock on Zepto during verification -> watch the re-plan in the trace
curl -s -X POST $B/api/dev/faults -H 'Content-Type: application/json' \
  -d '{"kind":"out_of_stock","platform":"zepto","sku_id":"milk-nandini-500ml","after_calls":1}'
```

## Integration points for Members B and C

Each interface is loaded by dotted path. If the module isn't there, the built-in fallback runs, so the backend always works on its own.

| Owner | Provide | Signature (shared models from `app/core/models.py`) |
|---|---|---|
| B | `app/agents/matching_agent.py` | `match_products(item: ItemRequest, listings: list[Listing], constraints: MatchConstraints) -> MatchedProduct` · `find_substitutes(item, constraints) -> list[SubstituteSuggestion]` |
| B | Mock store API (`CARTPILOT_STORE_URL`) | `GET /{platform}/search?q=` · `POST /{platform}/fees`, `/eta`, `/offers`, `/cart` with `{"cart": [CartLine]}`, as listed in `adapters/http_adapter.py` |
| B | `app/adapters/price_history.py` | `get_price_history(platform, sku_id) -> [{"days_ago", "price"}]` |
| B | Brand list | Replace the temporary `KNOWN_BRANDS` import in the orchestrator |
| C | `app/optimizer/optimizer.py` | `optimize_cart(items: list[MatchedProduct], constraints: OptimizationConstraints) -> OptimizationResult`. Fill in `best_single_platform` and `best_single_items` so savings can be verified. If it raises, the fallback runs and the trace says so. |
| C | UI | Read `/proposal` (`carts`, `warnings`, `requires_confirmation`, `unresolved_items`, `provenance`) and `/trace`. Send approve, edit or reject with the `proposal_id`. |

Optimizer figures are planning estimates only. The **proposal is rebuilt from the Auditor's fresh tool calls**, and `verify_proposal_numbers` rejects any number it can't trace.

## Tests

`pytest -q` runs 16 required scenarios plus edge cases:

1. normal request
2. missing budget
3. missing deadline
4. out of stock during verification
5. platform timeout (retries, then the platform is dropped; also a real hanging adapter)
6. price change during verification
7. fee anomaly
8. over budget (shown but blocked until edited)
9. deadline (slow platform excluded; ETA miss found during verification)
10. low-confidence substitute needs confirmation
11. reject
12. approve
13. a rejected brand is remembered across a process restart
14. never-substitute (both the brand rule and the item rule)
15. unauthorized cart preparation blocked (no token, forged token, reused token, token from another session, tampered proposal, FINALIZE outside the gate, non-user actor, "I approve" written inside the request text)
16. no payment possible (no payment methods or routes; a store that claims payment is rejected)

The approval gate is also checked over 20 varied runs, and the invented-number checker is tested against 6 kinds of tampering.

## Evaluation (deck numbers)

`python scripts/evaluate.py` runs the 20 baskets in `tests/baskets/baskets.json`, 8 of them with an injected fault. It writes `reports/eval_results.md` and `.json` with:
- average saving vs. the best single app
- constraint satisfaction
- re-plan success
- approval-gate stops
- invented numbers found

`--labels <file>` adds matching accuracy. `tests/baskets/match_labels.example.json` is only an example: Member B should supply the real hand-labelled pairs and fill in each basket's `expected` split. **Quote only numbers this script measured, and label them as mock data.**

## Git setup (first push)

GitHub already has a README and LICENSE, so merge them in once. Our README wins.

```bash
cd ~/Desktop/cartpilot
git init -b main && git remote add origin https://github.com/thisisraviteja1/Cartpilot.git
git add . && git commit -m "Member A: agent, auditor, approval gate, memory, FastAPI backend"
git fetch origin && git merge origin/main --allow-unrelated-histories -X ours -m "Merge GitHub main"
git push -u origin main                      # first push only; afterwards work on feature/planner
git checkout -b feature/planner && git push -u origin feature/planner
```

## Changes after the audit

- **Fixed:** substitution and platform-failure events no longer use up the verification re-plan limit. The limit now counts per proposal version.
- **Fixed:** an edit is fully validated **before** anything changes. A bad deadline, an unknown item, a zero quantity, an empty edit, or removing every item now returns 4xx and leaves the proposal approvable. Before, a bad edit returned 500 and the proposal could never be approved, or a weekly basket was invented.
- **Fixed:** intent phrasing: "for 1 person" is the household size, not an item, and "by 9 tonight" means 21:00.
- **Added:** a per-session lock (a double-click on Approve prepares carts once), and a visible fallback when a teammate's matching module fails.
- **Added:** `set_reminder`, pantry memory, price memory (used for fake-discount checks when no seeded history exists), and unit price per item.
- **Changed:** brands you approved before are now a *soft* preference (at most 10% extra per item). Preferred platforms break ties.
- **Added:** the evaluation runner and 20 baskets. Fault injection is now off by default.

## Known limitations

- The mock store and price history are **temporary** stand-ins until Member B's API is ready. Savings depend on the mock data.
- Approval tokens are kept in memory. A server restart in the middle of an approval means the user has to approve again (the session itself is persisted).
- The deterministic parser covers common English phrasing. Hindi and other regional input needs the LLM path.
- No authentication. `user_id` is passed in by the client, which is fine for a hackathon and not for production.
- Session locks are per process. Run a single uvicorn worker, which is the default.
- Reminders are stored and listed. Delivering them (push or calendar) is up to the UI or a later integration.
- A timed-out tool call keeps running in a background thread, because Python cannot cancel threads. Its result is discarded.
