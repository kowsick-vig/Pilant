"""
Live verification of the migrated agent_github.py (NVIDIA -> Claude, 2026-08-26).
Mirrors the gmail/slack migration tests, plus this connector's two real differences:
force_tool_choice=True (no conversational-reply mode) and identity-scoped fetches via
scope_issues(). connectors_github.get_github_issues is mocked (no live GitHub token in
this sandbox), but every model call goes to the REAL Anthropic API.

Run: python3 /tmp/test_agent_github_migration.py
"""
import sys, os, json, time, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_github
from users import get_user

FAKE_ISSUES = [
    {"title": "Login page crashes on submit", "number": 101, "state": "open", "labels": ["bug", "critical"], "created_at": "2026-08-25T10:00:00Z", "comments": 4, "url": "https://github.com/x/x/issues/101"},
    {"title": "Add dark mode toggle", "number": 102, "state": "open", "labels": ["enhancement"], "created_at": "2026-08-24T10:00:00Z", "comments": 1, "url": "https://github.com/x/x/issues/102"},
    {"title": "Update README with setup instructions", "number": 103, "state": "open", "labels": ["documentation"], "created_at": "2026-08-23T10:00:00Z", "comments": 0, "url": "https://github.com/x/x/issues/103"},
    {"title": "SQL injection in search endpoint", "number": 104, "state": "open", "labels": ["security", "critical"], "created_at": "2026-08-22T10:00:00Z", "comments": 6, "url": "https://github.com/x/x/issues/104"},
]

connectors_github.get_github_issues = mock.Mock(return_value=FAKE_ISSUES)

import agent_github
agent_github.get_github_issues = connectors_github.get_github_issues

PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print(f"  PASS: {name}")
    else:
        FAIL.append(name)
        print(f"  FAIL: {name}  {detail}")


owner = get_user("kowsick")       # label_scope=None, unrestricted
analyst = get_user("analyst")     # label_scope=["bug", "critical", "security"]

# ---------------------------------------------------------------------------
print("\n=== Test 1: real data fetch + render, UNRESTRICTED user (live API) ===")
t0 = time.time()
result = agent_github.run_agent("what open issues are there?", user=owner, verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result, indent=2, default=str)[:2500])

check("test1: no error key", "error" not in result, result.get("error"))
check("test1: got a render (force_tool_choice=True, no text mode)", "render" in result, result)
if "render" in result:
    blob = json.dumps(result["render"]).lower()
    check("test1: unrestricted user sees the bug issue", "login page crashes" in blob)
    check("test1: unrestricted user sees the enhancement issue", "dark mode" in blob)
    check("test1: unrestricted user sees the docs issue", "readme" in blob)
    check("test1: unrestricted user sees the security issue", "sql injection" in blob)
check("test1: get_github_issues called exactly once", connectors_github.get_github_issues.call_count == 1,
      f"call_count={connectors_github.get_github_issues.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 2: identity scoping — RESTRICTED analyst user (live API) ===")
connectors_github.get_github_issues.reset_mock()
t0 = time.time()
result2 = agent_github.run_agent("what open issues are there?", user=analyst, verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result2, indent=2, default=str)[:2500])

check("test2: no error key", "error" not in result2, result2.get("error"))
check("test2: got a render", "render" in result2, result2)
if "render" in result2:
    blob = json.dumps(result2["render"]).lower()
    check("test2: analyst (bug/critical/security scope) sees the bug issue", "login page crashes" in blob)
    check("test2: analyst sees the security issue", "sql injection" in blob)
    check("test2: analyst does NOT see the enhancement-only issue (out of scope)", "dark mode" not in blob)
    check("test2: analyst does NOT see the documentation-only issue (out of scope)", "readme" not in blob)
check("test2: get_github_issues called exactly once (scoping happens AFTER fetch, not a second call)",
      connectors_github.get_github_issues.call_count == 1, f"call_count={connectors_github.get_github_issues.call_count}")


# ---------------------------------------------------------------------------
print("\n=== Test 3: too-vague clarify round trip (live API, forced tool choice) ===")
connectors_github.get_github_issues.reset_mock()
t0 = time.time()
result3 = agent_github.run_agent("what needs attention?", user=owner, verbose=True)
elapsed = time.time() - t0
print(f"elapsed: {elapsed:.1f}s")
print(json.dumps(result3, indent=2, default=str)[:1500])

# force_tool_choice=True means every step must be a tool call -- so this is either a
# genuine ask_user clarify, or the model reasonably just fetches+renders its best
# interpretation (SYSTEM explicitly nudges toward "default to acting, not asking").
# Both are valid; what's NOT valid is an error or hitting max_steps.
check("test3: no error (either clarified or rendered a reasonable interpretation)",
      "error" not in result3, result3)
check("test3: got clarify or render (never bare text, since force_tool_choice=True)",
      "clarify" in result3 or "render" in result3, result3)

if "clarify" in result3:
    resumed = result3["messages"] + [{"role": "user", "content": "the critical/security ones"}]
    t0 = time.time()
    result3b = agent_github.run_agent(user=owner, messages=resumed, fetched_data=result3.get("fetched_data", False), verbose=True)
    elapsed = time.time() - t0
    print(f"resume elapsed: {elapsed:.1f}s")
    print(json.dumps(result3b, indent=2, default=str)[:1500])
    check("test3b: no error after resume", "error" not in result3b, result3b.get("error"))
    check("test3b: resume produces a render", "render" in result3b)


# ---------------------------------------------------------------------------
print("\n=== Test 4: fabrication guardrail unit test (dispatch-level, deterministic) ===")
dispatch = agent_github._make_dispatch(seed_fetched_data=True, verbose=False, user=owner)

fake_prior_messages = [
    {"role": "user", "content": "show me the issues"},
    {"role": "assistant", "content": [{"type": "tool_use", "id": "tc1", "name": "get_github_issues", "input": {}}]},
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tc1", "content": json.dumps(FAKE_ISSUES)},
        ],
    },
]

fabricated_view = {
    "heading": "Open Issues",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Totally invented issue nobody filed", "note": "This issue does not exist in the repo"},
            ],
        }
    ],
}
outcome = dispatch("render_view", fabricated_view, "tcX", fake_prior_messages, True)
check("test4: fabricated render_view is rejected (tool_result nudge, no final)",
      outcome.final is None and outcome.tool_result is not None and "was rejected" in outcome.tool_result,
      outcome.tool_result)

grounded_view = {
    "heading": "Critical Issues",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Login page crashes on submit", "note": "#101 — bug, critical"},
            ],
        }
    ],
}
outcome2 = dispatch("render_view", grounded_view, "tcY", fake_prior_messages, True)
check("test4b: grounded render_view is accepted (final set)", outcome2.final is not None, outcome2.tool_result)


# ---------------------------------------------------------------------------
print("\n=== Test 5: placeholder-label guardrail unit test ===")
placeholder_view = {
    "heading": "Issues",
    "components": [
        {
            "type": "list",
            "rows": [
                {"name": "Issue 1", "note": "Label"},
            ],
        }
    ],
}
outcome3 = dispatch("render_view", placeholder_view, "tcZ", fake_prior_messages, True)
check("test5: placeholder labels ('Issue 1'/'Label') rejected", outcome3.final is None and outcome3.tool_result is not None, outcome3.tool_result)


# ---------------------------------------------------------------------------
print(f"\n\n=== SUMMARY: {len(PASS)} passed, {len(FAIL)} failed ===")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print(f"  - {f}")
    sys.exit(1)
else:
    print("ALL PASSED")
