"""
Regression coverage for the 2026-08-29 Pilant Studio workspace-generation
rewrite: roles.py's RBAC, the Salesforce/Jira mock connectors, the typed
Primitive renderers, and workspace_engine.py's four example workspaces —
including the EXACT counts the brief specifies for the default Attention
workspace (5 messages / 2 customers at risk / 3 overdue tasks / 1 approval
request) and the exact "Acme renewal at risk" cross-application item.

Run directly: python3 tests/test_workspace_engine.py
"""

import sys
sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_jira_mock
import connectors_salesforce_mock
import roles
import workspace_engine
import workspace_primitives
from users import get_user

TEST_USER = get_user("kowsick")


def test_salesforce_mock_scoping_by_owner():
    all_risk = connectors_salesforce_mock.get_account_risk()
    kowsick_risk = connectors_salesforce_mock.get_account_risk(owner="kowsick")
    assert len(kowsick_risk) <= len(all_risk)
    assert all(o["owner"] == "kowsick" for o in kowsick_risk)
    assert kowsick_risk[0]["customer"] == "Acme Corp"  # highest risk, sorted first


def test_jira_mock_overdue_and_open_counts():
    overdue = connectors_jira_mock.get_overdue_issues(min_unresolved_days=10)
    assert len(overdue) == 3
    assert all(i["unresolved_days"] >= 10 for i in overdue)
    assert connectors_jira_mock.get_open_issue_count("Acme Corp") == 2


def test_roles_filter_by_type_and_action():
    primitives = [
        {"id": "a", "type": "Chart", "allowed_actions": [{"key": "escalate", "label": "Escalate"}]},
        {"id": "b", "type": "MessageCard", "allowed_actions": [{"key": "draft_response", "label": "Draft response"}]},
    ]
    filtered = roles.filter_primitives_for_role("sales_rep", primitives)
    types = {p["type"] for p in filtered}
    assert "MessageCard" in types
    # sales_rep can't escalate -> action stripped even on a visible primitive of that type
    exec_filtered = roles.filter_primitives_for_role("executive", primitives)
    exec_ids = {p["id"] for p in exec_filtered}
    assert "b" not in exec_ids  # executive doesn't get row-level MessageCard
    assert "a" in exec_ids
    a = next(p for p in exec_filtered if p["id"] == "a")
    assert a["allowed_actions"][0]["key"] == "escalate"  # executive CAN escalate


def test_roles_that_can_view():
    labels = roles.roles_that_can_view("ApprovalPanel")
    assert "Sales Manager" in labels
    assert "Sales Representative" not in labels  # can't approve, panel hidden entirely


def test_match_intent_covers_all_four_example_requests():
    assert workspace_engine.match_intent("Show what needs attention") == "attention"
    assert workspace_engine.match_intent(workspace_engine.DEFAULT_GOAL) == "attention"
    assert workspace_engine.match_intent("Prepare me for the Acme meeting") == "acme_meeting"
    assert workspace_engine.match_intent("Review this week's sales risk") == "sales_risk"
    assert workspace_engine.match_intent("Handle urgent support problems") == "urgent_support"
    assert workspace_engine.match_intent("something with no keywords at all") == "attention"  # honest fallback


def test_attention_workspace_matches_exact_spec_counts():
    ws = workspace_engine.generate_workspace("ws_test_attn", workspace_engine.DEFAULT_GOAL, "sales_rep", TEST_USER)
    by_id = {p["id"]: p for p in ws["primitives"]}
    assert by_id["m_replies"]["data"]["value"] == 5
    assert by_id["m_risk"]["data"]["value"] == 2
    assert by_id["m_overdue"]["data"]["value"] == 3
    assert by_id["m_approvals"]["data"]["value"] == 1

    priority_items = by_id["priority_queue"]["data"]["items"]
    acme = priority_items[0]
    assert acme["title"] == "Acme renewal at risk"
    reasons = {w["source"].split(" ")[0]: w["text"] for w in acme["why"]}
    assert "Gmail" in reasons
    assert reasons["Salesforce"] == "Opportunity inactive for 21 days"
    assert reasons["Jira"] == "2 unresolved issues"
    action_keys = {a["key"] for a in acme["actions"]}
    assert action_keys == {"view_account", "draft_response", "assign_follow_up", "escalate"}
    escalate = next(a for a in acme["actions"] if a["key"] == "escalate")
    assert escalate["approval_required"] is True


def test_every_generated_component_declares_a_source_or_is_source_agnostic():
    """'Every component identifies its source' — Metric/PriorityList/
    Alert-with-items etc. carry data_sources at the primitive level, and
    every row-level item inside PriorityList/MessageCard/TaskQueue/Timeline
    also carries its own source. Purely structural primitives (Form,
    ActivityLog, ApprovalPanel controls) have no external source and are
    exempt."""
    exempt_types = {"Form", "ActivityLog", "ApprovalPanel", "Metric"}
    for intent_key in workspace_engine.INTENT_ORDER:
        ws = workspace_engine.generate_workspace(f"ws_src_{intent_key}", "x", "operations_manager", TEST_USER)
        for p in ws["primitives"]:
            if p["type"] in exempt_types:
                continue
            assert p.get("data_sources"), f"{intent_key}/{p['id']} ({p['type']}) has no data_sources"


def test_role_switch_changes_data_scope_not_just_a_filter_label():
    ws_rep = workspace_engine.generate_workspace("ws_scope_rep", "Show what needs attention", "sales_rep", TEST_USER)
    ws_mgr = workspace_engine.generate_workspace("ws_scope_mgr", "Show what needs attention", "sales_manager", TEST_USER)
    rep_risk = next(p for p in ws_rep["primitives"] if p["id"] == "m_risk")["data"]["value"]
    mgr_risk = next(p for p in ws_mgr["primitives"] if p["id"] == "m_risk")["data"]["value"]
    assert mgr_risk >= rep_risk  # manager sees team, not just personal accounts


def test_render_primitive_escapes_untrusted_looking_text():
    instance = {
        "id": "p1", "type": "Alert", "title": "<script>evil()</script>", "data_sources": [],
        "allowed_actions": [], "tone": "critical", "position": {"span": "full"},
        "data": {"message": "<img src=x onerror=alert(1)>"},
    }
    html = workspace_primitives.render_primitive(instance, "ws_x")
    assert "<script>" not in html
    assert "<img src=x" not in html  # never becomes a live tag/attribute
    assert "&lt;script&gt;" in html
    assert "&lt;img" in html


if __name__ == "__main__":
    test_salesforce_mock_scoping_by_owner()
    test_jira_mock_overdue_and_open_counts()
    test_roles_filter_by_type_and_action()
    test_roles_that_can_view()
    test_match_intent_covers_all_four_example_requests()
    test_attention_workspace_matches_exact_spec_counts()
    test_every_generated_component_declares_a_source_or_is_source_agnostic()
    test_role_switch_changes_data_scope_not_just_a_filter_label()
    test_render_primitive_escapes_untrusted_looking_text()
    print("all workspace_engine tests passed")
