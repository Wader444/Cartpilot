# CartPilot Implementation Plan

## Goal

Make CartPilot reliable for a live AgentX hackathon demo and deployable on the
target hosting platform. The priority is that a judge can enter a shopping
request, receive a correct plan, see the agent trace and recovery behavior, and
approve a prepared cart without the request silently failing.

## What I found before implementation

1. The project contains a FastAPI backend and a Streamlit frontend.
2. The Streamlit UI is configured to call `http://localhost:8000` when
   `USE_MOCK=0`.
3. Streamlit Cloud normally launches only `streamlit run ui/app.py`; it does
   not keep a second FastAPI process running on the same instance.
4. The combined `scripts/run_all.py` runner is appropriate for a container
   host such as Railway or Render, but is not a reliable Streamlit Cloud
   entrypoint.
5. The frontend imports `altair` and `pandas`, so those runtime dependencies
   must be explicitly available in the deployment dependency set.
6. The repository already has deterministic mock-store data, an approval gate,
   fault injection, API tests, and an evaluation suite. These should remain
   the source of truth for the demo rather than introducing live scraping.
7. The live URL redirected to Streamlit authentication during the initial
   check, so the public runtime behavior must also be verified from the
   deployment logs or an accessible deployment after the changes.

These are initial findings, not a claim that every runtime issue has been
resolved yet.

## Planned implementation

### Phase 1 - Reproduce and baseline

- Run the existing tests, data checks, demo, and evaluation.
- Start the backend and UI locally.
- Exercise the same request flow used by the hosted app:
  - normal basket;
  - missing budget/deadline clarification;
  - demo fault recovery;
  - proposal display;
  - approval and prepared-cart output.
- Record any failures with the exact request, response, and traceback.

### Phase 2 - Make the hosting model explicit

- Add a supported single-process deployment mode for Streamlit Cloud, with
  the UI invoking the in-process service rather than assuming `localhost:8000`.
- Preserve the existing FastAPI mode for Docker/Railway/Render and local API
  development.
- Make mode selection explicit and safe through environment variables.
- Keep demo fault injection disabled by default outside the demo mode.
- Add a health/status presentation in the UI so backend mode and degraded
  states are visible instead of looking like a request that did nothing.

### Phase 3 - Harden request processing

- Normalize and validate frontend/API responses consistently.
- Surface timeout, validation, clarification, and planning failures as useful
  user-facing messages.
- Prevent stale session/proposal state from being reused after a failed or
  edited request.
- Verify that approval remains bound to the exact proposal version.
- Preserve the no-payment/no-order guarantee.

### Phase 4 - Improve the judge experience

- Make the one-click demo scenarios deterministic and easy to discover.
- Show the request interpretation, platform checks, fee breakdown, fault
  recovery, savings, warnings, and approval boundary in a clear sequence.
- Ensure the UI works when optional integrations such as Neo4j or an LLM are
  absent.
- Improve empty, loading, and error states without hiding failures.

### Phase 5 - Deployment and verification

- Update dependency/configuration documentation and deployment instructions.
- Validate the chosen deployment command and required secrets/configuration.
- Run targeted tests, then the full existing test/evaluation suite.
- Perform a clean deployment smoke test using the exact hosted URL.
- Provide a final demo script and a short list of known limitations.

## Decisions needed from you before implementation

Please provide these values before Phase 2:

1. **Target deployment:** Streamlit Community Cloud, Docker/Railway/Render,
   or both. If both, which one is the primary judging URL?
2. **LLM preference:** deterministic parser only (most reliable), or an
   OpenAI-compatible provider. If using a provider, configure its secret in
   the host; do not commit it to the repository.
3. **Data mode:** keep the built-in mock catalog for the hackathon demo, or
   connect an external store API/database. The mock catalog is recommended for
   reproducibility.
4. **Demo defaults:** confirm the intended default scenario, budget, deadline,
   and whether fault injection should be enabled only after a judge clicks the
   demo toggle.
5. **Branding/content:** provide any team name, logo, screenshots, or judging
   rubric requirements that should appear in the UI.
6. **Deployment secrets:** if using an external API, database, Neo4j, or LLM,
   add the values to the host's secret manager only. Never paste secret values
   into source files or `implementation.md`.

## Expected deliverables

- A working local demo with the request flow fixed.
- A deployment-specific start/configuration path.
- Clear user-visible error and clarification handling.
- Regression tests for the corrected hosting/request path.
- Updated README and environment example.
- A reproducible verification report in the final response, including the
  exact commands run and any host-side steps still required.

## Out of scope

- Scraping or automating purchases on Blinkit, Zepto, or Swiggy Instamart.
- Storing or committing credentials.
- Adding payment execution or order placement.
- Replacing the deterministic mock catalog with unverified live prices.
