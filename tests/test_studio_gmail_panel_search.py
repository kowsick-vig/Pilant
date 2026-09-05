"""
Regression coverage for the 2026-08-29 addition of "improvement idea #2" —
a real search box inside the embedded Studio Gmail panel itself, alongside
(not instead of) the chat box, mirroring /inbox's own search box but scoped
to the panel (POST /studio/panel_search).

Checks:
  1. Submitting real Gmail search syntax (from:) is used verbatim, scopes
     the panel to All Mail (no folder given -> stays on whatever folder was
     active), and shows a "Filtered to" hint.
  2. Submitting plain English is translated to real Gmail search syntax via
     resolve_panel_query (mocked here to avoid a real model call) before
     being applied.
  3. Submitting an empty search clears any active filter and shows the
     folder's full unfiltered contents again.
  4. A folder-tab click after a panel search clears the search box's
     remembered text (gmail_panel_search_text), not just the query.
  5. The search box itself renders with the last-submitted raw text as its
     value, so what's typed doesn't disappear after submitting.

Run directly: python3 tests/test_studio_gmail_panel_search.py
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
        {"id": "m1", "from": "uber@uber.com", "to": "", "subject": "Festival bound?",
         "snippet": "Book the squad's train", "unread": True, "starred": False, "important": False},
        {"id": "m2", "from": "alex@company.com", "to": "", "subject": "Q3 planning sync",
         "snippet": "Can we move it to Thursday?", "unread": True, "starred": False, "important": False},
    ],
}


def _fake_get_gmail_messages(unread_only=False, limit=10, folder=None, query=None):
    pool = ALL_MESSAGES.get(folder, [])
    if query and "from:uber.com" in query and not query.startswith("-"):
        return [m for m in pool if "uber.com" in m["from"]]
    if query and "-from:uber.com" in query:
        return [m for m in pool if "uber.com" not in m["from"]]
    return pool


with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", side_effect=_fake_get_gmail_messages):

    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"
    wf = studio._create_workflow(connector="gmail")
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]

    # --- 1. Real Gmail syntax used verbatim ---------------------------------
    r = client.post("/studio/panel_search",
                     data={"panel_q": "from:uber.com", "panel_folder": "inbox"},
                     follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Festival bound?" in html
    assert "Q3 planning sync" not in html
    assert "Filtered to:" in html and "from:uber.com" in html
    assert 'name="panel_q" value="from:uber.com"' in html  # box remembers what was typed
    print("PASSED — 1: real Gmail search syntax typed into the panel's own search box is "
          "used verbatim and actually filters the panel.\n")

    # --- 2. Plain English gets translated -----------------------------------
    with mock.patch("gmail_site.translate_to_search_query", return_value="-from:uber.com"):
        r = client.post("/studio/panel_search",
                         data={"panel_q": "not from uber", "panel_folder": "inbox"},
                         follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Q3 planning sync" in html
    assert "Festival bound?" not in html
    assert "Filtered to:" in html and "-from:uber.com" in html
    print("PASSED — 2: plain English typed into the panel's search box is translated to "
          "real Gmail search syntax and applied.\n")

    # --- 3. Empty search clears the filter -----------------------------------
    r = client.post("/studio/panel_search",
                     data={"panel_q": "", "panel_folder": "inbox"},
                     follow_redirects=True)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Festival bound?" in html and "Q3 planning sync" in html
    assert "Filtered to:" not in html
    print("PASSED — 3: submitting an empty search clears the active filter, showing the "
          "folder's full contents again.\n")

    # --- 4. A folder-tab click clears the search box's remembered text ------
    client.post("/studio/panel_search", data={"panel_q": "from:uber.com", "panel_folder": "inbox"})
    r = client.get("/studio?panel_folder=inbox")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Filtered to:" not in html
    assert 'name="panel_q" value=""' in html
    print("PASSED — 4: clicking a folder tab clears the panel search box's remembered "
          "text, not just the resolved query.\n")

print("ALL STUDIO GMAIL PANEL SEARCH BOX TESTS PASSED")
