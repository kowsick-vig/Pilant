"""
Verifies the 2026-08-26 merge of gmail_site.py's full interactive Gmail
interface (compose/delete/star/forward/reply) into studio.py as a Blueprint
— the user's explicit request after finding those actions weren't reachable
from Studio's chat page (studio.py, port 5008) they were actually running,
only from the separate standalone gmail_site.py app (port 5007) that was
never linked from anywhere in Studio.

Checks:
  1. No route collisions between studio.py's own routes and gmail_bp's.
  2. The merged app actually serves the real interactive inbox at /inbox,
     /compose, /message/<id>, etc. — same Flask process, same port as
     Studio's chat.
  3. Studio is aware of it: the sidebar (every page), the Integrations
     Gmail card, and a Gmail workflow's first chat bubbles all link to it.
  4. Auth is genuinely shared — one login covers both; logging out clears
     access to the merged inbox routes too.

Run: python3 /tmp/test_studio_gmail_merge.py
"""
import sys, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import studio

studio.app.config["TESTING"] = True
studio.app.secret_key = "test-secret"

# --- 1. No route collisions -------------------------------------------------
rules = list(studio.app.url_map.iter_rules())
paths = [r.rule for r in rules]
assert len(paths) == len(set(paths)), f"duplicate route paths registered: {paths}"
mail_endpoints = {r.rule: r.endpoint for r in rules if r.endpoint.startswith("mail.")}
assert mail_endpoints, "gmail_bp routes never got registered onto studio.app"
for expected in ["/inbox", "/compose", "/message/<message_id>", "/message/<message_id>/delete",
                  "/message/<message_id>/star", "/message/<message_id>/forward"]:
    assert expected in mail_endpoints, f"missing merged route: {expected}"
print("PASSED — 1: gmail_bp's routes are registered on studio.app with no path collisions.\n")

fake_user = {"username": "demo", "name": "Kowsick"}

with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", return_value=[
         {"id": "m1", "from": "a@x.com", "to": "", "subject": "hi", "date": "", "snippet": "s",
          "unread": True, "starred": True, "important": False},
     ]):
    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"

    # --- 2. The merged app really serves the interactive inbox -------------
    r = client.get("/inbox")
    assert r.status_code == 200, r.status_code
    html = r.get_data(as_text=True)
    assert "Compose" in html
    assert 'action="/message/m1/star"' in html and 'action="/message/m1/delete"' in html
    assert 'class="icon-btn star active"' in html  # reflects the mocked starred=True

    r = client.get("/compose")
    assert r.status_code == 200 and 'name="to"' in r.get_data(as_text=True)
    print("PASSED — 2: /inbox and /compose serve the real interactive Gmail interface\n"
          "         (compose button, working star/delete forms) through studio.app.\n")

    # --- 3a. Sidebar link on every page (chat page + integrations page) ----
    r = client.get("/studio")
    assert r.status_code == 200
    assert 'href="/inbox"' in r.get_data(as_text=True) and "Full inbox" in r.get_data(as_text=True)

    r = client.get("/integrations")
    assert r.status_code == 200
    assert "Open full inbox" in r.get_data(as_text=True)
    print("PASSED — 3a: 'Full inbox' is linked from Studio's sidebar and the Integrations\n"
          "         page's Gmail card.\n")

    # --- 3b. A brand-new Gmail workflow's chat mentions it directly --------
    wf = studio._create_workflow(connector="gmail")
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]
    r = client.get(f'/studio/open/{wf["id"]}', follow_redirects=True)
    assert r.status_code == 200, r.status_code
    html = r.get_data(as_text=True)
    assert 'href="/inbox"' in html and "full inbox" in html.lower(), (
        "a brand-new Gmail workflow's welcome chat should point at the real inbox"
    )
    print("PASSED — 3b: opening a fresh Gmail workflow's chat explicitly links to the full\n"
          "         inbox for real actions (reply/compose/delete/star/forward).\n")

    # --- 4. Shared auth: logout revokes access to the merged inbox too -----
    r = client.get("/logout")
    assert r.status_code == 302
    r = client.get("/inbox")
    assert r.status_code == 302 and "/login" in r.headers["Location"], (
        "an unauthenticated request to a merged gmail_bp route should redirect to "
        "Studio's own /login, proving the session really is shared, not separate"
    )

print("PASSED — 4: logging out of Studio also revokes access to the merged inbox routes —\n"
      "         one real shared session, not two separate logins pretending to be one.\n")

print("ALL STUDIO+GMAIL MERGE TESTS PASSED")
