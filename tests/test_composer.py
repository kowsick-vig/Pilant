"""
Test coverage for the Composer build added 2026-08-26 — building toward
"the full DynamisOS product" at the user's explicit request (see
primitives.py's, agent_composer.py's, and studio.py's module docstrings/
comments on the Composer for the full reasoning):
  - primitives.py's Registry (register/get/all/by_connector/as_tool_schema/call)
  - agent_composer.py's dispatch (cross-primitive fetch, identity scoping
    via the registry's own `scope` hook, required-primitive-ids gating,
    guardrails still apply unchanged)
  - saved_views.py's primitive_ids extension (backward compatible with
    server.py's existing 3-arg calls)
  - studio.py's /composer, /composer/save, /composer/open/<id>,
    /composer/<id>/delete routes

All external calls (connectors, the model) are mocked — this validates the
wiring/logic, not real API behavior.
"""
import json
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/claude/pilant-agent")

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


from users import get_user
owner = get_user("kowsick")
viewer = get_user("viewer")

# ---------------------------------------------------------------------------
print("== primitives.py: Registry ==")
from primitives import Registry, REGISTRY

r = Registry()
r.register(id="fake_one", type="data_source", connector="fake", label="Fake One",
           description="d", input_schema={"type": "object", "properties": {}}, handler=lambda: [1, 2, 3])
check("register()/get(): a registered primitive is retrievable by id", r.get("fake_one")["label"] == "Fake One")
check("get(): an unknown id returns None, doesn't raise", r.get("nope") is None)
check("all(): returns every registered primitive", len(r.all()) == 1)
check("by_connector(): filters correctly", len(r.by_connector("fake")) == 1 and len(r.by_connector("other")) == 0)

tool = r.as_tool_schema("fake_one")
check("as_tool_schema(): produces the exact {name,description,input_schema} shape a Claude tool needs",
      set(tool.keys()) == {"name", "description", "input_schema"} and tool["name"] == "fake_one")

result = r.call("fake_one", {})
check("call(): actually invokes the real handler", result == [1, 2, 3])

try:
    r.register(id="fake_one", type="data_source", connector="fake", label="dup",
               description="d", input_schema={}, handler=lambda: [])
    check("register(): duplicate id raises", False)
except ValueError:
    check("register(): duplicate id raises ValueError", True)

# Regression test for the real bug found live 2026-08-26: a dotted id
# ("gmail.messages") passed Registry.register() with no complaint, then
# broke EVERY Composer request with an opaque BadRequestError from
# Anthropic's API, because as_tool_schema() uses the id as a tool's real
# "name" verbatim, and Anthropic requires ^[a-zA-Z0-9_-]+$ — no dots. This
# proves that mistake can't silently ship again.
for bad_id in ("dotted.id", "has space", "semi;colon"):
    try:
        r.register(id=bad_id, type="data_source", connector="fake", label="bad",
                   description="d", input_schema={}, handler=lambda: [])
        check(f"register(): invalid id {bad_id!r} raises", False)
    except ValueError as e:
        check(f"register(): invalid id {bad_id!r} raises ValueError naming the real constraint",
              "tool-name" in str(e) or "invalid" in str(e).lower())
check("register(): a valid underscore/hyphen id is accepted", (
    r.register(id="valid_id-1", type="data_source", connector="fake", label="ok",
               description="d", input_schema={}, handler=lambda: []) or True
))

# scope hook
r2 = Registry()
r2.register(id="scoped_thing", type="data_source", connector="x", label="Scoped",
            description="d", input_schema={}, handler=lambda: [{"labels": ["bug"]}, {"labels": ["docs"]}],
            scope=lambda result, user: [i for i in result if "bug" in i["labels"]] if user.get("label_scope") else result)
scoped_result = r2.call("scoped_thing", {}, user={"label_scope": ["bug"]})
check("call(): applies the scope hook when a user is given", scoped_result == [{"labels": ["bug"]}])
unscoped_result = r2.call("scoped_thing", {}, user=None)
check("call(): skips the scope hook when no user is given", len(unscoped_result) == 2)

print("== primitives.py: real registrations (gmail_messages/slack_messages/github_issues/knowledge_base_search) ==")
check("every REAL registered primitive id is Anthropic-tool-name-valid (would have caught the dotted-id bug directly)",
      all(Registry._VALID_ID_RE.match(p["id"]) for p in REGISTRY.all()))
ids = {p["id"] for p in REGISTRY.all()}
check("all 4 expected primitives are registered", ids == {"gmail_messages", "slack_messages", "github_issues", "knowledge_base_search"})
check("github_issues has a scope hook wired (identity scoping)", REGISTRY.get("github_issues")["scope"] is not None)
check("knowledge_base_search has a scope hook wired (same rag_scope rule every connector uses)", REGISTRY.get("knowledge_base_search")["scope"] is not None)
check("gmail_messages has NO scope hook (Gmail has no label-scope concept, same as agent_gmail.py)", REGISTRY.get("gmail_messages")["scope"] is None)

# primitives.py's registry captured connectors_github.get_github_issues by
# REFERENCE at registration time (module import), so patching the
# connectors_github module attribute afterward wouldn't reach the already-
# bound reference — swap the registry entry's own "handler" key instead,
# then restore it, same effect without patching the wrong target.
_real_gh_handler = REGISTRY.get("github_issues")["handler"]
REGISTRY.get("github_issues")["handler"] = lambda **kw: [
    {"title": "bug1", "labels": ["bug"]}, {"title": "doc1", "labels": ["documentation"]},
]
scoped = REGISTRY.call("github_issues", {}, user=viewer)
check("REGISTRY.call('github_issues', ..., user=viewer): identity scoping actually filters through the registry path",
      scoped == [{"title": "doc1", "labels": ["documentation"]}])
unrestricted = REGISTRY.call("github_issues", {}, user=owner)
check("REGISTRY.call('github_issues', ..., user=owner): unrestricted user sees everything", len(unrestricted) == 2)
REGISTRY.get("github_issues")["handler"] = _real_gh_handler

# ---------------------------------------------------------------------------
print("== agent_composer.py: cross-primitive dispatch ==")
import agent_composer as ac

with patch.object(ac.REGISTRY, "call") as m_call:
    def fake_call(id, args, user=None):
        if id == "gmail_messages":
            return [{"from": "a@x.com", "subject": "Q2 budget", "snippet": "s"}]
        if id == "github_issues":
            return [{"title": "Budget tracker bug", "number": 1, "labels": ["bug"]}]
        return []
    m_call.side_effect = fake_call

    dispatch = ac._make_dispatch(False, False, owner, None)
    o1 = dispatch("gmail_messages", {"query": "budget"}, "tc1", [], False)
    check("dispatch: a real primitive id returns tool_result JSON of its real result",
          json.loads(o1.tool_result) == [{"from": "a@x.com", "subject": "Q2 budget", "snippet": "s"}])
    o2 = dispatch("github_issues", {}, "tc2", [], False)
    check("dispatch: a SECOND, DIFFERENT primitive in the same turn also fetches for real (the whole point of the Composer)",
          json.loads(o2.tool_result)[0]["title"] == "Budget tracker bug")
    o3 = dispatch("gmail_messages", {"query": "again"}, "tc3", [], False)
    check("dispatch: calling the SAME primitive again in one turn is nudged, not re-run",
          "was not run again" in o3.tool_result)

with patch.object(ac.REGISTRY, "call") as m_call:
    m_call.side_effect = RuntimeError("GITHUB_REPO is not set in .env")
    dispatch = ac._make_dispatch(False, False, owner, None)
    outcome = dispatch("github_issues", {}, "tc1", [], False)
    check("dispatch: a real RuntimeError (not-configured) surfaces as a tool_result error, not a crash",
          json.loads(outcome.tool_result) == {"error": "GITHUB_REPO is not set in .env"})
    outcome2 = dispatch("github_issues", {}, "tc2", [], False)
    check("dispatch: the SAME error twice in a row stops early via final={'error': ...} instead of looping forever",
          outcome2.final == {"error": "GITHUB_REPO is not set in .env"})

print("== agent_composer.py: required primitive_ids gating (the canvas-selection contract) ==")
with patch.object(ac.REGISTRY, "call") as m_call:
    m_call.return_value = [{"x": "y"}]
    dispatch = ac._make_dispatch(False, False, owner, ["gmail_messages", "github_issues"])
    dispatch("gmail_messages", {}, "tc1", [], False)
    out = dispatch("render_view", {"heading": "h", "components": []}, "tc2", [], False)
    check("render_view is rejected while a canvas-selected primitive hasn't been called yet",
          "github_issues" in out.tool_result and out.final is None)
    dispatch("github_issues", {}, "tc3", [], False)
    out2 = dispatch("render_view", {"heading": "h", "components": []}, "tc4", [], False)
    check("render_view is accepted once every canvas-selected primitive has been called",
          out2.final is not None and "render" in out2.final)

print("== agent_composer.py: render_view path — grounding, guardrails still apply unchanged ==")
with patch.object(ac.REGISTRY, "call") as m_call:
    m_call.return_value = [{"source": "gmail", "title": "Q2 budget plan", "url": "/message/42", "snippet": "the real plan"}]
    dispatch = ac._make_dispatch(False, False, owner, None)
    dispatch("knowledge_base_search", {"query": "budget"}, "tc1", [], False)
    prior_messages = [
        {"role": "user", "content": "find anything about the budget"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "tc1", "name": "knowledge_base_search", "input": {"query": "budget"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tc1", "content": json.dumps(m_call.return_value)}]},
    ]
    good_view = {"heading": "1 result", "components": [{"type": "list", "rows": [
        {"name": "Q2 budget plan", "note": "the real plan", "url": "/message/42"},
    ]}]}
    outcome = dispatch("render_view", good_view, "tc2", prior_messages, False)
    check("a render fully grounded in real fetched data is accepted",
          outcome.final is not None and "render" in outcome.final)

    bad_view = {"heading": "1 result", "components": [{"type": "list", "rows": [
        {"name": "Completely invented headline nobody searched for", "note": "made up", "url": "https://evil.example/not-real"},
    ]}]}
    outcome2 = dispatch("render_view", bad_view, "tc3", prior_messages, False)
    check("a fabricated row is rejected by the SAME guardrails.py check every other connector uses, unchanged",
          outcome2.final is None and "rejected" in outcome2.tool_result.lower())

print("== agent_composer.py: run_agent() builds tools from the live registry ==")
tools = [ac.REGISTRY.as_tool_schema(p["id"]) for p in ac.REGISTRY.all()] + [ac.ASK_USER_TOOL, ac.RENDER_VIEW_TOOL]
tool_names = {t["name"] for t in tools}
check("run_agent's tool list includes every registered primitive plus ask_user/render_view",
      tool_names == {"gmail_messages", "slack_messages", "github_issues", "knowledge_base_search", "ask_user", "render_view"})

system_with_selection = ac._build_system(["gmail_messages"])
check("_build_system: names the canvas-selected primitives explicitly as REQUIRED",
      "gmail_messages" in system_with_selection and "MUST call each of them" in system_with_selection)
system_no_selection = ac._build_system(None)
check("_build_system: pure-NL mode (no selection) doesn't mention a required list",
      "MUST call each of them" not in system_no_selection)

print("== user_style.py + agent_composer.py: the actual per-person customization ==")
import user_style
import tempfile

_orig_style_store = user_style._STORE_PATH
_tmp_style_store = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp_style_store.close()
user_style._STORE_PATH = __import__("pathlib").Path(_tmp_style_store.name)

check("get_style(): a user who never set one gets \"\", not an error", user_style.get_style("nobody") == "")
user_style.set_style("kowsick", "  compact lists, grouped by urgency  ")
check("set_style()/get_style(): stores and returns the trimmed value", user_style.get_style("kowsick") == "compact lists, grouped by urgency")
user_style.set_style("viewer", "big stat_grid dashboards, I like numbers up top")
check("set_style(): different users get independently stored styles", user_style.get_style("viewer") != user_style.get_style("kowsick"))
user_style.set_style("kowsick", "new value")
check("set_style(): overwrites, doesn't append, on a second call", user_style.get_style("kowsick") == "new value")

system_no_style = ac._build_system(None, style=None)
check("_build_system: no style set — no style instruction in SYSTEM at all", "how THEY like their interface shaped" not in system_no_style)
system_with_style = ac._build_system(None, style="compact lists, grouped by urgency")
check("_build_system: a real style note becomes a real instruction in SYSTEM",
      "compact lists, grouped by urgency" in system_with_style and "how THEY like their interface shaped" in system_with_style)
system_with_style2 = ac._build_system(None, style="big stat_grid dashboards, I like numbers up top")
check("_build_system: two DIFFERENT users' styles produce two DIFFERENTLY-WORDED system prompts — the actual mechanism behind per-person customization",
      system_with_style != system_with_style2)

# ce.run_claude_agent is mocked too here — this checks run_agent()'s OWN
# wiring (does it look up the calling user's style and pass it into
# _build_system) without making a real network call to Anthropic. The
# real end-to-end proof — two users' different styles genuinely producing
# different render_view shapes from the SAME real data, against the real
# API — was verified manually this session (not baked into this fast,
# fully-mocked suite, same as the rest of it).
with patch("agent_composer.user_style.get_style") as m_get_style, patch("agent_composer.ce.run_claude_agent") as m_run:
    m_get_style.return_value = "always use a stat_grid, never a list"
    m_run.return_value = {"text": "stub"}
    ac.run_agent("anything", user=owner, primitive_ids=None, max_steps=1, verbose=False)
    check("run_agent(): actually calls user_style.get_style() for the CALLING user, not a hardcoded default",
          m_get_style.call_args[0][0] == "kowsick")
    check("run_agent(): the fetched style actually reaches the SYSTEM prompt handed to the model",
          "always use a stat_grid, never a list" in m_run.call_args[1]["system"])

user_style._STORE_PATH = _orig_style_store
os.unlink(_tmp_style_store.name)

# ---------------------------------------------------------------------------
print("== saved_views.py: primitive_ids extension is backward compatible ==")
import saved_views

_orig_store = saved_views._STORE_PATH
import tempfile
_tmp_store = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp_store.close()
saved_views._STORE_PATH = __import__("pathlib").Path(_tmp_store.name)

v1 = saved_views.save_view("testuser", "old style", "some plain query")
check("save_view() with the OLD 3-arg call (server.py's usage) still works, no primitive_ids key at all",
      "primitive_ids" not in v1)

v2 = saved_views.save_view("testuser", "composition", "budget stuff", primitive_ids=["gmail_messages", "github_issues"])
check("save_view() with primitive_ids stores them", v2["primitive_ids"] == ["gmail_messages", "github_issues"])
check("get_view() returns the stored primitive_ids", saved_views.get_view("testuser", v2["id"])["primitive_ids"] == ["gmail_messages", "github_issues"])

saved_views._STORE_PATH = _orig_store
os.unlink(_tmp_store.name)

# ---------------------------------------------------------------------------
print("== studio.py: /composer routes ==")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
import studio

# Redirect saved_views.py's storage to a scratch file for the route tests
# below (composer_save/composer_open/composer_delete all go through it) so
# this test run doesn't leave test entries in the sandbox's real
# saved_views.json. studio.py imported save_view/get_view/delete_view by
# name, but those functions still read saved_views._STORE_PATH as a module
# global at CALL time, so patching it here affects them too.
_tmp_store2 = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp_store2.close()
saved_views._STORE_PATH = __import__("pathlib").Path(_tmp_store2.name)

# Same reasoning, same swap, for user_style.py's storage — studio.py
# imported it as a module (`import user_style`), and its functions read
# user_style._STORE_PATH at call time, so this reaches studio.py's calls
# too.
_tmp_style_store2 = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp_style_store2.close()
user_style_orig_path = user_style._STORE_PATH
user_style._STORE_PATH = __import__("pathlib").Path(_tmp_style_store2.name)

studio.app.config["TESTING"] = True
client = studio.app.test_client()
with client.session_transaction() as sess:
    sess["username"] = "kowsick"

resp = client.get("/composer")
check("GET /composer: 200, shows the primitive palette", resp.status_code == 200 and b"gmail_messages" in resp.data)
check("GET /composer: sidebar has a Composer nav link", b"Composer" in resp.data)
check("GET /composer: shows the 'Your interface style' box", b"Your interface style" in resp.data)

resp = client.post("/composer/style", data={"style": "compact lists, grouped by urgency"})
check("POST /composer/style: 200, shows a confirmation note", resp.status_code == 200 and b"Style saved" in resp.data)
check("POST /composer/style: the saved style now shows pre-filled in the box", b"compact lists, grouped by urgency" in resp.data)
check("POST /composer/style: actually persisted via user_style.get_style, not just echoed back this response",
      user_style.get_style("kowsick") == "compact lists, grouped by urgency")

resp = client.post("/composer", data={})
check("POST /composer with nothing selected and no instruction: friendly error, not a crash",
      resp.status_code == 200 and b"Pick at least one primitive" in resp.data)

with patch("studio.agent_composer.run_agent") as m_run:
    m_run.return_value = {"render": {
        "heading": "Budget-related items",
        "components": [{"type": "list", "rows": [
            {"name": "Q2 budget plan", "note": "from Gmail", "badge": {"text": "Gmail", "tone": "default"}},
            {"name": "Budget tracker bug", "note": "from GitHub", "badge": {"text": "GitHub", "tone": "default"}},
        ]}],
    }}
    resp = client.post("/composer", data={"primitive_ids": ["gmail_messages", "github_issues"], "instruction": "budget stuff"})
    check("POST /composer with a canvas selection: 200, renders the composed result", resp.status_code == 200 and b"Budget-related items" in resp.data)
    check("POST /composer: both cross-connector rows actually render", b"Q2 budget plan" in resp.data and b"Budget tracker bug" in resp.data)
    check("POST /composer: passes primitive_ids through to agent_composer.run_agent",
          set(m_run.call_args[1]["primitive_ids"]) == {"gmail_messages", "github_issues"})
    check("POST /composer: shows a 'Save this composition' form", b"composer-save-form" in resp.data)

with patch("studio.agent_composer.run_agent") as m_run:
    m_run.return_value = {"clarify": "which repo do you mean?"}
    resp = client.post("/composer", data={"primitive_ids": ["github_issues"]})
    check("POST /composer: a clarify result shows as a note, not a crash", resp.status_code == 200 and b"which repo" in resp.data)

with patch("studio.agent_composer.run_agent") as m_run:
    m_run.return_value = {"error": "hit max_steps without a valid render_view"}
    resp = client.post("/composer", data={"instruction": "anything"})
    check("POST /composer: an agent-side error shows as a note, not a 500", resp.status_code == 200)

print("== studio.py: /composer/save, /composer/open/<id>, /composer/<id>/delete ==")
with patch("studio.agent_composer.run_agent") as m_run:
    m_run.return_value = {"render": {"heading": "h", "components": []}}
    resp = client.post("/composer/save", data={
        "name": "My budget view", "instruction": "budget stuff",
        "primitive_ids": ["gmail_messages", "github_issues"],
    }, follow_redirects=False)
    check("POST /composer/save: redirects to /composer/open/<new-id>", resp.status_code == 302 and "/composer/open/" in resp.headers["Location"])
    view_id = resp.headers["Location"].rsplit("/", 1)[-1]

with patch("studio.agent_composer.run_agent") as m_run:
    m_run.return_value = {"render": {"heading": "Regenerated live", "components": []}}
    resp = client.get(f"/composer/open/{view_id}")
    check("GET /composer/open/<id>: 200, re-runs live (not a stored snapshot)", resp.status_code == 200 and b"Regenerated live" in resp.data)
    check("GET /composer/open/<id>: shows the saved name in a 'reopened' banner", b"My budget view" in resp.data)
    check("GET /composer/open/<id>: passes the SAVED primitive_ids through on reopen",
          set(m_run.call_args[1]["primitive_ids"]) == {"gmail_messages", "github_issues"})

resp = client.get("/composer/open/does-not-exist")
check("GET /composer/open/<bad-id>: redirects to a fresh Composer, doesn't error", resp.status_code == 302 and resp.headers["Location"].endswith("/composer"))

# ownership: a saved view belongs to kowsick; viewer must not be able to open it
with client.session_transaction() as sess:
    sess["username"] = "viewer"
resp = client.get(f"/composer/open/{view_id}")
check("GET /composer/open/<id>: another user can't open someone else's saved composition (same trust boundary as saved_views.py)",
      resp.status_code == 302 and resp.headers["Location"].endswith("/composer"))

with client.session_transaction() as sess:
    sess["username"] = "kowsick"
resp = client.post(f"/composer/{view_id}/delete", follow_redirects=False)
check("POST /composer/<id>/delete: redirects back to /composer", resp.status_code == 302)
resp = client.get(f"/composer/open/{view_id}")
check("GET /composer/open/<id> after delete: gone, redirects to fresh Composer", resp.status_code == 302 and resp.headers["Location"].endswith("/composer"))

saved_views._STORE_PATH = _orig_store
os.unlink(_tmp_store2.name)
user_style._STORE_PATH = user_style_orig_path
os.unlink(_tmp_style_store2.name)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
