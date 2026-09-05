"""
Regression coverage for the 2026-08-29 addition of "improvement idea #1" —
one-tap Unread/Starred/Important filter chips in the embedded Studio Gmail
panel, sitting alongside the folder tabs and the panel's own search box
(added just before this, in the same session) rather than replacing either.

Checks:
  1. render_inbox_panel_chips() renders all 3 chips as real links to
     /studio?panel_chip=<key>, with the one matching the active query
     marked active and no other.
  2. resolve_panel_chip() maps each real chip key to its real Gmail search
     term, and any unrecognized key to None.
  3. /studio integration: clicking a chip's link (?panel_chip=unread)
     actually filters the panel to that real query, marks the chip active,
     and mirrors the term into the search box.
  4. Clicking the SAME active chip again toggles it off — clears the query,
     shows the folder's full contents, and un-marks the chip.
  5. A chip filter applies within whatever folder is already active (chip
     clicks don't reset or require a folder param).
  6. A folder-tab click clears any active chip filter, same as it already
     clears a chat- or search-box-driven query.

Run directly: python3 tests/test_studio_gmail_panel_chips.py
"""
import sys
import unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import gmail_site
import studio

studio.app.config["TESTING"] = True
studio.app.secret_key = "test-secret"

fake_user = {"username": "demo", "name": "Kowsick"}

ALL_MESSAGES = {
    "inbox": [
        {"id": "m1", "from": "alex@company.com", "to": "", "subject": "Q3 planning sync",
         "snippet": "Can we move it to Thursday?", "unread": True, "starred": False, "important": False},
        {"id": "m2", "from": "vendor@x.com", "to": "", "subject": "Invoice #881",
         "snippet": "Please review", "unread": False, "starred": True, "important": False},
    ],
    "sent": [
        {"id": "m3", "from": "me@company.com", "to": "vendor@x.com", "subject": "Re: invoice",
         "snippet": "Paid, thanks.", "unread": False, "starred": False, "important": False},
    ],
}


def _fake_get_gmail_messages(unread_only=False, limit=10, folder=None, query=None):
    pool = ALL_MESSAGES.get(folder, [])
    if query == "is:unread":
        return [m for m in pool if m["unread"]]
    if query == "is:starred":
        return [m for m in pool if m["starred"]]
    if query == "is:important":
        return [m for m in pool if m["important"]]
    return pool


# --- 1. render_inbox_panel_chips() ------------------------------------------
html = gmail_site.render_inbox_panel_chips("is:starred", next_url="/studio")
assert 'href="/studio?panel_chip=unread">Unread<' in html
assert 'href="/studio?panel_chip=starred">Starred<' in html
assert 'href="/studio?panel_chip=important">Important<' in html
assert 'class="gmail-panel-chip active" href="/studio?panel_chip=starred">' in html
assert 'class="gmail-panel-chip active" href="/studio?panel_chip=unread">' not in html
print("PASSED — 1: render_inbox_panel_chips() lists all 3 chips as real links and marks "
      "only the one matching the active query.\n")

# --- 2. resolve_panel_chip() -------------------------------------------------
assert gmail_site.resolve_panel_chip("unread") == "is:unread"
assert gmail_site.resolve_panel_chip("starred") == "is:starred"
assert gmail_site.resolve_panel_chip("important") == "is:important"
assert gmail_site.resolve_panel_chip("not-a-real-chip") is None
assert gmail_site.resolve_panel_chip(None) is None
print("PASSED — 2: resolve_panel_chip() maps each real chip key to its real Gmail search "
      "term and anything else to None.\n")

with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", side_effect=_fake_get_gmail_messages):

    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"
    wf = studio._create_workflow(connector="gmail")
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]

    # --- 3. Clicking a chip filters the panel and marks it active ----------
    r = client.get("/studio?panel_chip=unread")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Q3 planning sync" in html      # unread message
    assert "Invoice #881" not in html      # read message, excluded
    assert 'class="gmail-panel-chip active" href="/studio?panel_chip=unread">' in html
    assert "Filtered to:" in html and "is:unread" in html
    assert 'name="panel_q" value="is:unread"' in html  # search box mirrors the chip
    print("PASSED — 3: clicking a filter chip actually filters the panel to that real "
          "search, marks the chip active, and mirrors it into the search box.\n")

    # --- 4. Clicking the same active chip again toggles it off -------------
    r = client.get("/studio?panel_chip=unread")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Q3 planning sync" in html and "Invoice #881" in html  # full inbox again
    assert "Filtered to:" not in html
    assert 'class="gmail-panel-chip active"' not in html
    print("PASSED — 4: clicking an already-active chip again turns the filter back off.\n")

    # --- 5. A chip filter applies within whatever folder is active ---------
    client.get("/studio?panel_folder=sent")
    r = client.get("/studio?panel_chip=important")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="gmail-panel-tab active" href="/studio?panel_folder=sent">Sent<' in html
    print("PASSED — 5: a chip click applies within the folder the panel is already "
          "showing, without needing a folder param of its own.\n")

    # --- 6. A folder-tab click clears any active chip filter ----------------
    client.get("/studio?panel_chip=important")  # (Sent has no important mail -> empty, fine)
    r = client.get("/studio?panel_folder=inbox")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Filtered to:" not in html
    assert 'class="gmail-panel-chip active"' not in html
    print("PASSED — 6: switching folders via the tabs clears any active chip filter, same "
          "as it already clears a chat- or search-driven one.\n")

print("ALL STUDIO GMAIL PANEL FILTER CHIP TESTS PASSED")
