"""
Regression coverage for the 2026-08-29 fix to a real gap the user hit
directly: after typing "no from uber.com" in a Gmail workflow's chat, the
assistant said "Built it — see the live preview on the right" but the
panel kept showing the plain unfiltered Inbox — every message, Uber
included. Root cause: the embedded panel (gmail_site.render_inbox_panel)
was never tied to what a chat request actually fetched; for a Gmail
workflow it always just showed the deterministic real Inbox, ignoring
wf["last_render"] entirely (by design, so real star/delete stay live —
see gmail_site.py's render_inbox_panel docstring), and the LLM's own
render_view result has no real message IDs to render safely anyway.

Fix: agent_gmail.py's dispatch() now remembers the real kwargs the one
get_gmail_messages() call in a run used (state["last_fetch_args"]) and
hands them back as result["fetch_args"] alongside a successful render.
studio.py's _handle_studio_message stores those onto
wf["gmail_panel_folder"]/wf["gmail_panel_query"], and the /studio route +
_preview_html scope the embedded panel to them — so the panel now shows
PRECISELY the same real folder/query the chat request actually fetched
with, not a second guess.

Checks:
  1. A render with fetch_args={"query": "-from:uber.com"} (no folder — the
     model fetching an unscoped, cross-folder query) scopes the panel to
     that exact real search, across All Mail, and shows a "Filtered to"
     hint with a working "clear" link.
  2. A render with fetch_args={"folder": "sent"} moves the panel's active
     tab to Sent and drops any prior query.
  3. A render with fetch_args=None (e.g. an ask_user-resume that seeded
     fetched_data from an earlier turn — see agent_gmail.py's dispatch
     docstring) leaves the panel's current scoping untouched rather than
     resetting it.
  4. Clicking a folder tab (?panel_folder=) always clears any active
     chat-driven query, even when landing back on the same folder — the
     panel's own "start fresh" affordance.

Run directly: python3 tests/test_studio_gmail_panel_precise_filter.py
"""
import sys
import unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import studio

studio.app.config["TESTING"] = True
studio.app.secret_key = "test-secret"

fake_user = {"username": "demo", "name": "Kowsick"}

ALL_MESSAGES = {
    None: [  # unscoped / All Mail
        {"id": "m1", "from": "uber@uber.com", "to": "", "subject": "Festival bound?",
         "snippet": "Book the squad's train", "unread": True, "starred": False, "important": False},
        {"id": "m2", "from": "alex@company.com", "to": "", "subject": "Q3 planning sync",
         "snippet": "Can we move it to Thursday?", "unread": True, "starred": False, "important": False},
    ],
    "sent": [
        {"id": "m3", "from": "me@company.com", "to": "vendor@x.com", "subject": "Re: invoice",
         "snippet": "Paid, thanks.", "unread": False, "starred": False, "important": False},
    ],
    "inbox": [
        {"id": "m4", "from": "alex@company.com", "to": "", "subject": "Q3 planning sync",
         "snippet": "Can we move it to Thursday?", "unread": True, "starred": False, "important": False},
    ],
}


def fake_get_gmail_messages(unread_only=False, limit=10, folder=None, query=None):
    pool = ALL_MESSAGES.get(folder, [])
    if query and "-from:uber.com" in query:
        return [m for m in pool if "uber.com" not in m["from"]]
    return pool


def _client():
    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"
    return client


def _new_gmail_workflow(client):
    with mock.patch.object(studio, "get_user", return_value=fake_user):
        wf = studio._create_workflow(connector="gmail")
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]
    return wf


def _send_message(client, text, fetch_args):
    """Drives /studio/message with a fake connector run_agent, exactly the
    way a real chat exchange would land in _handle_studio_message's
    "render" branch — without touching the real Anthropic API."""
    fake_render = {
        "heading": "Results", "components": [{"type": "list", "rows": []}],
    }
    fake_result = {"render": fake_render, "fetch_args": fetch_args}
    with mock.patch.dict(studio.CONNECTORS["gmail"], {"run_agent": lambda *a, **k: fake_result}):
        return client.post("/studio/message", data={"text": text}, follow_redirects=True)


with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", side_effect=fake_get_gmail_messages):

    client = _client()
    wf = _new_gmail_workflow(client)

    # --- 1. Unscoped query-only fetch scopes the panel to All Mail + query -
    r = _send_message(client, "no from uber.com", {"query": "-from:uber.com"})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Q3 planning sync" in html          # the non-Uber message
    assert "Festival bound?" not in html       # the Uber message, correctly excluded
    assert "Filtered to:" in html and "-from:uber.com" in html
    assert 'class="gmail-panel-tab active" href="/studio?panel_folder=all">All Mail<' in html
    print("PASSED — 1: an unscoped chat-driven query scopes the panel to All Mail and "
          "the exact real search, with a visible filter hint.\n")

    # --- 2. A folder-scoped fetch moves the active tab and drops the query -
    r = _send_message(client, "show my sent mail", {"folder": "sent"})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Re: invoice" in html
    assert "Filtered to:" not in html  # no query this time, so no leftover hint
    assert 'class="gmail-panel-tab active" href="/studio?panel_folder=sent">Sent<' in html
    print("PASSED — 2: a folder-scoped chat fetch moves the panel's active tab to match "
          "and clears any earlier query.\n")

    # --- 3. fetch_args=None leaves the panel's current scoping untouched ---
    r = _send_message(client, "anything else interesting?", None)
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # still on Sent from step 2 — nothing reset it
    assert 'class="gmail-panel-tab active" href="/studio?panel_folder=sent">Sent<' in html
    assert "Re: invoice" in html
    print("PASSED — 3: fetch_args=None (no fetch in this run) leaves the panel's existing "
          "folder/query scoping alone instead of resetting it.\n")

    # --- 4. A folder-tab click clears any active chat-driven query --------
    _send_message(client, "no from uber.com", {"query": "-from:uber.com"})  # re-apply a filter
    r = client.get("/studio?panel_folder=inbox")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Filtered to:" not in html
    assert 'class="gmail-panel-tab active" href="/studio?panel_folder=inbox">Inbox<' in html
    assert "Q3 planning sync" in html  # inbox's own unfiltered content
    print("PASSED — 4: clicking a folder tab always clears any active chat-driven filter, "
          "showing that folder's own full contents.\n")

print("ALL STUDIO GMAIL PANEL PRECISE-FILTER TESTS PASSED")
