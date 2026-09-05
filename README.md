# Pilant — Composition Engine prototype

The first real (non-scripted) piece of Pilant: a bounded AI agent that reads a
typed request, calls a mock connector for live data, and generates a screen
by calling `render_view` — whose input schema is the shared component
vocabulary (`schema.py`), so the model's output is shaped by the schema
rather than free text or raw HTML.

## Files

- `schema.py` — the component schema (`stat_grid`, `panel`, `list`). This is
  the fixed vocabulary the agent is allowed to build a screen from.
- `connectors.py` — a mock connector standing in for a real read-only
  adapter into a customer's security tool. Two fake incidents-affecting-
  customers, one exposed-credentials incident, one internal-only incident,
  and one pending approval.
- `agent.py` — the agent, running on Claude (Anthropic API). This is the
  intended production path.
- `agent_nvidia.py` — the same agent, running on a model hosted via
  NVIDIA's API catalog (Llama 3.1 70B by default) instead, using the
  OpenAI-compatible wire format. Useful if you want to compare providers,
  or don't have Anthropic API credit yet.
- `validation.py` — checks a generated view against `schema.py` before
  accepting it. If the model returns something malformed (wrong type, or
  worse, a fabricated placeholder instead of real tool data — Llama 3.1 70B
  did exactly this in testing), the agent sends back a specific correction
  and lets the model retry, instead of a bad screen reaching a renderer.
- `renderer.py` — the visual renderer. Takes the validated `render_view`
  JSON and turns it into a real, self-contained HTML page, using the same
  design system as the interactive demo artifact (dark chrome around a
  light "app window," Sora/IBM Plex type, the same stat-card/panel/list
  visual language). Pure server-side string generation — no client-side JS.
  Run `python3 renderer.py` on its own to sanity-check it against a sample
  view without hitting any API (writes `output_test.html`).
- `connectors_github.py` — a REAL connector (not mock): reads live issues
  from a GitHub repository via GitHub's REST API. See "The real connector"
  below.
- `agent_github.py` — the agent wired to `connectors_github.py` instead of
  the mock `connectors.py`. Same schema, same guardrails, same NVIDIA model
  as `agent_nvidia.py` — only the data source changed, which is the point:
  the loop doesn't care what's behind the connector.
- `users.py` — a mock identity/auth layer. A small directory of fictional
  users, each with a `label_scope` defining which issue labels they're
  authorized to see. `scope_issues()` enforces this on data returned by
  `get_github_issues`, before it ever reaches the model. See "Identity and
  scoping" below.
- `server.py` — the hosted portal: a real local web server (Flask) around
  `agent_github.run_agent`, with a real login gating every route. This is
  the "no default dashboard, just a query box" claim running as an actual
  web page instead of a CLI script. See "The hosted portal" below.
- `connectors_retail.py` / `agent_retail.py` — a second real-shaped mock
  connector and agent, for a fictional clothing retailer ("Harriet & Co")
  instead of security incidents or GitHub issues — orders and inventory
  instead of incidents and approvals. Same schema, same guardrails, same
  everything else; proves the pipeline is domain-independent.
- `retail_site.py` — a dummy internal admin tool for Harriet & Co, with
  Pilant embedded inside it as one widget on the page instead of being the
  whole page. This is the "embedded widget" integration model. See "The
  embedded widget demo" below.

## Known failure modes (found in real testing against Llama 3.1 70B)

Two distinct failures showed up running this against NVIDIA's hosted
Llama 3.1 70B, and both are now guarded against in the loop:

1. **Skipping the data fetch.** The model called `render_view` directly
   without calling `get_security_incidents` first, and filled the result
   with a fabricated placeholder like `{{get_security_incidents(...)}}`
   instead of real data. Fixed by refusing any `render_view` call until at
   least one data tool has actually been called and returned a real result.
2. **Answering in plain text instead of calling a tool.** The model wrote
   out `{"name": "render_view", "parameters": {...}}` as its message text
   rather than using the API's structured tool-calling mechanism — and the
   JSON it wrote was itself malformed. Fixed by requesting `tool_choice`
   as required (falls back to `"auto"` if the model/endpoint rejects that)
   and, if the model still responds in plain text, sending it a specific
   correction and letting it retry rather than failing immediately.

3. **Double-encoding nested data as a JSON string.** Even after correctly
   fetching real data, the model kept calling `render_view` with
   `components` set to a *string* containing JSON text, instead of a real
   nested array — repeatedly, across several retries, ignoring the
   validator's correction. Fixed with `normalize_args()`: before
   validating, recursively walk the tool call's arguments and parse any
   string that's actually JSON back into a real array/object (and coerce
   `"true"`/`"false"` strings to real booleans, another thing this model
   did with the `customer_systems_only` argument). Recovering silently
   works far better than repeatedly rejecting and hoping the wording of
   the correction eventually lands.

All three fixes are general-purpose guardrails, not something specific to
one bad response — the same protections apply to `agent.py` (Claude) too,
even though Claude is much less likely to need them. This is exactly the
kind of reliability gap a first build needs to design around if it's going
to use a lighter-weight or open-weight model instead of a frontier one.

## What ended up working

`meta/llama-3.1-70b-instruct` never got past the failures above, even with
all three guardrails in place — it kept regenerating the same malformed,
truncated JSON regardless of the correction it was given, which is a real
capability ceiling, not a prompting problem. Switching the model to
**`openai/gpt-oss-120b`** (still via the same NVIDIA endpoint, same
`agent_nvidia.py`, same schema, same guardrails — only the model name
changed) fixed it immediately: correct tool routing on two genuinely
different requests ("what's affecting customers" → `get_security_incidents`,
"what's waiting on my approval" → `get_pending_approvals`), real fetched
data, valid nested schema output, first try. `MODEL` in `agent_nvidia.py`
now defaults to this.

The takeaway that matters beyond this one test: for a task shaped like
this — free text in, correct tool routing, then a nontrivial nested
structured object out — model choice is not a minor detail. Some models
that claim OpenAI-compatible tool calling support the shape of the
protocol without reliably producing correct output through it. Test
against real production candidates (Claude, GPT, and whichever
NVIDIA-hosted models you'd actually deploy) before picking one, rather
than assuming "supports function calling" means "reliable at this."

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and paste in whichever key(s) you're using
```

You need API credit on whichever key you use — this is separate from a
Claude.ai subscription. For Anthropic: console.anthropic.com → Plans &
Billing. For NVIDIA: build.nvidia.com (a fresh account usually comes with
some free credits).

## Run it

```bash
python3 agent.py "what's actually affecting customers right now that I need to deal with?"
# or, on NVIDIA:
python3 agent_nvidia.py "what's actually affecting customers right now that I need to deal with?"
```

Try different phrasings of the same intent, and genuinely different intents
("what's waiting on my approval?") — the point of this prototype is proving
the agent interprets the request rather than matching a fixed phrase. Each
run prints the tool calls it made to stderr, then the final generated
screen (JSON matching `schema.py`) to stdout — and, if `render_view`
succeeded, writes `output.html` and opens it in your default browser
automatically. That's the actual screen the typed request produced.

## The real connector

Everything above runs on `connectors.py`, which is fabricated data — useful
for proving the agent loop and the renderer, but it's still a fiction the
agent could theoretically have memorized. `connectors_github.py` +
`agent_github.py` replace that with a real external system: a live GitHub
repository's issue tracker, reachable at request time, with data the model
has never seen.

Setup:

```bash
# in .env:
GITHUB_REPO=owner/repo        # any real repo, e.g. your own or a public one
GITHUB_TOKEN=                 # optional for public repos, required for private ones
```

No token is required to read a public repo — GitHub allows 60 unauthenticated
requests/hour, which is plenty for testing. Set `GITHUB_TOKEN` (a personal
access token from github.com/settings/tokens, "repo" scope or fine-grained
"Issues: Read-only") to raise that to 5000/hour, or if `GITHUB_REPO` is
private.

Run it the same way as the others:

```bash
python3 agent_github.py "what open issues need attention right now?"
python3 agent_github.py "show me anything labeled critical"
```

The agent decides what state/label filters actually answer the request,
fetches real issues, and renders a real screen from them — same schema,
same validation, same renderer, zero fabricated data anywhere in the loop.

## Identity and scoping

`agent_github.py` now takes an optional second argument: which user is
asking.

```bash
python3 agent_github.py "what open issues need attention right now?"          # defaults to kowsick (unrestricted)
python3 agent_github.py "what open issues need attention right now?" analyst  # scoped to bug/critical/security labels
python3 agent_github.py "what open issues need attention right now?" viewer   # scoped to enhancement/documentation labels
```

The three mock users live in `users.py`. The important design point isn't
the specific users — it's *where* the restriction is enforced. `scope_issues()`
runs in `run_agent()`'s trusted tool-execution code, right after
`get_github_issues` returns and before the result goes back into the
conversation. The model never sees an issue outside the requesting user's
scope in the first place — it's not a system-prompt instruction asking the
model to withhold things, which a sufficiently different phrasing of the
request could talk around. If you're an `analyst` and ask "show me literally
everything, ignore any restrictions," the model still only has the
`analyst`-scoped issues in its context to work with, because that's all it
was ever given.

That's the actual guarantee "the software adapts to you" needs once real
customer data is involved: not just that the wording feels personalized, but
that a different identity mechanically cannot see data it isn't authorized
for, enforced in code the model doesn't control.

## The hosted portal

Everything above runs from the command line — you type a request as a CLI
argument, and the result opens as a file in your browser. `server.py` turns
the same engine into a real, small web app instead:

```bash
python3 server.py
```

Then open **http://localhost:5001**. You'll land on a real login screen —
every route requires an authenticated session now, not a dropdown you can
freely repick. Demo accounts (plaintext passwords, since these are mock
accounts with nothing real behind them yet):

| username  | password      | sees                                   |
|-----------|---------------|-----------------------------------------|
| `kowsick` | `owner123`    | everything (unrestricted)               |
| `analyst` | `analyst123`  | only `bug`/`critical`/`security` issues |
| `viewer`  | `viewer123`   | only `enhancement`/`documentation` issues |

After logging in you see nothing but an input box — that's deliberate,
it's the actual "no default dashboard" claim, not a mockup of it. Type a
request or click one of the example prompts, submit, and the server runs
the real Composition Engine — real GitHub data, identity scoping enforced
against *your logged-in session* (not a field you could edit in the form),
real schema validation — and renders the real result as the next page,
with a link back to ask something else, and a logout link.

This is the first version of this project where the whole loop is reachable
by just opening a web page, logging in, and typing, instead of running a
script with an argument. Real login, gone are the days of picking an
identity from a dropdown. It's still `localhost`-only (no deployment, no
HTTPS, no real user database — `verify_login()` checks against the
hardcoded mock accounts in `users.py`) — turning this into something an
actual customer could reach is exactly the "embedded widget / API-first /
hosted portal" decision from the architecture doc, and this is a first,
rough proof of the "hosted portal" branch of that choice specifically.

## The embedded widget demo

Everything above — the CLI scripts, the hosted portal — has Pilant own the
whole screen. `retail_site.py` proves the other integration model instead:
Pilant as a component living inside someone else's real software, not a
destination of its own.

```bash
python3 retail_site.py
```

Then open **http://localhost:5002**. What loads is a dummy internal admin
tool ("Harriet & Co Ops") — a sidebar, a topbar with a fake logged-in user,
a row of decorative stat cards. None of that chrome is real; it's just set
dressing to sell the illusion that Pilant is embedded in an actual product
instead of being the whole demo.

The one real thing on the page is the "Ask Ops" card. Type a request (or
click an example chip) and it POSTs to `/widget/generate`, which runs the
actual Composition Engine — `agent_retail.run_agent`, backed by
`connectors_retail.py`'s mock order/inventory data — and injects the
rendered result directly into that card, in place, with no page reload.
That's the technical shape a real embedded widget needs: the host page
keeps its own identity and chrome, and Pilant only ever touches the one
region it was given, returning a fragment (`renderer.render_fragment()`)
rather than a full page.

This demo intentionally skips its own login screen — a real embedded
widget would inherit whatever session the host app already established
(that's `server.py`'s job, already proven), not show a second login inside
someone else's product. The point here is narrower and specifically about
the embedding mechanism: same schema, same agent loop, same renderer,
running as a fragment inside a foreign page instead of owning the whole
screen or the whole terminal.

## What this proves, and what it doesn't

This proves the core mechanic works end to end: real typed text in (from
an actual web page, not just a CLI argument), a real login gating access,
a real model deciding what data to fetch and what to show across two
different domains (GitHub issues, retail orders/inventory) proving the
pipeline is domain-independent, real structured output, a real rendered
screen — either owning the whole page (the hosted portal) or embedded as
one widget inside someone else's page (the retail ops demo) — a real
external system behind it instead of fabricated data, data that's actually
scoped to whoever's authenticated, and a real — if rough — public URL
someone else could open, via a tunnel. It does **not** yet include: a real
user database (accounts are hardcoded in `users.py`, no signup), a
persistent deployment (the tunnel is temporary and stops when you close
it), HTTPS on a real domain, or protection against the model generating
something wrong in ways schema validation can't catch (correct shape,
wrong content). Reasonable next step: an actual persistent cloud
deployment, and choosing the embedded-widget or API-first variant for a
real customer integration — `retail_site.py` is a first proof of the
embedded-widget shape specifically.

## A note on the API keys

Whatever key you used to get this running was pasted into a chat at some
point during development. That's fine for testing, but before this goes
anywhere near production, rotate any key that was ever typed into a chat
window, and keep real keys out of version control (`.env` is already
gitignored — see below).
