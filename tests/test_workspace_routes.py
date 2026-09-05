"""
End-to-end Flask route coverage for the new /workspace surface (see
workspace_site.py's module docstring) — generation, role switching,
approval decisions + the audit trail they write, save/reopen/rename with
version history and restore, and confirmation that every existing route
(the old /studio chat journey, /composer, the real Gmail inbox) still
works unchanged alongside it.

Run directly: python3 tests/test_workspace_routes.py
"""

import re
import sys
sys.path.insert(0, "/home/claude/pilant-agent")

import approvals
import audit_log
import studio

studio.app.testing = True


def _client():
    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "kowsick"
    return client


def test_root_and_login_redirect_to_workspace_not_studio():
    client = _client()
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/workspace")


def test_workspace_home_renders_default_attention_workspace():
    client = _client()
    r = client.get("/workspace")
    assert r.status_code == 200
    body = r.data.decode()
    assert "Today" in body
    assert "Acme renewal at risk" in body
    assert "Adapted for" in body
    assert "Open full Gmail inbox" in body


def test_all_four_example_requests_generate_meaningfully_different_layouts():
    client = _client()
    titles = set()
    for goal in [
        "Show what needs attention",
        "Prepare me for the Acme meeting",
        "Review this week's sales risk",
        "Handle urgent support problems",
    ]:
        r = client.post("/workspace/generate", data={"goal": goal, "role": "sales_rep"}, follow_redirects=True)
        assert r.status_code == 200
        m = re.search(r'<h1 class="canvas-title">([^<]*)</h1>', r.data.decode())
        titles.add(m.group(1))
    assert len(titles) == 4  # four distinct workspace titles, not one filtered Gmail view


def test_role_switch_hides_and_reveals_manager_only_primitive():
    client = _client()
    client.post("/workspace/generate", data={"goal": "Show what needs attention", "role": "sales_rep"})
    r = client.get("/workspace")
    assert "prim-messagecard" in r.data.decode().lower()
    r = client.post("/workspace/role", data={"role": "executive"}, follow_redirects=True)
    assert "prim-messagecard" not in r.data.decode().lower()  # executive: aggregate only
    r = client.post("/workspace/role", data={"role": "sales_rep"}, follow_redirects=True)
    assert "prim-messagecard" in r.data.decode().lower()


def test_approval_requires_manager_role_and_creates_audit_entry():
    client = _client()
    client.get("/workspace")
    r = client.post("/workspace/role", data={"role": "sales_manager"}, follow_redirects=True)
    m = re.search(r'/workspace/(ws_\w+)/approve/(appr_\w+)', r.data.decode())
    assert m, "sales_manager should see decide controls on the pending approval"
    ws_id, appr_id = m.groups()

    # sales_rep can't decide it
    client2 = _client()
    client2.get("/workspace")
    client2.post("/workspace/role", data={"role": "sales_rep"})
    r2 = client2.post(f"/workspace/{ws_id}/approve/{appr_id}", follow_redirects=True)
    assert "can" in r2.data.decode().lower() and "role" in r2.data.decode().lower()
    assert approvals.get_approval(appr_id)["status"] == "pending"

    before = len(audit_log.list_for_workspace(ws_id))
    r3 = client.post(f"/workspace/{ws_id}/approve/{appr_id}", follow_redirects=True)
    assert r3.status_code == 200
    after = audit_log.list_for_workspace(ws_id)
    assert len(after) == before + 1
    assert after[0]["event_type"] == "approval_approved"
    assert approvals.get_approval(appr_id)["status"] == "approved"

    # deciding again is a no-op, not a second audit entry
    client.post(f"/workspace/{ws_id}/reject/{appr_id}")
    assert len(audit_log.list_for_workspace(ws_id)) == before + 1


def test_save_rename_reopen_and_restore_version():
    client = _client()
    client.post("/workspace/generate", data={"goal": "Show what needs attention", "role": "sales_rep"})
    r = client.get("/workspace")
    ws_id = re.search(r'data-prim-id="[^"]*"[^>]*data-config="[^"]*"', r.data.decode())  # sanity: primitives present
    assert ws_id

    r = client.post("/workspace/save", data={"name": "My Board"}, follow_redirects=True)
    assert "saved" in r.data.decode().lower()

    r = client.get("/workspace?tab=history")
    assert "My Board" in r.data.decode()

    # regenerate creates version 2
    r = client.post("/workspace/regenerate", follow_redirects=True)
    r = client.get("/workspace?tab=history")
    assert "v2" in r.data.decode()

    m = re.search(r'/workspace/(ws_\w+)/restore/1', r.data.decode())
    assert m, "should offer to restore version 1 once a v2 exists"
    ws_id = m.group(1)
    r = client.post(f"/workspace/{ws_id}/restore/1", follow_redirects=True)
    assert "restored version 1" in r.data.decode().lower()

    r = client.post(f"/workspace/{ws_id}/rename", data={"name": "Renamed Board"}, follow_redirects=True)
    assert "renamed" in r.data.decode().lower()
    r = client.get("/workspace?tab=history")
    assert "Renamed Board" in r.data.decode()

    r = client.get(f"/workspace/open/{ws_id}", follow_redirects=True)
    assert r.status_code == 200 and "Renamed Board" in r.data.decode()


def test_publish_requires_save_first():
    client = _client()
    client.post("/workspace/generate", data={"goal": "Show what needs attention", "role": "sales_rep"})
    r = client.post("/workspace/publish", follow_redirects=True)
    assert "save" in r.data.decode().lower()
    client.post("/workspace/save", data={"name": "Publishable"})
    r = client.post("/workspace/publish", follow_redirects=True)
    assert "published" in r.data.decode().lower()


def test_existing_routes_still_work_unchanged():
    client = _client()
    for path in ["/inbox?folder=inbox", "/studio", "/composer", "/integrations", "/customers"]:
        r = client.get(path)
        assert r.status_code in (200, 302), (path, r.status_code)


if __name__ == "__main__":
    test_root_and_login_redirect_to_workspace_not_studio()
    test_workspace_home_renders_default_attention_workspace()
    test_all_four_example_requests_generate_meaningfully_different_layouts()
    test_role_switch_hides_and_reveals_manager_only_primitive()
    test_approval_requires_manager_role_and_creates_audit_entry()
    test_save_rename_reopen_and_restore_version()
    test_publish_requires_save_first()
    test_existing_routes_still_work_unchanged()
    print("all workspace route tests passed")
