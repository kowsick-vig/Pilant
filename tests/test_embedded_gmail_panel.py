"""
Verifies the 2026-08-26 fix for the user's follow-up complaint after the
Studio+Gmail merge: "still can't control form this page, all I need control
from here and just want to extend the interface not a seperate full view
mode" — the chat's live-preview pane for a Gmail workflow used to be a
read-only skin (renderer.render_gmail_fragment) with a link out to /inbox
for any real action. It's now gmail_site.render_inbox_panel(): the same
deterministic get_gmail_messages() data /inbox itself shows, with real
star/delete forms embedded directly on /studio.

Checks:
  1. render_inbox_panel() emits real forms pointing at the real
     /message/<id>/star and /message/<id>/delete routes, with next=/studio
     so the action redirects back to Studio, not /inbox.
  2. /studio itself (not /inbox) embeds those same real forms when the
     active workflow is a Gmail workflow — before any chat message has
     even been sent, since this panel isn't driven by wf["last_render"].
  3. The embedded Compose link points at /compose?next=/studio, and
     actually sending mail through that flow redirects back to /studio.
  4. Star/delete submitted from the embedded panel's next=/studio really
     redirects back to /studio (not /inbox), proving next round-trips
     through star_message_route/delete_message_route correctly.

Run: python3 /tmp/test_embedded_gmail_panel.py
"""
import sys, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import studio
import gmail_site

studio.app.config["TESTING"] = True
studio.app.secret_key = "test-secret"

fake_user = {"username": "demo", "name": "Kowsick"}
fake_messages = [
    {"id": "m1", "from": "alex@company.com", "to": "", "subject": "Q3 planning sync",
     "snippet": "Can we move it to Thursday?", "unread": True, "starred": False, "important": False},
    {"id": "m2", "from": "billing@vendor.com", "to": "", "subject": "Invoice #4821 overdue",
     "snippet": "This invoice is now 12 days overdue.", "unread": False, "starred": True, "important": False},
]

with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", return_value=fake_messages):

    # --- 1. render_inbox_panel() emits real, working forms ------------------
    panel_html = gmail_site.render_inbox_panel(next_url="/studio")
    assert 'action="/message/m1/star"' in panel_html
    assert 'action="/message/m1/delete"' in panel_html
    assert 'action="/message/m2/star"' in panel_html and 'class="gmail-row-star gmail-row-star-btn active"' in panel_html
    assert 'name="next" value="/studio"' in panel_html
    # Added 2026-08-29: opening a message from the panel now stays on
    # /studio itself (?panel_message=<id>), not a real navigation out to
    # /message/<id> — see test_studio_gmail_panel_message.py for the full
    # inline-message-view coverage this change enables.
    assert 'href="/studio?panel_message=m1"' in panel_html
    assert 'href="/message/m1"' not in panel_html
    print("PASSED — 1: render_inbox_panel() emits real star/delete forms with real\n"
          "         message IDs and next=/studio, reflecting each message's real starred state,\n"
          "         and links a message open back to /studio itself, not a separate page.\n")

    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"

    # --- 2. /studio embeds the same real panel, even with no chat yet ------
    wf = studio._create_workflow(connector="gmail")
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]
    r = client.get("/studio")
    assert r.status_code == 200, r.status_code
    html = r.get_data(as_text=True)
    assert 'action="/message/m1/star"' in html, "the live embedded panel should render before any chat message"
    assert 'action="/message/m1/delete"' in html
    assert 'href="/compose?next=/studio"' in html
    print("PASSED — 2: /studio embeds the real working star/delete panel directly —\n"
          "         no chat message needed first, no detour through /inbox.\n")

    # --- 3. Compose linked from Studio carries next=/studio through send ---
    r = client.get("/compose?next=/studio")
    assert r.status_code == 200
    compose_html = r.get_data(as_text=True)
    assert 'name="next" value="/studio"' in compose_html
    assert 'Back to Studio' in compose_html

    with mock.patch("gmail_site.send_email", return_value={"to": "x@y.com"}):
        r = client.post("/compose", data={"to": "x@y.com", "subject": "hi", "body": "hey", "next": "/studio"})
        assert r.status_code == 302 and r.headers["Location"].endswith("/studio"), r.headers.get("Location")
    print("PASSED — 3: Composing from Studio's embedded Compose link returns to /studio\n"
          "         after sending, instead of always landing on /inbox.\n")

    # --- 4. Star/delete submitted from the embedded panel return to /studio -
    with mock.patch("gmail_site.star_message") as mock_star:
        r = client.post("/message/m1/star", data={"starred": "0", "next": "/studio"})
        assert r.status_code == 302 and r.headers["Location"].endswith("/studio")
        mock_star.assert_called_once_with("m1", starred=True)

    with mock.patch("gmail_site.trash_message") as mock_trash:
        r = client.post("/message/m1/delete", data={"next": "/studio"})
        assert r.status_code == 302 and r.headers["Location"].endswith("/studio")
        mock_trash.assert_called_once_with("m1")
    print("PASSED — 4: starring/deleting from the embedded panel calls the real connector\n"
          "         functions and redirects back to /studio, not /inbox.\n")

print("ALL EMBEDDED GMAIL PANEL TESTS PASSED")
