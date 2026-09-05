"""
Tests for the 2026-08-26 "ask like DynamisOS in our main workflow" feature:
the user compared Pilant Studio's connector chats (which only ever ask
ask_user REACTIVELY, when a specific request is ambiguous) against
DynamisOS's real product, which proactively asks a round of clarifying
questions (purpose/data/style/tools) before building anything, every time.

Scope decided live via AskUserQuestion: apply to the Composer + all real
connector chats (Gmail/Slack/GitHub/Helpdesk) — NOT agent_custom.py, which
already got its own DynamisOS-style 4-item checklist on 2026-08-25 for the
free-form "describe an app" case (see agent_custom.py's own docstring).
Style: ONE bundled, options-based question (purpose + style, and — for the
Composer only, when nothing was pre-selected via the canvas — which data
source), not DynamisOS's longer numbered checklist.

This validates, per connector (agent_gmail/agent_slack/agent_github/
agent_helpdesk) and agent_composer:
  - _build_system(first_turn=True) appends the bundled-question paragraph;
    first_turn=False (or omitted) leaves SYSTEM byte-for-byte unchanged —
    every existing caller that doesn't know about this kwarg is unaffected.
  - _make_dispatch's first_turn gate is a real code-enforced backstop, not
    just a prompt hope: with first_turn=True, every fetch tool AND
    render_view is rejected with FIRST_TURN_GATE_NUDGE regardless of state
    — but ask_user is NEVER gated, since that's the escape valve the model
    is being pushed toward.
  - with first_turn=False (the default), dispatch behaves exactly as
    before this feature existed (a real fetch/render still goes through).
  - studio.py's _run_agent_for_workflow passes first_turn=True to a
    connector's run_agent() ONLY on the genuinely-first branch (no
    in-progress clarify, no prior memory) — never on a resume, never on an
    in-conversation follow-up, and never at all for agent_custom (which
    doesn't accept the kwarg and would raise TypeError if it were passed).
  - studio.py's /composer route asks the bundled question once per LOGIN
    SESSION (composer has no per-conversation memory of its own — see its
    own docstring), tracked via session["composer_first_turn_done"], not
    once per HTTP request.

All external calls (the model) are mocked or avoided entirely — this
validates the wiring/logic, not real API behavior (see other *_migration.py
files in this same directory for live-API smoke tests of this same
feature).
"""
import sys
import unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")
import os
os.environ.setdefault("GMAIL_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
os.environ.setdefault("GMAIL_CLIENT_SECRET", "test-client-secret")

import agent_gmail
import agent_slack
import agent_github
import agent_helpdesk
import agent_composer
from users import get_user, DEFAULT_USER

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}  {detail}")


class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, id_, name, input_):
        self.id = id_
        self.name = name
        self.input = input_


# ============================================================================
print("== per-connector _build_system(first_turn) ==")
# ============================================================================

for mod, fetch_tool in [
    (agent_gmail, "get_gmail_messages"),
    (agent_slack, "get_slack_messages"),
    (agent_github, "get_github_issues"),
    (agent_helpdesk, "get_tickets"),
]:
    name = mod.__name__
    check(f"{name}: _build_system(first_turn=False) == SYSTEM verbatim",
          mod._build_system(first_turn=False) == mod.SYSTEM)
    check(f"{name}: _build_system() (default) == SYSTEM verbatim",
          mod._build_system() == mod.SYSTEM)
    with_first = mod._build_system(first_turn=True)
    check(f"{name}: _build_system(first_turn=True) is SYSTEM + more, not equal",
          with_first != mod.SYSTEM and with_first.startswith(mod.SYSTEM))
    check(f"{name}: first_turn=True paragraph mentions a single bundled ask_user question",
          "ONE time with a single bundled question" in with_first)
    check(f"{name}: first_turn=True paragraph mentions purpose",
          "what they actually want to see" in with_first)
    check(f"{name}: first_turn=True paragraph mentions layout/style",
          "how they'd like it laid out" in with_first)


# ============================================================================
print("\n== per-connector dispatch: first_turn gate is code-enforced, not just prompted ==")
# ============================================================================

# --- Gmail ---
with mock.patch.object(agent_gmail, "get_gmail_messages", return_value=[{"subject": "x"}]):
    d = agent_gmail._make_dispatch(seed_fetched_data=False, verbose=False, first_turn=True)
    out = d("get_gmail_messages", {}, "tc1", [], False)
    check("gmail: first_turn=True blocks get_gmail_messages", out.final is None and out.tool_result == agent_gmail.FIRST_TURN_GATE_NUDGE)
    check("gmail: first_turn=True gate did NOT actually call the real fetch", agent_gmail.get_gmail_messages.call_count == 0)

    d2 = agent_gmail._make_dispatch(seed_fetched_data=False, verbose=False, first_turn=True)
    out2 = d2("search_knowledge_base", {"query": "x"}, "tc2", [], False)
    check("gmail: first_turn=True blocks search_knowledge_base", out2.tool_result == agent_gmail.FIRST_TURN_GATE_NUDGE)

    d3 = agent_gmail._make_dispatch(seed_fetched_data=True, verbose=False, first_turn=True)
    out3 = d3("render_view", {"heading": "h", "components": []}, "tc3", [], False)
    check("gmail: first_turn=True blocks render_view even with fetched_data already True",
          out3.tool_result == agent_gmail.FIRST_TURN_GATE_NUDGE)

    d4 = agent_gmail._make_dispatch(seed_fetched_data=False, verbose=False, first_turn=True)
    out4 = d4("ask_user", {"question": "What's this view mainly for — everything unread, mail from someone specific, or something else? And how would you like it laid out, a compact list or a stat summary?"}, "tc4", [], False)
    check("gmail: first_turn=True does NOT block ask_user (the escape valve)",
          out4.final is not None and out4.final.get("clarify") and out4.tool_result == agent_gmail.ce.ASK_USER_ACCEPTED_SENTINEL,
          out4.tool_result)

# regression: first_turn=False (default) behaves exactly as before
with mock.patch.object(agent_gmail, "get_gmail_messages", return_value=[{"subject": "x"}]) as m:
    d5 = agent_gmail._make_dispatch(seed_fetched_data=False, verbose=False)
    out5 = d5("get_gmail_messages", {"query": ""}, "tc5", [], False)
    check("gmail: first_turn=False (default) — fetch actually runs, unblocked", m.call_count == 1 and out5.tool_result is not None)

# --- Slack ---
with mock.patch.object(agent_slack, "get_slack_messages", return_value=[{"text": "x"}]):
    d = agent_slack._make_dispatch(seed_fetched_data=False, verbose=False, first_turn=True)
    out = d("get_slack_messages", {}, "tc1", [], False)
    check("slack: first_turn=True blocks get_slack_messages", out.tool_result == agent_slack.FIRST_TURN_GATE_NUDGE)
    check("slack: first_turn=True gate did NOT call the real fetch", agent_slack.get_slack_messages.call_count == 0)

    d2 = agent_slack._make_dispatch(seed_fetched_data=True, verbose=False, first_turn=True)
    out2 = d2("render_view", {"heading": "h", "components": []}, "tc2", [], False)
    check("slack: first_turn=True blocks render_view", out2.tool_result == agent_slack.FIRST_TURN_GATE_NUDGE)

with mock.patch.object(agent_slack, "get_slack_messages", return_value=[{"text": "x"}]) as m:
    d3 = agent_slack._make_dispatch(seed_fetched_data=False, verbose=False, first_turn=False)
    out3 = d3("get_slack_messages", {}, "tc3", [], False)
    check("slack: first_turn=False (default) — fetch actually runs, unblocked", m.call_count == 1)

# --- GitHub (needs `user`) ---
owner = get_user(DEFAULT_USER)
with mock.patch.object(agent_github, "get_github_issues", return_value=[{"title": "x", "labels": []}]):
    d = agent_github._make_dispatch(seed_fetched_data=False, verbose=False, user=owner, first_turn=True)
    out = d("get_github_issues", {}, "tc1", [], False)
    check("github: first_turn=True blocks get_github_issues", out.tool_result == agent_github.FIRST_TURN_GATE_NUDGE)
    check("github: first_turn=True gate did NOT call the real fetch", agent_github.get_github_issues.call_count == 0)

    d2 = agent_github._make_dispatch(seed_fetched_data=True, verbose=False, user=owner, first_turn=True)
    out2 = d2("render_view", {"heading": "h", "components": []}, "tc2", [], False)
    check("github: first_turn=True blocks render_view", out2.tool_result == agent_github.FIRST_TURN_GATE_NUDGE)

with mock.patch.object(agent_github, "get_github_issues", return_value=[{"title": "x", "labels": []}]) as m:
    d3 = agent_github._make_dispatch(seed_fetched_data=False, verbose=False, user=owner, first_turn=False)
    out3 = d3("get_github_issues", {}, "tc3", [], False)
    check("github: first_turn=False (default) — fetch actually runs, unblocked", m.call_count == 1)

# --- Helpdesk ---
with mock.patch.object(agent_helpdesk, "get_tickets", return_value=[{"subject": "x"}]):
    d = agent_helpdesk._make_dispatch(seed_fetched_data=False, verbose=False, first_turn=True)
    out = d("get_tickets", {}, "tc1", [], False)
    check("helpdesk: first_turn=True blocks get_tickets", out.tool_result == agent_helpdesk.FIRST_TURN_GATE_NUDGE)
    check("helpdesk: first_turn=True gate did NOT call the real fetch", agent_helpdesk.get_tickets.call_count == 0)

    d2 = agent_helpdesk._make_dispatch(seed_fetched_data=True, verbose=False, first_turn=True)
    out2 = d2("render_view", {"heading": "h", "components": []}, "tc2", [], False)
    check("helpdesk: first_turn=True blocks render_view", out2.tool_result == agent_helpdesk.FIRST_TURN_GATE_NUDGE)

with mock.patch.object(agent_helpdesk, "get_tickets", return_value=[{"subject": "x"}]) as m:
    d3 = agent_helpdesk._make_dispatch(seed_fetched_data=False, verbose=False, first_turn=False)
    out3 = d3("get_tickets", {}, "tc3", [], False)
    check("helpdesk: first_turn=False (default) — fetch actually runs, unblocked", m.call_count == 1)


# ============================================================================
print("\n== agent_composer.py: first_turn (bundled question covers data source too, if unselected) ==")
# ============================================================================

sys_no_primitives = agent_composer._build_system(None, style=None, first_turn=True)
check("composer: first_turn=True, no canvas selection -> mentions which data source(s)",
      "which data source(s)" in sys_no_primitives)

sys_with_primitives = agent_composer._build_system(["gmail_messages"], style=None, first_turn=True)
check("composer: first_turn=True, WITH canvas selection -> does NOT ask about data source again",
      "which data source(s)" not in sys_with_primitives)
check("composer: first_turn=True, WITH canvas selection -> still mentions purpose+style",
      "what they actually want to see" in sys_with_primitives and "how they'd like it laid out" in sys_with_primitives)

check("composer: first_turn=False (default) leaves SYSTEM unaffected by the paragraph",
      "bundled question" not in agent_composer._build_system(None, style=None, first_turn=False))

owner = get_user(DEFAULT_USER)
d = agent_composer._make_dispatch(seed_fetched_data=False, verbose=False, user=owner, primitive_ids=None, first_turn=True)
out = d("gmail_messages", {}, "tc1", [], False)
check("composer: first_turn=True blocks a primitive call", out.tool_result == agent_composer.FIRST_TURN_GATE_NUDGE)

d2 = agent_composer._make_dispatch(seed_fetched_data=True, verbose=False, user=owner, primitive_ids=None, first_turn=True)
out2 = d2("render_view", {"heading": "h", "components": []}, "tc2", [], False)
check("composer: first_turn=True blocks render_view", out2.tool_result == agent_composer.FIRST_TURN_GATE_NUDGE)

d3 = agent_composer._make_dispatch(seed_fetched_data=False, verbose=False, user=owner, primitive_ids=None, first_turn=True)
out3 = d3("ask_user", {"question": "what's this for, which source, and how should it look?"}, "tc3", [], False)
check("composer: first_turn=True does NOT block ask_user", out3.final is not None and "clarify" in out3.final)

d4 = agent_composer._make_dispatch(seed_fetched_data=False, verbose=False, user=owner, primitive_ids=None, first_turn=False)
out4 = d4("gmail_messages", {}, "tc4", [], False)
check("composer: first_turn=False (default) — primitive call actually runs (not the gate string)",
      out4.tool_result != agent_composer.FIRST_TURN_GATE_NUDGE)


# ============================================================================
print("\n== studio.py: _run_agent_for_workflow only sets first_turn=True on the genuinely-first branch ==")
# ============================================================================

import studio

studio.app.config["TESTING"] = True


def make_wf(connector, agent_messages=None, memory=None):
    wf = studio._create_workflow(connector)
    wf["agent_messages"] = agent_messages
    wf["memory"] = memory
    wf["fetched_data"] = False
    return wf


# _run_agent_for_workflow reads flask.session (for a log line) — needs an
# active request context, same as any Flask view function would have.
ctx = studio.app.test_request_context()
ctx.push()
from flask import session as flask_session
flask_session["username"] = "kowsick"

with mock.patch.dict(studio.CONNECTORS["gmail"], {"run_agent": mock.Mock(return_value={"text": "ok"})}):
    wf = make_wf("gmail")
    studio._run_agent_for_workflow(wf, "show my inbox")
    _, kwargs = studio.CONNECTORS["gmail"]["run_agent"].call_args
    check("gmail: brand-new workflow (no memory, no agent_messages) -> first_turn=True passed",
          kwargs.get("first_turn") is True, kwargs)

with mock.patch.dict(studio.CONNECTORS["gmail"], {"run_agent": mock.Mock(return_value={"text": "ok"})}):
    wf = make_wf("gmail", memory=[{"role": "user", "content": "earlier request"},
                                   {"role": "assistant", "content": 'Built "earlier request"'}])
    studio._run_agent_for_workflow(wf, "now a follow-up")
    _, kwargs = studio.CONNECTORS["gmail"]["run_agent"].call_args
    check("gmail: follow-up request WITH prior memory -> first_turn NOT set (defaults False)",
          "first_turn" not in kwargs, kwargs)

with mock.patch.dict(studio.CONNECTORS["gmail"], {"run_agent": mock.Mock(return_value={"text": "ok"})}):
    wf = make_wf("gmail", agent_messages=[{"role": "user", "content": "vague"}])
    studio._run_agent_for_workflow(wf, "answering the clarifying question")
    _, kwargs = studio.CONNECTORS["gmail"]["run_agent"].call_args
    check("gmail: resuming an in-progress ask_user round -> first_turn NOT set (defaults False)",
          "first_turn" not in kwargs, kwargs)

with mock.patch.dict(studio.CONNECTORS["custom"], {"run_agent": mock.Mock(return_value={"text": "ok"})}):
    wf = make_wf("custom")
    try:
        studio._run_agent_for_workflow(wf, "clothing brand")
        _, kwargs = studio.CONNECTORS["custom"]["run_agent"].call_args
        check("custom: brand-new workflow -> first_turn is NEVER passed (agent_custom doesn't accept it)",
              "first_turn" not in kwargs, kwargs)
    except TypeError as e:
        check("custom: brand-new workflow -> first_turn is NEVER passed (agent_custom doesn't accept it)", False, str(e))

for other in ("slack", "github", "helpdesk"):
    with mock.patch.dict(studio.CONNECTORS[other], {"run_agent": mock.Mock(return_value={"text": "ok"})}):
        wf = make_wf(other)
        studio._run_agent_for_workflow(wf, "some request")
        _, kwargs = studio.CONNECTORS[other]["run_agent"].call_args
        check(f"{other}: brand-new workflow -> first_turn=True passed", kwargs.get("first_turn") is True, kwargs)

ctx.pop()


# ============================================================================
print("\n== studio.py: /composer asks the bundled question once per LOGIN SESSION, not once per request ==")
# ============================================================================

client = studio.app.test_client()
r = client.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
assert r.status_code == 200, r.status_code

with mock.patch.object(agent_composer, "run_agent", return_value={"text": "ok"}) as m:
    client.post("/composer", data={"instruction": "show me something"})
    _, kwargs1 = m.call_args
    check("composer route: FIRST post-login compose -> first_turn=True", kwargs1.get("first_turn") is True, kwargs1)

    client.post("/composer", data={"instruction": "show me something else"})
    _, kwargs2 = m.call_args
    check("composer route: SECOND compose in same session -> first_turn=False (already asked once)",
          kwargs2.get("first_turn") is False, kwargs2)

    client.post("/composer", data={"instruction": "and a third"})
    _, kwargs3 = m.call_args
    check("composer route: THIRD compose in same session -> still first_turn=False",
          kwargs3.get("first_turn") is False, kwargs3)

# a fresh login (new session) resets the flag
client2 = studio.app.test_client()
r2 = client2.post("/login", data={"username": "kowsick", "password": "owner123"}, follow_redirects=True)
assert r2.status_code == 200
with mock.patch.object(agent_composer, "run_agent", return_value={"text": "ok"}) as m2:
    client2.post("/composer", data={"instruction": "brand new session"})
    _, kwargs4 = m2.call_args
    check("composer route: a NEW login session -> first_turn=True again", kwargs4.get("first_turn") is True, kwargs4)


print(f"\n=== SUMMARY: {passed} passed, {failed} failed ===")
if failed:
    sys.exit(1)
