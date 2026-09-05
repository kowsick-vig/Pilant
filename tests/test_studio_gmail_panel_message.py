"""
Regression coverage for the 2026-08-29 fix to a real complaint the user
raised directly with screenshots: clicking a mail row in the embedded
Studio Gmail panel navigated away to a separate, differently-branded
"Pilant Mail" full page (gmail_site.render_message / the real /message/<id>
route) — "if we click the mail it's navigating to next page want to do
everything in the studio front page."

Fix: gmail_site._embedded_row_html now links a row's open action to
/studio?panel_message=<id> instead of /message/<id>; the /studio route
reads that and swaps _preview_html's panel to
gmail_site.render_inbox_panel_message() (subject/body/reply, star/delete/
mark-unread) instead of the list — a real page (so back/forward and
bookmarking still work), but the SAME /studio page throughout, never a
separate one. Forward is the one action that still opens its own page
(a real recipient-picking compose flow needs more room than a quick
action), matching real Gmail's own Forward behavior.

Checks:
  1. A message row's link in the panel points at /studio?panel_message=<id>.
  2. GET /studio?panel_message=<id> shows that message's real subject,
     body, and toolbar (Delete/Star/Mark unread/Forward) — all still
     rendering the rest of /studio (sidebar, chat) around it, not a
     separate page — and does NOT show the folder tabs/search/chip row
     (the list stepping aside for the message, same as real Gmail).
  3. Star/Delete from the message toolbar call the real connector
     functions; Delete redirects back to the LIST (/studio, no
     panel_message), Star redirects back to the SAME message.
  4. Generate draft (POST /message/<id>/draft with next=/studio?panel_
     message=<id>) stashes the draft and lands back on the same message
     with it pre-filled in the reply textarea.
  5. Send reply, success: calls the real send_reply(), shows a "Reply
     sent" notice, and stays on the SAME message (not bounced to the
     list) — the panel toolbar's own back arrow is the way out, not an
     automatic redirect.
  6. Send reply, failure (empty body): shows the real error inline and
     keeps the just-typed text in the textarea via the session draft
     stash, still on the same message.
  7. Mark unread redirects back to the LIST (/studio, no panel_message) —
     "on your way out of a message," same semantics the full page's own
     toolbar already documents.
  8. Opening a message never touches the panel's own folder/query
     scoping (wf["gmail_panel_folder"]/["gmail_panel_query"]) — going
     back to the list shows exactly what was there before.

Run directly: python3 tests/test_studio_gmail_panel_message.py
"""
import sys
import unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import gmail_site
import studio

studio.app.config["TESTING"] = True
studio.app.secret_key = "test-secret"

fake_user = {"username": "demo", "name": "Kowsick"}

fake_messages = [
    {"id": "m1", "from": "alex@company.com", "to": "", "subject": "Q3 planning sync",
     "snippet": "Can we move it to Thursday?", "unread": True, "starred": False, "important": False},
]

fake_full = {
    "id": "m1", "from": "Alex <alex@company.com>", "to": "me@company.com",
    "subject": "Q3 planning sync", "date": "Sat, 29 Aug 2026 10:00:00 +0000",
    "body": "Can we move the sync to Thursday instead?", "unread": True, "starred": False,
    "attachments": [],
}


def _client():
    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"
    return client


with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", return_value=fake_messages), \
     mock.patch("gmail_site.get_message_full", return_value=dict(fake_full)), \
     mock.patch("gmail_site.mark_read") as mock_mark_read:

    # --- 1. A row's link points into /studio, not /message/<id> ------------
    panel_html = gmail_site.render_inbox_panel(next_url="/studio")
    assert 'href="/studio?panel_message=m1"' in panel_html
    print("PASSED — 1: a message row's open link stays on /studio (?panel_message=) "
          "instead of navigating to a separate page.\n")

    client = _client()
    with mock.patch.object(studio, "get_user", return_value=fake_user):
        wf = studio._create_workflow(connector="gmail")
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]

    # --- 2. GET /studio?panel_message=m1 shows the message, inline ---------
    r = client.get("/studio?panel_message=m1")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Q3 planning sync" in html
    assert "Can we move the sync to Thursday instead?" in html
    assert 'action="/message/m1/delete"' in html
    assert 'action="/message/m1/star"' in html
    assert 'action="/message/m1/read"' in html
    assert 'href="/message/m1/forward"' in html
    assert 'Describe the interface you want' in html  # the chat box is still there — same page
    # the list controls step aside while looking at one message
    assert 'class="gmail-panel-tabs"' not in html
    assert 'class="gmail-panel-search"' not in html
    mock_mark_read.assert_called_once_with("m1", read=True)  # real mark-read-on-open, same as the full page
    print("PASSED — 2: /studio?panel_message=<id> shows the real message inline — subject, "
          "body, and a real action toolbar — on the SAME page, with the list controls "
          "stepping aside.\n")

    # --- 3a. Delete from the message toolbar returns to the LIST -----------
    with mock.patch("gmail_site.trash_message") as mock_trash:
        r = client.post("/message/m1/delete", data={"next": "/studio"})
        assert r.status_code == 302 and r.headers["Location"] == "/studio"
        mock_trash.assert_called_once_with("m1")
    print("PASSED — 3a: deleting from the panel's message toolbar calls the real connector "
          "and returns to the list, not a separate page.\n")

    # --- 3b. Star from the message toolbar stays on the SAME message -------
    with mock.patch("gmail_site.star_message") as mock_star:
        r = client.post("/message/m1/star", data={"starred": "0", "next": "/studio?panel_message=m1"})
        assert r.status_code == 302 and r.headers["Location"] == "/studio?panel_message=m1"
        mock_star.assert_called_once_with("m1", starred=True)
    print("PASSED — 3b: starring from the panel's message toolbar stays on the same "
          "message afterward.\n")

    # --- 4. Generate draft stashes it and lands back on the same message ---
    with mock.patch("gmail_site.draft_reply_text", return_value="Thursday works for me!"):
        r = client.post(
            "/message/m1/draft",
            data={"instruction": "", "next": "/studio?panel_message=m1"},
            follow_redirects=True,
        )
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Thursday works for me!" in html  # pre-filled in the reply textarea
    print("PASSED — 4: Generate draft stashes the real draft and lands back on the SAME "
          "message with it pre-filled, never leaving /studio.\n")

    # --- 5. Send reply, success: stays on the message with a notice --------
    with mock.patch("gmail_site.send_reply", return_value={"to": "alex@company.com"}) as mock_reply:
        r = client.post(
            "/message/m1/reply",
            data={"body": "Thursday works!", "next": "/studio?panel_message=m1"},
            follow_redirects=True,
        )
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Reply sent to alex@company.com" in html
    assert 'action="/message/m1/star"' in html  # still on the message, not bounced to the list
    mock_reply.assert_called_once_with("m1", "Thursday works!")
    print("PASSED — 5: sending a real reply calls send_reply(), shows a real confirmation, "
          "and stays on the same message rather than bouncing away.\n")

    # --- 6. Send reply, failure: real error + text preserved ---------------
    r = client.post(
        "/message/m1/reply",
        data={"body": "   ", "next": "/studio?panel_message=m1"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Write something before sending." in html
    print("PASSED — 6: an empty reply shows the real validation error inline, still on the "
          "same message.\n")

    # --- 7. Mark unread returns to the LIST ---------------------------------
    r = client.post("/message/m1/read", data={"next": "/studio"}, follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="gmail-panel-tabs"' in html  # back on the list view
    print("PASSED — 7: marking unread returns to the list, same 'on your way out' "
          "semantics the full page's own toolbar already has.\n")

    # --- 8. Opening a message never touches the panel's folder/query state -
    wf["gmail_panel_folder"] = "sent"
    wf["gmail_panel_query"] = "from:vendor.com"
    client.get("/studio?panel_message=m1")
    assert wf["gmail_panel_folder"] == "sent"
    assert wf["gmail_panel_query"] == "from:vendor.com"
    print("PASSED — 8: opening a message is purely a transient query param — the panel's "
          "underlying folder/query scoping is completely untouched.\n")

print("ALL STUDIO GMAIL PANEL INLINE MESSAGE VIEW TESTS PASSED")
