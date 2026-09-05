"""
primitives.py — added 2026-08-26 at the user's explicit request to build
toward "the full DynamisOS product": dynamisos.in's own pitch is "typed,
composable building blocks with real product semantics" (their words —
data sources, actions, validations, workflows) exposed through a protocol
coding agents can read, so an interface can be COMPOSED from them per
person instead of shipped once as a fixed screen.

Pilant Studio already had the hard, proven half of that idea before this
file existed: render_view + guardrails.py mean the model genuinely composes
a screen's layout per request, bound to real fetched data, never fabricated
(see guardrails.py's module docstring). What it didn't have was a TYPED
REGISTRY — a single place where "what can be fetched" is declared once,
with a real schema, independent of any one connector's hand-written TOOLS
list. Every existing connector (agent_gmail.py, agent_slack.py,
agent_github.py) still hard-codes its own tools inline; this file doesn't
touch them. It's a NEW, additive layer that wraps the same real underlying
functions (get_gmail_messages, get_slack_messages, get_github_issues,
rag_index.search) as typed Primitive objects, so a NEW cross-connector
composer (agent_composer.py) can pull from several of them in one request —
the actual "same primitives, personalized interface" mechanic DynamisOS
describes, built on data this app already has real access to.

A Primitive is a plain dict, not a class — this is a prototype, and a dict
serializes trivially to the Anthropic tool-call schema shape (see
Registry.as_tool_schema below) with no extra translation layer:

    {
        "id": "gmail_messages",           # unique, connector_name — NOT dotted: Anthropic's tool-calling
                                           # API requires a tool's "name" to match ^[a-zA-Z0-9_-]+$, and
                                           # as_tool_schema() below uses this id AS that name verbatim, so
                                           # a "." here would make every request using it fail with a real
                                           # BadRequestError (found live 2026-08-26 testing the Composer)
        "type": "data_source",            # data_source | action (only data_source exists yet — no
                                           # connector here supports a real write action other than
                                           # Gmail's, which lives in gmail_site.py/connectors_gmail.py
                                           # and is deliberately NOT exposed as a composable primitive
                                           # yet — see this module's "NOT YET COVERED" note below)
        "connector": "gmail",             # which CONNECTORS[...] entry (studio.py) this belongs to
        "label": "Gmail messages",        # short human-readable name, for the canvas palette
        "description": "...",             # exactly what an agent tool description needs to be —
                                           # copied from the connector's own hand-written TOOLS entry
                                           # where one already exists, not reworded, so this registry
                                           # never silently drifts from what real usage already proved
                                           # out
        "input_schema": {...},            # JSON schema, the exact shape Anthropic's tool-calling API
                                           # expects for a tool's "input_schema" field
        "handler": callable,              # the REAL function this primitive calls — get_gmail_messages,
                                           # etc. — never a mock, never synthesized data
        "scope": callable | None,         # optional (result, user) -> result post-fetch identity
                                           # filter, applied AFTER handler() returns and BEFORE the
                                           # composer ever sees it — same "never in its context to
                                           # begin with" guarantee users.py's module docstring
                                           # describes, just applied generically instead of only
                                           # inside agent_github.py's own dispatch
    }

NOT YET COVERED by this registry (honest scope boundary, not an oversight):
Gmail's real WRITE actions (compose/reply/forward/star/delete, in
gmail_site.py) aren't primitives here — they're real mutations with their
own confirmation/redirect flow bound tightly to the interactive inbox UI,
and turning a destructive action into something an agent can call
unsupervised needs its own safety design (a real "action" primitive type,
confirmation semantics, etc.) that a prototype registry shouldn't paper
over by just wiring it in. Healthcare/Helpdesk connectors aren't
registered yet — direct, mechanical follow-up once needed, not a design
question.

retail_orders, added 2026-08-27, is the first of "the rest": this is what
proves the composer mechanic generalizes past the original four demo
connectors it was built and tested against, for the customer-360 feature
(one screen per customer — orders, refund status, delivery status,
recommended actions — instead of a support agent flipping between
separate systems). No new connector file was needed; registering the
existing real connectors_retail.get_orders() here is the entire integration
— studio.py's new /customers routes call agent_composer.run_agent() with
primitive_ids=["retail_orders"] pre-selected, same mechanism
composer_open() already uses for a saved canvas selection.
"""

import connectors_gmail
import connectors_slack
import connectors_github
import connectors_retail
import rag_index
from users import scope_issues
from rag_scope import scope_knowledge_base_results


class Registry:
    """A typed, in-memory primitive registry — the "typed protocol readable
    by coding agents" DynamisOS's site describes, minus the "readable by
    OTHER companies' agents over a real network API" part (see this file's
    module docstring: this is the in-process prototype of the idea, not
    the externally-integrable version). Deliberately not a singleton
    pattern beyond the module-level REGISTRY instance at the bottom of this
    file — a test can build its own Registry() and register fakes into it
    without touching the real one."""

    # Anthropic's tool-calling API requires a tool's "name" to match this
    # pattern — as_tool_schema() uses a primitive's id AS that name
    # verbatim (see this file's module docstring), so an id outside this
    # shape doesn't fail here, it fails LATER as an opaque BadRequestError
    # from the model API, with no indication which primitive caused it.
    # Found live 2026-08-26: the first version of this file used dotted
    # ids ("gmail.messages") and every Composer request failed instantly.
    # Enforcing the real constraint at registration time turns that into
    # an immediate, actionable error instead.
    _VALID_ID_RE = __import__("re").compile(r"^[a-zA-Z0-9_-]{1,128}$")

    def __init__(self):
        self._items = {}

    def register(self, id, *, type, connector, label, description, input_schema, handler, scope=None):
        if not self._VALID_ID_RE.match(id):
            raise ValueError(
                f"primitive id {id!r} is invalid — must match ^[a-zA-Z0-9_-]{{1,128}}$ (Anthropic's "
                "tool-name constraint; this id is used as a tool's real 'name' verbatim). No dots, "
                "spaces, or other punctuation — use an underscore instead."
            )
        if id in self._items:
            raise ValueError(f"primitive '{id}' is already registered — ids must be unique")
        self._items[id] = {
            "id": id,
            "type": type,
            "connector": connector,
            "label": label,
            "description": description,
            "input_schema": input_schema,
            "handler": handler,
            "scope": scope,
        }

    def get(self, id):
        """The primitive dict for `id`, or None — never raises, since
        callers (agent_composer.py's dispatch, studio.py's routes) need to
        tell "not a primitive" apart from "a primitive that errored" by a
        plain None check, not a try/except."""
        return self._items.get(id)

    def all(self):
        """Every registered primitive, in registration order — stable
        because Python dicts preserve insertion order, not because this
        sorts anything."""
        return list(self._items.values())

    def by_connector(self, connector):
        return [p for p in self._items.values() if p["connector"] == connector]

    def as_tool_schema(self, id):
        """The exact {"name", "description", "input_schema"} shape
        Anthropic's Messages API expects inside a `tools` list — this is
        what makes the registry a real typed protocol an agent can read,
        not just an internal bookkeeping structure. Raises KeyError for an
        unknown id (a programming error, not a user-facing case — every
        caller here builds `tools` from REGISTRY.all() directly)."""
        p = self._items[id]
        return {"name": p["id"], "description": p["description"], "input_schema": p["input_schema"]}

    def call(self, id, args, user=None):
        """Actually invoke a primitive's real handler, then apply its scope
        function if it has one and a user was given. Raises whatever the
        underlying connector function raises (typically RuntimeError for
        "not configured") — callers decide how to surface that, same
        convention every existing connector's dispatch() already follows
        for its own hand-called functions."""
        p = self._items[id]
        result = p["handler"](**(args or {}))
        if p["scope"] is not None and user is not None:
            result = p["scope"](result, user)
        return result


REGISTRY = Registry()

# --- gmail_messages ---------------------------------------------------------
# Description/input_schema copied verbatim from agent_gmail.py's TOOLS entry
# for get_gmail_messages — same real tool, same real contract, just reachable
# from the registry instead of only from that one connector's hard-coded
# TOOLS list.
REGISTRY.register(
    id="gmail_messages",
    type="data_source",
    connector="gmail",
    label="Gmail messages",
    description=(
        "Fetch recent real messages from the connected Gmail inbox, most recent first. "
        "`query` is REAL Gmail search syntax — the same operators you'd type in the "
        "Gmail search bar, e.g. 'from:someone@example.com', 'subject:invoice', "
        "'newer_than:3d', 'has:attachment'. `folder` scopes to one of Gmail's real "
        "built-in views — 'inbox', 'sent', 'spam', 'drafts', 'trash', 'starred', "
        "'important' — and combines with `query`/`unread_only` rather than replacing "
        "them; for any OTHER (custom) label the account has, use "
        "query=\"label:<name>\" instead. `unread_only`, if true, restricts to "
        "genuinely unread messages. `limit` caps how many messages come back (default "
        "10, max 50)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Real Gmail search syntax, e.g. 'from:boss@company.com' or 'subject:invoice'.",
            },
            "folder": {
                "type": "string",
                "enum": ["inbox", "sent", "spam", "drafts", "trash", "starred", "important"],
                "description": "One of Gmail's real built-in folders/views.",
            },
            "unread_only": {"type": "boolean"},
            "limit": {"type": "integer", "description": "How many messages to fetch."},
        },
    },
    handler=connectors_gmail.get_gmail_messages,
)

# --- slack_messages ----------------------------------------------------------
REGISTRY.register(
    id="slack_messages",
    type="data_source",
    connector="slack",
    label="Slack messages",
    description=(
        "Fetch recent real messages from the configured Slack channel, most recent "
        "first. Optionally filter to messages containing a specific word or phrase "
        "(a simple substring match, not full Slack search). `limit` caps how many "
        "recent messages to pull before filtering (default 20, max 100)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "contains": {
                "type": "string",
                "description": "Only include messages containing this word/phrase (case-insensitive).",
            },
            "limit": {"type": "integer", "description": "How many recent messages to fetch before filtering."},
        },
    },
    handler=connectors_slack.get_slack_messages,
)

# --- github_issues -------------------------------------------------------------
# `scope` re-applies users.scope_issues() after the real fetch, exactly what
# agent_github.py's dispatch already does by hand — see that module's
# docstring for why this exists at all. Registering it here means any FUTURE
# caller of this primitive (not just agent_github.py) gets the same identity
# scoping for free, by construction, not by remembering to add it.
REGISTRY.register(
    id="github_issues",
    type="data_source",
    connector="github",
    label="GitHub issues",
    description=(
        "Fetch real issues from the configured GitHub repository (set via GITHUB_REPO "
        "in .env), optionally filtered by state and by a single label."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "state": {"type": "string", "enum": ["open", "closed", "all"]},
            "label": {
                "type": "string",
                "description": "Filter to issues with this exact label name, e.g. 'bug' or 'critical'.",
            },
        },
    },
    handler=connectors_github.get_github_issues,
    scope=scope_issues,
)

# --- knowledge_base_search -------------------------------------------------
# The one primitive that's ALREADY inherently cross-source (Gmail/Slack/
# GitHub, whatever's been synced) — connector=None reflects that honestly
# rather than picking one connector to attribute it to. `scope` reuses
# rag_scope.py's shared helper, same as every connector's own
# search_knowledge_base dispatch already does.
REGISTRY.register(
    id="knowledge_base_search",
    type="data_source",
    connector=None,
    label="Knowledge base search",
    description=(
        "Semantic search over the local knowledge base synced from Gmail/Slack/GitHub. "
        "Use this for older history, a broad or fuzzy topic, or something that spans "
        "multiple sources at once — NOT real Gmail/Slack/GitHub search syntax, a "
        "plain-language description of what to find. `source`, if given, restricts "
        "results to one connector ('gmail'/'slack'/'github') — omit it to search "
        "everything synced. Returns an empty list if nothing has been synced yet."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to find, in plain language."},
            "source": {"type": "string", "enum": ["gmail", "slack", "github"]},
        },
        "required": ["query"],
    },
    handler=lambda query, source=None: rag_index.search(query, source=source),
    scope=scope_knowledge_base_results,
)

# --- retail_orders -----------------------------------------------------------
# See this file's module docstring for why this is the first connector
# registered here past the original four (Gmail/Slack/GitHub/knowledge base).
# No `scope` function: unlike github_issues (an internal analyst viewing
# issues they're allowed to see), this primitive is deliberately usable
# either unscoped (retail ops asking "what's unfulfilled right now") or
# scoped to one customer via the `customer` arg itself — the scoping here
# happens INSIDE connectors_retail.get_orders() (a real customer= filter,
# added 2026-08-27), not as a separate post-fetch step, because "which
# customer" is part of the request's own content, not the calling staff
# member's identity the way users.scope_issues()'s scoping is.
REGISTRY.register(
    id="retail_orders",
    type="data_source",
    connector="retail",
    label="Store orders",
    description=(
        "Fetch real orders from the connected Shopify store: fulfillment/delivery status "
        "(unfulfilled/partial/fulfilled/restocked/cancelled), item count, total, whether a "
        "refund has been issued, and when it was placed. `customer`, if given, restricts "
        "results to ONE customer's own orders — pass their exact email or Shopify customer "
        "id, whichever is already known; never guess or invent one. `status` optionally "
        "filters by fulfillment status instead of/in addition to a customer filter."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "customer": {
                "type": "string",
                "description": "Restrict to one customer's orders — their exact known email or Shopify customer id.",
            },
            "status": {
                "type": "string",
                "enum": ["unfulfilled", "partial", "fulfilled", "restocked", "cancelled"],
            },
        },
    },
    handler=connectors_retail.get_orders,
)
