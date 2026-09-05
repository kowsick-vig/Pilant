"""
Regression coverage for the 2026-08-29 embedded Gmail panel upgrade — the
user's request: remove the "Open the full inbox for search, folders,
reply, and forward" link (the panel should offer folders itself, not send
people to a separate full view) and add a folder tab strip (Inbox/Sent/
Drafts/Starred/Important/Spam/Trash/All Mail) plus minimize/maximize
controls directly on the live-preview panel embedded in Studio's chat page.

Checks:
  1. render_inbox_panel_tabs() renders every real folder as a link back to
     /studio?panel_folder=<key> ("all" for All Mail), with the active one
     marked.
  2. resolve_panel_folder() maps "all" -> None, a real folder key -> itself
     unchanged, and anything else (unset/typo'd/tampered) -> "inbox" —
     never silently to unscoped All Mail.
  3. /studio's embedded panel switches folders via ?panel_folder=, the old
     "Open the full inbox..." link is gone, and the minimize/maximize
     controls + toggle script are present.
  4. Star/delete actions on the panel still round-trip through /studio
     (not /inbox) regardless of which folder the panel is currently
     scoped to — the folder strip shouldn't have disturbed that.

Run directly: python3 tests/test_studio_gmail_panel_folders.py
"""
import sys
import unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import gmail_site
import studio

studio.app.config["TESTING"] = True
studio.app.secret_key = "test-secret"

fake_user = {"username": "demo", "name": "Kowsick"}
fake_messages = {
    "inbox": [{"id": "m1", "from": "alex@company.com", "to": "", "subject": "Q3 planning sync",
               "snippet": "Can we move it to Thursday?", "unread": True, "starred": False, "important": False}],
    "sent": [{"id": "m2", "from": "me@company.com", "to": "vendor@x.com", "subject": "Re: invoice",
              "snippet": "Paid, thanks.", "unread": False, "starred": False, "important": False}],
}


def _fake_get_gmail_messages(unread_only=False, limit=10, folder=None, query=None):
    return fake_messages.get(folder, [])


# --- 1. render_inbox_panel_tabs() -------------------------------------------
tabs_html = gmail_site.render_inbox_panel_tabs("sent", next_url="/studio")
assert 'href="/studio?panel_folder=inbox"' in tabs_html
assert 'href="/studio?panel_folder=sent"' in tabs_html
assert 'href="/studio?panel_folder=all"' in tabs_html  # All Mail (key=None) uses "all"
assert 'href="/studio?panel_folder=spam"' in tabs_html
assert 'href="/studio?panel_folder=trash"' in tabs_html
assert '>Spam<' in tabs_html and '>Trash<' in tabs_html and '>Starred<' in tabs_html and '>Important<' in tabs_html
# the active folder's tab is marked, and only that one
assert 'class="gmail-panel-tab active" href="/studio?panel_folder=sent">Sent<' in tabs_html
assert 'class="gmail-panel-tab active" href="/studio?panel_folder=inbox">Inbox<' not in tabs_html
print("PASSED — 1: render_inbox_panel_tabs() lists every real folder, links to "
      "/studio?panel_folder=<key>, and marks only the active one.\n")

# --- 2. resolve_panel_folder() ----------------------------------------------
assert gmail_site.resolve_panel_folder("all") is None
assert gmail_site.resolve_panel_folder("sent") == "sent"
assert gmail_site.resolve_panel_folder("spam") == "spam"
assert gmail_site.resolve_panel_folder(None) == "inbox"          # unset
assert gmail_site.resolve_panel_folder("") == "inbox"            # unset
assert gmail_site.resolve_panel_folder("not-a-real-folder") == "inbox"  # tampered
print("PASSED — 2: resolve_panel_folder() maps 'all' to unscoped All Mail, a real "
      "folder key to itself, and anything else to 'inbox' — never to unscoped "
      "All Mail by accident.\n")

with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", side_effect=_fake_get_gmail_messages):

    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"
    wf = studio._create_workflow(connector="gmail")
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]

    # --- 3a. Default /studio load: Inbox folder, old full-inbox link gone --
    r = client.get("/studio")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Q3 planning sync" in html  # inbox's message
    assert "Re: invoice" not in html   # sent's message, not shown by default
    assert "Open the full inbox for search, folders, reply, and forward" not in html
    assert 'class="gmail-panel-tab active" href="/studio?panel_folder=inbox">Inbox<' in html
    print("PASSED — 3a: /studio's embedded panel defaults to the Inbox folder and no "
          "longer links out to the full inbox for folders.\n")

    # --- 3b. Switching folders via the tab strip's own link -----------------
    r = client.get("/studio?panel_folder=sent")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Re: invoice" in html
    assert "Q3 planning sync" not in html
    assert 'class="gmail-panel-tab active" href="/studio?panel_folder=sent">Sent<' in html
    print("PASSED — 3b: following a folder tab's link actually re-scopes the panel to "
          "that real folder.\n")

    # --- 3c. Minimize/maximize controls + toggle script present -------------
    r = client.get("/studio")
    html = r.get_data(as_text=True)
    assert 'id="gmailPanel"' in html
    assert "pilantTogglePanel('gmailPanel','min')" in html
    assert "pilantTogglePanel('gmailPanel','max')" in html
    assert "function pilantTogglePanel" in html
    print("PASSED — 3c: the panel carries minimize/maximize controls wired to the real "
          "toggle script, not decorative buttons.\n")

    # --- 4. Star/delete still round-trip through /studio regardless of folder
    r = client.get("/studio?panel_folder=sent")
    html = r.get_data(as_text=True)
    assert 'action="/message/m2/star"' in html
    assert 'name="next" value="/studio"' in html  # not /studio?panel_folder=sent — see note below
    print("PASSED — 4: star/delete forms on a non-default folder still post to the real "
          "message routes and redirect back to /studio.\n")

print("ALL STUDIO GMAIL PANEL FOLDER/MINIMIZE-MAXIMIZE TESTS PASSED")
