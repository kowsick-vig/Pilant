# Briefing: build a "dummy Salesforce agent" for Pilant Studio (Python learning exercise)

Paste this whole thing as your first message in a new Claude conversation. It gives that session everything it needs to help you build a Salesforce connector agent step-by-step, as a way to learn Python — without touching the other Claude session that's still working on the rest of Pilant Studio.

---

## What this project is

Pilant Studio is a multi-connector business-app platform. The core idea: one Composition Engine (built on Claude) that can be wired up to different real business apps (Gmail, Slack, GitHub, a helpdesk, a CRM) through a consistent two-file pattern per connector. A shared shell app (`studio.py`) lets a user pick a connector and chat with it; the model decides what data to fetch and renders a UI screen back (cards, lists, stats) built from real fetched data — never fabricated content.

**Location:** the project lives at `/home/claude/pilant-agent` in the other Claude session's cloud workspace, and is synced to `/Users/cmx/Downloads/pilant-agent` on your Mac. If this new session doesn't have direct access to that project folder, ask it to work from a fresh local folder instead — the value here is learning the *pattern*, not necessarily editing the live project files from two sessions at once (editing the same files from two places at the same time risks clobbering each other's changes).

## The established per-connector pattern

Every real connector in this project follows two files:

1. **`connectors_X.py`** — the data layer. Plain Python functions that fetch/return data for that app. Two flavors exist in this project:
   - **Real**: e.g. `connectors_gmail.py` — actual OAuth2 API calls against a live external system.
   - **Honest mock**: e.g. `connectors_salesforce_mock.py` (already exists, see below) — no real API call, but clearly documented as a mock, and returns the *same field shapes* a real connector would, so it could be swapped for a real one later without changing any caller.

2. **`agent_X.py`** — the Composition Engine layer. This is the Claude-specific wiring:
   - `TOOLS` — a list of tool definitions (JSON schema) Claude can call: usually a `get_X_data`-style fetch tool, an `ask_user` clarifying-question tool, and a `render_view` tool that emits the final UI screen.
   - `SYSTEM` — a system prompt describing what the connector does, what data shapes mean, and (importantly) fabrication rules — e.g. "never invent a value, only use what the fetch tool actually returned."
   - `_make_dispatch(...)` — a closure that handles each tool call: runs the real fetch, validates `render_view`'s output against guardrails, and rejects anything that looks fabricated.
   - `run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False, ...)` — the entry point every connector exposes with this exact same calling convention, so the shell app (`studio.py`) can call any connector interchangeably.

The shared engine underneath all of this is `claude_engine.py` (`run_claude_agent()`, `ToolOutcome`, etc.) — every `agent_X.py` calls into it the same way.

## What already exists for Salesforce

Two files already sit in the project, unused/unwired so far:

- **`connectors_salesforce_mock.py`** (~120 lines, already written) — an honest mock CRM data layer: a hardcoded list of `OPPORTUNITIES` (id, account, customer, amount, stage, owner, days inactive, risk level/reason), plus `get_opportunities(...)`, `get_account_risk(...)`, `get_pipeline_metrics(...)`, `get_pipeline_by_stage(...)`. This is a good starting point for a "dummy" Salesforce agent — it's already shaped like real Salesforce Opportunity data, and `owner` values map to real usernames in `users.py` (`kowsick`, `analyst`), so role-based scoping works if you want it.
- **`connectors_salesforce.py`** (~500 lines) — a REAL connector stub with full Salesforce OAuth2 setup instructions in its docstring, for if this ever gets wired to an actual Salesforce org. Not needed for the learning exercise — just know it's there as the "real version" this mock could eventually be swapped for.

**There is no `agent_salesforce.py` yet.** That's the actual exercise: write one, using `agent_gmail.py` (or the smaller `agent_helpdesk.py`/`agent_custom.py`) as a template, wired to `connectors_salesforce_mock.py`.

## Suggested step-by-step build order (for the new session to propose/adjust)

1. Read `connectors_salesforce_mock.py` in full to see the exact data shape available.
2. Read a smaller existing agent file first for the pattern — `agent_helpdesk.py` is simpler than `agent_gmail.py` and a gentler place to start.
3. Write `agent_salesforce.py` from scratch, one piece at a time: first just `TOOLS` + a minimal `SYSTEM`, then `_make_dispatch`, then `run_agent`. Test it standalone (each agent file has a `if __name__ == "__main__":` block at the bottom for exactly this) before wiring it into `studio.py`.
4. Only after it runs standalone: add an entry to `studio.py`'s `CONNECTORS` registry to make it selectable from the Studio shell.

## Why this is a separate session

This is intentionally being done in a brand-new Claude conversation so it doesn't collide with the other session actively working on Pilant Studio (CRM Copilot, Integrations redesign, the layout-learning feature). That other session keeps running independently. Once the new `agent_salesforce.py` is built and tested there, you can bring the finished file back (or ask that session to hand it over) and have the original session review/merge it into the live project.

## One honesty note to carry over

This project has a running principle: never fabricate data or pretend a mock is real. `connectors_salesforce_mock.py`'s docstring already says this explicitly — keep that same honesty in whatever `SYSTEM` prompt you write for `agent_salesforce.py` (e.g., "these are mock CRM opportunities for a prototype, not a live Salesforce org").
