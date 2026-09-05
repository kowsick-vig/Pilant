"""
Regression coverage for the 2026-08-29 addition of "improvement idea #3" —
inline per-turn chat history results: each turn that successfully builds a
screen now carries a compact snapshot of what it actually found directly
under ITS OWN chat bubble (renderer.render_chat_inline_result), not just in
the single always-latest live-preview panel on the right. Scrolling back
through the chat should show a real history, not just whatever the panel
currently happens to display.

Checks:
  1. renderer.render_chat_inline_result() on a 'list' component: shows up
     to max_rows real rows and a "+N more" note when truncated; an empty
     view/component renders "" so studio.py never shows an empty card.
  2. renderer.render_chat_inline_result() on stat_grid/panel/suggestions
     components: compact label:value / title+fields / bullet renderings.
  3. /studio/message integration (non-Gmail connector, Slack): a successful
     build attaches its own inline result card under that turn's bubble.
  4. TWO separate builds in the same conversation both keep their own
     inline card in the transcript — the point of "history" — even though
     the live preview panel only ever shows the second (latest) one.
  5. /studio/message integration (Gmail connector): the inline card appears
     under a Gmail turn's bubble too, using the same real render_view JSON
     dispatch already produces (independent of the separate real-panel
     fetch_args mechanism tested elsewhere).
  6. A plain conversational reply (no render_view call) has no inline card.

Run directly: python3 tests/test_chat_inline_results.py
"""
import sys
import unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import renderer
import studio

studio.app.config["TESTING"] = True
studio.app.secret_key = "test-secret"

fake_user = {"username": "demo", "name": "Kowsick"}


# --- 1. render_chat_inline_result() — 'list' component ----------------------
view_many = {
    "heading": "5 open deals",
    "components": [{
        "type": "list",
        "rows": [
            {"name": f"Deal {i}", "note": f"$1{i}k"} for i in range(5)
        ],
    }],
}
html = renderer.render_chat_inline_result(view_many, max_rows=3)
assert "5 open deals" in html
assert "Deal 0" in html and "Deal 1" in html and "Deal 2" in html
assert "Deal 3" not in html and "Deal 4" not in html
assert "+2 more" in html
print("PASSED — 1a: a 'list' component shows only the first max_rows real rows plus a "
      "'+N more' note for the rest.\n")

view_empty = {"heading": "Nothing found", "components": [{"type": "list", "rows": []}]}
html = renderer.render_chat_inline_result(view_empty)
assert "Nothing matched" in html
print("PASSED — 1b: an empty list component still shows a real 'Nothing matched' card, "
      "not a blank one.\n")

view_blank = {"heading": "", "components": []}
assert renderer.render_chat_inline_result(view_blank) == ""
print("PASSED — 1c: a view with no components at all renders '' so studio.py never shows "
      "an empty card under a bubble.\n")

# --- 2. stat_grid / panel / suggestions --------------------------------------
view_stats = {"heading": "Pipeline", "components": [{
    "type": "stat_grid",
    "stats": [{"label": "Open", "value": "12"}, {"label": "Won", "value": "4"}],
}]}
html = renderer.render_chat_inline_result(view_stats)
assert "Open" in html and "12" in html and "Won" in html and "4" in html
print("PASSED — 2a: a stat_grid component renders as compact label:value chips.\n")

view_panel = {"heading": "Acme Corp", "components": [{
    "type": "panel", "title": "Acme Corp",
    "fields": [{"label": "Owner", "value": "Alex"}, {"label": "Stage", "value": "Negotiation"}],
}]}
html = renderer.render_chat_inline_result(view_panel)
assert "Acme Corp" in html and "Owner" in html and "Alex" in html
print("PASSED — 2b: a panel component renders its title and a few real fields.\n")

view_suggest = {"heading": "Ideas", "components": [{
    "type": "suggestions", "suggestions": ["Follow up with Alex", "Escalate the invoice"],
}]}
html = renderer.render_chat_inline_result(view_suggest)
assert "Follow up with Alex" in html
print("PASSED — 2c: a suggestions component renders its real bullet items.\n")

# --- 3-4-6. /studio/message integration (Slack, non-Gmail) ------------------


def _client_with_workflow(connector):
    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "demo"
    with mock.patch.object(studio, "get_user", return_value=fake_user):
        wf = studio._create_workflow(connector=connector)
    with client.session_transaction() as sess:
        sess["current_workflow"] = wf["id"]
    return client, wf


def _send(client, connector, text, fake_result):
    with mock.patch.dict(studio.CONNECTORS[connector], {"run_agent": lambda *a, **k: fake_result}):
        return client.post("/studio/message", data={"text": text}, follow_redirects=True)


with mock.patch.object(studio, "get_user", return_value=fake_user):
    client, wf = _client_with_workflow("slack")

    render_1 = {
        "heading": "3 unread DMs",
        "components": [{"type": "list", "rows": [
            {"name": "Priya", "note": "can you review the PR?"},
            {"name": "Sam", "note": "standup moved to 10am"},
            {"name": "Priya", "note": "nvm, figured it out"},
        ]}],
    }
    r = _send(client, "slack", "show my unread DMs", {"render": render_1})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="chat-result-card"' in html
    assert "3 unread DMs" in html and "can you review the PR?" in html
    print("PASSED — 3: a successful Slack build attaches its own inline result card "
          "under that turn's chat bubble.\n")

    render_2 = {
        "heading": "#eng-team channel",
        "components": [{"type": "list", "rows": [{"name": "Deploy done", "note": "10 min ago"}]}],
    }
    r = _send(client, "slack", "what's happening in #eng-team", {"render": render_2})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    # BOTH turns' inline cards still present — the whole point of "history"
    assert "3 unread DMs" in html and "can you review the PR?" in html
    assert "#eng-team channel" in html and "Deploy done" in html
    assert html.count('class="chat-result-card"') == 2
    print("PASSED — 4: two separate builds in the same conversation BOTH keep their own "
          "inline result card in the transcript, not just the latest.\n")

    # --- 6. A plain conversational reply gets no inline card ----------------
    r = _send(client, "slack", "thanks!", {"text": "You're welcome! Let me know if you need anything else."})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert html.count('class="chat-result-card"') == 2  # unchanged — no new card added
    assert "You&#x27;re welcome" in html or "You're welcome" in html
    print("PASSED — 6: a plain conversational reply (no render_view call) adds no inline "
          "result card.\n")

# --- 5. /studio/message integration (Gmail) ----------------------------------
with mock.patch.object(studio, "get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_user", return_value=fake_user), \
     mock.patch("gmail_site.get_gmail_messages", return_value=[]):

    client, wf = _client_with_workflow("gmail")
    gmail_render = {
        "heading": "2 emails from uber.com",
        "components": [{"type": "list", "rows": [
            {"name": "Uber", "note": "Your trip receipt"},
            {"name": "Uber Eats", "note": "Order confirmed"},
        ]}],
    }
    r = _send(client, "gmail", "emails from uber", {"render": gmail_render, "fetch_args": {"query": "from:uber.com"}})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert 'class="chat-result-card"' in html
    assert "2 emails from uber.com" in html and "Your trip receipt" in html
    print("PASSED — 5: a Gmail turn's own inline result card also appears under its chat "
          "bubble, independent of the separate real-panel fetch_args scoping.\n")

print("ALL CHAT INLINE RESULT (HISTORY) TESTS PASSED")
