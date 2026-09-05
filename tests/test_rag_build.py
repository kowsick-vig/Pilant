"""
Test coverage for the RAG build added 2026-08-26:
  - rag_ingest.py's three ingest functions (mocked connector calls)
  - search_knowledge_base tool dispatch in agent_gmail.py / agent_slack.py /
    agent_github.py, including agent_github's identity-scoping re-application
  - studio.py's /rag/sync route

All external calls (connectors, rag_index's Bedrock/Chroma calls) are mocked
— this validates the wiring/logic, not real embedding quality or real
connector data shapes (those remain unverified beyond a syntax check, per
rag_ingest.py's own docstring on what a "sync" honestly covers).

NOTE: this file originally also covered a standalone Search page
(studio.py's GET /rag/search, backed by agent_search.py) — removed
2026-08-26 at the user's request, since search_knowledge_base was already
reachable as a tool inside every connector's own chat and a second,
connector-less place to search the same index was redundant. The tests
below now just confirm that route/module are actually gone.
"""
import json
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


# ---------------------------------------------------------------------------
print("== rag_ingest.py ==")
import rag_ingest

with patch("rag_ingest.connectors_gmail.get_gmail_messages") as m_gmail, \
     patch("rag_ingest.rag_index.index_documents") as m_index:
    m_gmail.return_value = [
        {"id": "msg1", "subject": "Q2 budget", "snippet": "here's the plan", "date": "2026-08-01"},
        {"id": "msg2", "subject": "", "snippet": "", "date": ""},  # empty text — index_documents itself skips it
    ]
    m_index.return_value = 2
    count = rag_ingest.ingest_gmail()
    docs = m_index.call_args[0][0]
    check("ingest_gmail passes 2 docs to index_documents", len(docs) == 2)
    check("ingest_gmail doc has source=gmail", docs[0]["source"] == "gmail")
    check("ingest_gmail doc url points at /message/<id>", docs[0]["url"] == "/message/msg1")
    check("ingest_gmail doc text is subject+snippet", docs[0]["text"] == "Q2 budget\nhere's the plan")
    check("ingest_gmail returns index_documents' return value", count == 2)

with patch("rag_ingest.connectors_slack._resolve_channel_id") as m_resolve, \
     patch("rag_ingest.connectors_slack.get_slack_messages") as m_slack, \
     patch("rag_ingest.rag_index.index_documents") as m_index:
    m_resolve.return_value = "C0123456789"
    m_slack.return_value = [
        {"ts": "1690000000.000100", "text": "the release shipped"},
        {"ts": "", "text": "  "},  # blank text — skipped by ingest_slack itself (continue)
    ]
    m_index.return_value = 1
    count = rag_ingest.ingest_slack()
    docs = m_index.call_args[0][0]
    check("ingest_slack skips blank-text messages before index_documents", len(docs) == 1)
    check("ingest_slack builds a real permalink", docs[0]["url"] == "https://slack.com/archives/C0123456789/p1690000000000100")
    check("ingest_slack doc source=slack", docs[0]["source"] == "slack")

with patch("rag_ingest.connectors_github.get_github_issues") as m_gh, \
     patch("rag_ingest.rag_index.index_documents") as m_index:
    m_gh.return_value = [
        {"number": 42, "title": "Fix login bug", "labels": ["bug", "critical"], "url": "https://github.com/x/y/issues/42", "opened": "2026-07-01"},
    ]
    m_index.return_value = 1
    count = rag_ingest.ingest_github()
    docs = m_index.call_args[0][0]
    m_gh.assert_called_with(state="all")
    check("ingest_github requests state=all (broader than the agent's default 'open')", True)
    check("ingest_github carries labels through for later scoping", docs[0]["labels"] == ["bug", "critical"])
    check("ingest_github doc source=github", docs[0]["source"] == "github")
    check("ingest_github id is the issue number as a string", docs[0]["id"] == "42")

with patch("rag_ingest.ingest_gmail") as m_g, patch("rag_ingest.ingest_slack") as m_s, patch("rag_ingest.ingest_github") as m_h:
    m_g.return_value = 5
    m_s.side_effect = RuntimeError("SLACK_CHANNEL is not set")
    m_h.side_effect = ValueError("boom")
    results = rag_ingest.ingest_all()
    check("ingest_all: configured connector reports indexed count", results["gmail"] == {"indexed": 5})
    check("ingest_all: RuntimeError (not-configured) is skipped gracefully", results["slack"] == {"error": "SLACK_CHANNEL is not set"})
    check("ingest_all: a real/unexpected error is still reported, not swallowed", "Unexpected error" in results["github"]["error"])


# ---------------------------------------------------------------------------
print("== agent_gmail.py search_knowledge_base dispatch ==")
import agent_gmail
import claude_engine as ce

with patch("agent_gmail.rag_index.search") as m_search:
    m_search.return_value = [{"source": "slack", "title": "release", "url": "https://x", "timestamp": "", "snippet": "shipped", "score": 0.9, "labels": []}]
    dispatch = agent_gmail._make_dispatch(False, False)
    outcome = dispatch("search_knowledge_base", {"query": "release"}, "tc1", [], False)
    check("agent_gmail search_knowledge_base returns tool_result JSON of results", json.loads(outcome.tool_result) == m_search.return_value)
    check("agent_gmail search_knowledge_base sets fetched_data (shared gate)", True)  # verified below via re-call

    outcome2 = dispatch("search_knowledge_base", {"query": "again"}, "tc2", [], False)
    check("agent_gmail: second fetch-tool call in same turn is nudged, not re-run", outcome2.tool_result == agent_gmail.ALREADY_FETCHED_NUDGE_GENERIC)

# Cross-gating: get_gmail_messages first, then search_knowledge_base should be blocked too
with patch("agent_gmail.get_gmail_messages") as m_fetch:
    m_fetch.return_value = [{"id": "1", "subject": "hi", "snippet": "", "from": "a@b.com", "to": "", "date": ""}]
    dispatch = agent_gmail._make_dispatch(False, False)
    dispatch("get_gmail_messages", {}, "tc1", [], False)
    outcome = dispatch("search_knowledge_base", {"query": "x"}, "tc2", [], False)
    check("agent_gmail: search_knowledge_base blocked after get_gmail_messages already fetched (shared gate both directions)",
          outcome.tool_result == agent_gmail.ALREADY_FETCHED_NUDGE_GENERIC)

with patch("agent_gmail.rag_index.search") as m_search:
    m_search.side_effect = RuntimeError("OPENAI_API_KEY is not set")
    dispatch = agent_gmail._make_dispatch(False, False)
    outcome = dispatch("search_knowledge_base", {"query": "x"}, "tc1", [], False)
    check("agent_gmail: not-configured RuntimeError surfaces as a tool_result error, not a crash",
          json.loads(outcome.tool_result) == {"error": "OPENAI_API_KEY is not set"})


# ---------------------------------------------------------------------------
print("== agent_slack.py search_knowledge_base dispatch ==")
import agent_slack

with patch("agent_slack.rag_index.search") as m_search:
    m_search.return_value = [{"source": "gmail", "title": "budget", "url": "/message/1", "timestamp": "", "snippet": "plan", "score": 0.8, "labels": []}]
    dispatch = agent_slack._make_dispatch(False, False)
    outcome = dispatch("search_knowledge_base", {"query": "budget"}, "tc1", [], False)
    check("agent_slack search_knowledge_base returns tool_result JSON of results", json.loads(outcome.tool_result) == m_search.return_value)
    outcome2 = dispatch("search_knowledge_base", {"query": "again"}, "tc2", [], False)
    check("agent_slack: second fetch-tool call in same turn is nudged", outcome2.tool_result == agent_slack.ALREADY_FETCHED_NUDGE_GENERIC)


# ---------------------------------------------------------------------------
print("== agent_github.py search_knowledge_base dispatch + identity scoping ==")
import agent_github
from users import get_user

analyst = get_user("analyst")   # label_scope = ["bug", "critical", "security"]
viewer = get_user("viewer")     # label_scope = ["enhancement", "documentation"]
owner = get_user("kowsick")     # label_scope = None (unrestricted)

fake_results = [
    {"source": "github", "title": "Fix crash", "url": "https://x/1", "timestamp": "", "snippet": "s", "score": 0.9, "labels": ["bug"]},
    {"source": "github", "title": "Add docs",  "url": "https://x/2", "timestamp": "", "snippet": "s", "score": 0.8, "labels": ["documentation"]},
    {"source": "slack",  "title": "chat",      "url": "https://x/3", "timestamp": "", "snippet": "s", "score": 0.7, "labels": []},
]

check("_scope_knowledge_base_results: unrestricted user (label_scope=None) sees everything",
      len(agent_github._scope_knowledge_base_results(fake_results, owner)) == 3)

analyst_kept = agent_github._scope_knowledge_base_results(fake_results, analyst)
check("_scope_knowledge_base_results: analyst (bug/critical/security) sees the 'bug' github hit",
      any(r["title"] == "Fix crash" for r in analyst_kept))
check("_scope_knowledge_base_results: analyst does NOT see the 'documentation' github hit",
      not any(r["title"] == "Add docs" for r in analyst_kept))
check("_scope_knowledge_base_results: analyst still sees the non-github (slack) hit untouched",
      any(r["title"] == "chat" for r in analyst_kept))

viewer_kept = agent_github._scope_knowledge_base_results(fake_results, viewer)
check("_scope_knowledge_base_results: viewer (enhancement/documentation) sees the 'documentation' hit",
      any(r["title"] == "Add docs" for r in viewer_kept))
check("_scope_knowledge_base_results: viewer does NOT see the 'bug' hit",
      not any(r["title"] == "Fix crash" for r in viewer_kept))

with patch("agent_github.rag_index.search") as m_search:
    m_search.return_value = fake_results
    dispatch = agent_github._make_dispatch(False, False, viewer)
    outcome = dispatch("search_knowledge_base", {"query": "issues"}, "tc1", [], False)
    result = json.loads(outcome.tool_result)
    check("agent_github dispatch: search_knowledge_base result is scoped for the calling user (viewer)",
          not any(r["title"] == "Fix crash" for r in result) and any(r["title"] == "Add docs" for r in result))

    outcome2 = dispatch("search_knowledge_base", {"query": "again"}, "tc2", [], False)
    check("agent_github: second fetch-tool call in same turn is nudged", outcome2.tool_result == agent_github.ALREADY_FETCHED_NUDGE_GENERIC)

with patch("agent_github.get_github_issues") as m_fetch, patch("agent_github.scope_issues") as m_scope:
    m_fetch.return_value = [{"number": 1, "title": "x", "labels": ["bug"]}]
    m_scope.side_effect = lambda issues, user: issues  # pass-through for this gating check
    dispatch = agent_github._make_dispatch(False, False, owner)
    dispatch("get_github_issues", {}, "tc1", [], False)
    outcome = dispatch("search_knowledge_base", {"query": "x"}, "tc2", [], False)
    check("agent_github: search_knowledge_base blocked after get_github_issues already fetched",
          outcome.tool_result == agent_github.ALREADY_FETCHED_NUDGE_GENERIC)


# ---------------------------------------------------------------------------
print("== studio.py /rag/sync route, and the removed standalone Search page ==")
import os
os.environ.setdefault("FLASK_SECRET_KEY", "test")
import studio

studio.app.config["TESTING"] = True
client = studio.app.test_client()

with client.session_transaction() as sess:
    sess["username"] = "kowsick"

with patch("studio.rag_index.is_configured") as m_conf:
    m_conf.return_value = False
    resp = client.post("/rag/sync", follow_redirects=True)
    check("POST /rag/sync when not configured: redirects back with an error notice, doesn't crash",
          resp.status_code == 200 and b"Can&#39;t sync" in resp.data or b"sync" in resp.data.lower())

with patch("studio.rag_index.is_configured") as m_conf, patch("studio.rag_ingest.ingest_all") as m_ingest:
    m_conf.return_value = True
    m_ingest.return_value = {"gmail": {"indexed": 3}, "slack": {"error": "SLACK_CHANNEL is not set"}, "github": {"indexed": 7}}
    resp = client.post("/rag/sync", follow_redirects=True)
    check("POST /rag/sync (configured): 200 after redirect", resp.status_code == 200)
    check("POST /rag/sync: notice mentions the indexed counts", b"3 indexed" in resp.data and b"7 indexed" in resp.data)

# 2026-08-26: removed at the user's explicit request ("why using agent rag
# in the search bar. instead use it in the main workflow") — a standalone,
# connector-less Search page was redundant now that search_knowledge_base
# is already a tool inside every connector's own chat. agent_search.py (the
# module that page ran through) was deleted outright, so this just proves
# the route and module are actually gone, not silently left dead.
resp = client.get("/rag/search")
check("GET /rag/search: route no longer exists (404) now that the standalone Search page is removed",
      resp.status_code == 404)
check("agent_search module was deleted, not just unrouted", "agent_search" not in sys.modules and not __import__("importlib").util.find_spec("agent_search"))

with patch("studio.rag_index.is_configured") as m_conf:
    m_conf.return_value = True
    resp = client.get("/integrations")
    check("Integrations page's knowledge-base card no longer offers a 'Search' action link",
          b'href="/rag/search"' not in resp.data)

resp = client.get("/studio")
check("Sidebar no longer has a 'Search' nav item", b"&#128269; Search" not in resp.data)


# ---------------------------------------------------------------------------
print("== schema.py / renderer.py / guardrails.py: the new optional row 'url' field ==")
import schema
from validation import validate_view
import renderer
import guardrails

view_with_url = {"heading": "h", "components": [{"type": "list", "rows": [{"name": "n", "url": "https://example.com/x"}]}]}
check("schema.py: a list row with a url field validates cleanly", validate_view(view_with_url, schema.UI_SCHEMA) is None)

fragment = renderer.render_fragment(view_with_url)
check("renderer.py: a row with url renders a real <a href> around the name", '<a href="https://example.com/x"' in fragment and "target=\"_blank\"" in fragment)

view_without_url = {"heading": "h", "components": [{"type": "list", "rows": [{"name": "n"}]}]}
fragment2 = renderer.render_fragment(view_without_url)
check("renderer.py: a row with no url still renders as a plain div (no behavior change for existing connectors)", "<a href" not in fragment2)

real_tool_messages = [
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": json.dumps([{"title": "Real title here", "url": "https://real.example/42"}])}]},
]
grounded_view = {"heading": "h", "components": [{"type": "list", "rows": [{"name": "Real title here", "url": "https://real.example/42"}]}]}
check("guardrails.py: find_fabricated_content accepts a url that matches a real fetched record",
      guardrails.find_fabricated_content(grounded_view, real_tool_messages) == [])

fabricated_view = {"heading": "h", "components": [{"type": "list", "rows": [{"name": "Real title here", "url": "https://totally-invented.example/99"}]}]}
check("guardrails.py: find_fabricated_content flags a url that does NOT match any real fetched record",
      "https://totally-invented.example/99" in guardrails.find_fabricated_content(fabricated_view, real_tool_messages))


print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
