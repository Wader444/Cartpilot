# CartPilot – an autonomous shopping agent for quick commerce

**AgentX Hackathon 2026 · PS-07**

You tell CartPilot what you need, for example *"Weekly groceries for 4 people, vegetarian, under ₹2,500, delivered before 9 PM."* It plans the basket and checks **Blinkit, Zepto and Swiggy Instamart** through tools. It works out the **true landed cost**: item prices plus handling, delivery, small-cart and surge fees, minus offers. It **splits the cart across apps when that is cheaper**, re-verifies everything, and then **waits for your approval**.

> **It never pays and never places orders.** Approved carts are only *prepared*; you pay inside each app.
> **Every price, fee and ETA comes from a tool.** The data is a realistic **mock store** (we do not scrape live apps). Real platforms plug in through the `PlatformAdapter` interface.

---

## Why an agent, not a chatbot

* **Live data:** prices, stock, fees and ETAs change by the minute, so the agent fetches them with tools instead of guessing.
* **Multi-step:** it understands the goal, searches every app, matches the same product across apps, optimises the split, verifies, then proposes. Each step depends on the last.
* **It acts:** the result is prepared carts, not an answer, so it needs a hard approval gate.
* **It remembers:** brands you accept or reject, your diet, your usual basket.

## What you see in the demo

1. **Plan:** a 4-person weekly basket is split across all three apps, with a full fee breakdown per app.
2. **Something goes wrong:** curd goes out of stock on Zepto, and Blinkit adds a ₹15 surge between planning and verification.
3. **The Auditor catches both.** The agent **re-plans**, and the trace shows exactly why.
4. **Proposal:** ₹1,715.90 split vs ₹1,995.00 on the best single app, so **₹279.10 saved (14%)**.
5. **Approve:** three carts are prepared, `payment_executed = false` on every one, and a reorder reminder is set.
6. **Next week:** it reuses your approved basket, brands and diet.

## Results

Measured on the mock store with `python scripts/evaluate.py`.

| Metric | Result |
|---|---|
| Demo basket | **₹279.10 saved (14%)** with a 3-app split |
| 20 fixed test baskets | A split beat the best single app on **7 of 20** (the larger baskets). When it did, it saved **6.4%** on average and **up to 15.3%**. Small baskets correctly stay on one app, because extra fees would cost more. |
| Approval gate | **100%** of runs stopped for approval; nothing is ever prepared without it |
| Invented numbers | **0**: every price, fee and ETA in a proposal is traced to a tool output |
| Injected failures (out of stock, price change, fee spike, ETA miss, timeout, bad data) | **8 of 8** recovered by re-planning |
| Optimiser | OR-Tools plans are checked against a **brute-force optimum** |
| Automated tests | **149 passing** |

All figures are on mock data and can be reproduced with the commands below.

---

## Run it

Requires Python 3.11+ (tested on 3.13, macOS).

```bash
git clone https://github.com/Wader444/Cartpilot.git && cd Cartpilot
python -m venv .venv
# On Windows: .venv\Scripts\Activate.ps1
# On macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

**Everything in one go (Cross-Platform):**
This validates tests, data graph checks, demo story, evaluation, and starts the API & Streamlit UI together:
```bash
python scripts/run_all.py     # Works on Windows, macOS, and Linux
# Alternatively on macOS/Linux: bash scripts/run_all.sh
```

**Or step by step:**
```bash
pytest -q                          # 149 tests
python scripts/demo.py             # the full demo story in the terminal (no server needed)
python scripts/evaluate.py         # results on the 20 test baskets -> reports/eval_results.md
python integration_check.py        # product data, matching and price-history checks
```

**The app (API + UI):**
```bash
# Terminal 1 – API. Faults are enabled only for the demo toggle; the fixed clock makes "before 9 PM" work at any time of day.
CARTPILOT_ENABLE_FAULTS=true CARTPILOT_FIXED_NOW=17:00 python -m uvicorn app.main:app --reload --reload-dir app
# Terminal 2 – UI
USE_MOCK=0 python -m streamlit run ui/app.py
```
In the UI sidebar, click **Load demo basket**, tick **Inject demo faults**, then click **Plan my cart**. Open **Agent trace** to see the re-plan, then click **Approve and prepare carts**.
API docs (Swagger): http://127.0.0.1:8000/docs

### Streamlit Community Cloud

For a Streamlit-only deployment, set the main file to `ui/app.py`. The UI
uses `CARTPILOT_UI_MODE=auto` by default: it uses a running FastAPI service
when available and automatically embeds the same backend when Streamlit Cloud
cannot reach `localhost:8000`. Set `USE_MOCK=0` to exercise the real planner
and keep the built-in mock catalog; no LLM, Neo4j, or store API is required.
For a fully explicit Cloud configuration, add `CARTPILOT_UI_MODE=in_process`
to the app's environment/secrets.

---

## How it works

```
User goal ─▶ PLAN ─▶ ACT (search, fees, ETA, offers) ─▶ OBSERVE (match products) ─▶ ACT (optimise split)
                                   ▲                                                   │
                                   └──────────── re-plan ◀──── VERIFY (Auditor) ◀──────┘
                                     out of stock · price change · fee jump · ETA miss · bad offer
VERIFY ─▶ PROPOSE (zero-invented-numbers check) ─▶ AWAITING APPROVAL ─▶ prepare carts (no payment) ─▶ DONE
                                                         │ edit ─▶ re-plan (new proposal version)
                                                         └ reject ─▶ nothing prepared
```

| Part | What it does | Code |
|---|---|---|
| **Intent agent** | Turns free text into a structured request: items, quantities, budget, deadline, diet and brand rules. It asks **one** question if the budget or deadline is missing and never guesses an ambiguous time. An optional LLM can be used; its output is checked against the user's own words. | `app/agents/intent_agent.py` |
| **Planner** | An explicit state machine with a full action log: every tool call's input, output, reason, status and time. Re-planning is bounded. | `app/planner/` |
| **Platform tools** | `search_products`, `get_fees`, `get_eta`, `get_offers`, with timeouts, retries and response validation. A failing app is dropped and logged. | `app/tools/platform_tools.py` |
| **Product matching** | Graph first (Neo4j `SAME_AS` / `SUBSTITUTE_FOR` seed), then name/brand/quantity similarity, with attribute rules as a check. ≥ 0.85 is accepted automatically, 0.60–0.84 needs the user to confirm, and < 0.60 makes it ask. It never guesses. | `app/agents/matching.py`, `app/agents/matching_agent.py`, `data/graph_seed/` |
| **Optimiser** | Models the cart split as a constraint problem with **Google OR-Tools CP-SAT**: one listing per item, free-delivery thresholds, small-cart fees, one coupon per cart, and only apps that meet the deadline. An exhaustive split search runs alongside as a baseline. Every chosen cart is re-checked by the **landed-cost engine** (integer paise). | `app/optimizer/` |
| **Auditor** | Independently re-queries price, stock, fees, offers and ETA on the actual carts just before proposing. It flags fee jumps, bulk-pack traps and fake discounts (an inflated MRP compared with price history). Anything it can't check is shown as unverified. | `app/agents/auditor_agent.py` |
| **Approval gate** | Approval is bound to one proposal and its exact contents with a single-use token. Over-budget, edited (stale) or unconfirmed-substitute proposals cannot be approved. There is **no payment code** anywhere. | `app/tools/approval_tools.py` |
| **Memory** | Preferences (accepted/rejected brands, diet, never-substitute) with full change history; episodes (every proposal and decision); pantry; observed prices; reminders. | `app/memory/` |
| **Guardrails** | Budget cap, diet and brand rules, never-substitute, zero invented numbers, no payment capability. | `app/core/guardrails.py` |
| **API + UI** | A FastAPI backend (Swagger at `/docs`) and a Streamlit UI with approve, reject, edit, substitute confirmation, clarification and the agent trace. | `app/api/`, `app/main.py`, `ui/` |

## Team

| Member | Lane |
|---|---|
| **A – Agent & Backend** | Intent agent, planner, Auditor, approval gate, memory, FastAPI backend, guardrails, integration |
| **B – Data & Product Graph** | Mock catalog, platform adapter, Neo4j graph seed, product matching, price history, test baskets |
| **C – Optimizer & UI** | Landed-cost engine, unit price, optimiser, Streamlit UI |

---

## API reference

| Method & path | Purpose |
|---|---|
| `POST /api/shopping/plan` | Start a plan: `{"text": "...", "user_id": "demo-user"}` → 201 |
| `POST /api/shopping/{id}/clarify` | Answer the clarification question: `{"answer": "budget 600 by 9 pm"}` |
| `GET /api/shopping/{id}` | Workflow state |
| `GET /api/shopping/{id}/proposal` | Proposal: carts, fees, savings, warnings, number provenance |
| `GET /api/shopping/{id}/trace` | Every tool call and decision |
| `POST /api/shopping/{id}/approve` | `{"proposal_id", "confirmed_items": [], "remind_in_days": 7}` → prepares carts only |
| `POST /api/shopping/{id}/reject` | `{"proposal_id", "reason", "rejected_brands": []}` |
| `POST /api/shopping/{id}/edit` | `{"proposal_id", "changes": {remove_items, add_items, update_quantities, budget, deadline, reject_brands, prefer_brands, exclude_platforms, never_substitute}}` → new proposal version |
| `GET /api/memory/preferences` · `/episodes` · `/pantry` | Memory (`?user_id=`) |
| `POST /api/reminders` · `GET /api/reminders` | Reorder and price-watch reminders |
| `GET /health` | Health, and which components are active |
| `POST` / `DELETE /api/dev/faults` | Demo fault injection, **off by default** (`CARTPILOT_ENABLE_FAULTS=true` turns it on) |

Errors: `404` unknown session or proposal · `409` stale proposal or not awaiting approval · `422` over budget, confirmation required or invalid input · `403` not the user, or a guardrail.

<details>
<summary>curl walkthrough</summary>

```bash
B=http://127.0.0.1:8000
R=$(curl -s -X POST $B/api/shopping/plan -H 'Content-Type: application/json' \
  -d '{"text":"Weekly groceries for 4 people, vegetarian, under ₹2,500, delivered before 9 PM."}')
SID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
PID=$(echo "$R" | python3 -c 'import sys,json;print(json.load(sys.stdin)["proposal_id"])')
curl -s $B/api/shopping/$SID/proposal | python3 -m json.tool | head -60
curl -s -X POST $B/api/shopping/$SID/approve -H 'Content-Type: application/json' \
  -d "{\"proposal_id\":\"$PID\",\"remind_in_days\":7}"
curl -s $B/api/shopping/$SID/trace | python3 -m json.tool | less
```
</details>

## Configuration

All settings are optional. Copy `.env.example` to `.env`. The notable ones:

| Variable | Meaning |
|---|---|
| `CARTPILOT_DATABASE_URL` | SQLite by default; Postgres works through SQLAlchemy |
| `CARTPILOT_FIXED_NOW` | Pins the clock (e.g. `17:00`) for repeatable demos |
| `CARTPILOT_ENABLE_FAULTS` | Enables the demo fault endpoint (keep it off for anything public) |
| `CARTPILOT_STORE_URL` | Uses an HTTP store API instead of the in-process mock store |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Enables the Neo4j graph path for matching (otherwise similarity matching runs) |
| `LLM_BASE_URL` / `LLM_MODEL` | Optional OpenAI-compatible LLM for intent parsing (e.g. local Ollama); otherwise a deterministic parser is used |

## Tests

`pytest -q` runs 149 tests. They cover the 16 required scenarios and edge cases:

* normal request; missing budget or deadline
* out of stock during verification; platform timeout; price change; fee anomaly
* over budget; missed deadline; low-confidence substitute
* reject; approve; brand memory across restarts; never-substitute
* cart preparation blocked without approval: no token, forged, reused or tampered token, a non-user approver, and "I approve" inside the request text
* no payment possible

They also test the UI client against the real API, product matching inside the agent, the landed-cost engine, and the optimiser against brute force.

## Known limitations

* **Mock data.** Prices, fees and stock come from a realistic mock store, not live apps, so the savings figures are mock results.
* **Neo4j is optional at runtime.** With Neo4j running and `NEO4J_PASSWORD` set, matching uses the graph; otherwise it uses similarity plus attribute rules.
* **No authentication.** `user_id` comes from the client; fine for a demo, not for production.
* **Single process.** Session locks and approval tokens live in one server process, so run one uvicorn worker.
* **Intent parsing** covers common English phrasing; Hindi and regional-language input need the LLM path.

MIT License; see `LICENSE`.
