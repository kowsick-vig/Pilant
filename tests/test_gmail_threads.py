"""
Regression coverage for real Gmail thread/conversation grouping, added
2026-08-27 as item #1 of the Gmail feature backlog (the user chose "want
full backlog including the image proxy" -> multi-account included -> the
priority order agreed: thread grouping first).

Real Gmail collapses several messages that share a conversation into ONE
row with a count ("Revolut (3)"), not one row per message. Pilant's list
never did this before this date. Covers the whole path: connectors_gmail
.get_gmail_threads() (list-view grouping, built on Gmail's real /threads
endpoint rather than post-hoc grouping of individual messages, to avoid
undercounting a thread whose other messages fall outside one page),
get_thread_full() (the conversation-detail page's full content, one real
API call for every message in it), the thread-level trash_thread()/
star_thread() actions (Gmail's own /threads/{id}/trash and .../modify,
not a loop over every message), and gmail_site.py's rendering + routes
(_thread_row_html, render_thread's <details>/<summary> accordion with the
latest message auto-expanded, and the /thread/<id> Flask routes) —
including the thread-aware redirect fix in draft_message()/reply_message()
so replying from a conversation doesn't lose the rest of the conversation
on an error, or bounce back to the plain inbox on success.
"""

import base64
import sys
sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_gmail as cg
import gmail_site


def _b64url(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).decode().rstrip("=")


def _header_list(**kv):
    return [{"name": k, "value": v} for k, v in kv.items()]


# ---------------------------------------------------------------------
# connectors_gmail.get_gmail_threads
# ---------------------------------------------------------------------

def test_display_name_extracts_name_or_falls_back_to_raw():
    assert cg._display_name("Revolut <no-reply@revolut.com>") == "Revolut"
    assert cg._display_name('"Alice B" <alice@x.com>') == "Alice B"
    assert cg._display_name("bare@x.com") == "bare@x.com"
    assert cg._display_name("") == ""
    assert cg._display_name(None) == ""


def test_get_gmail_threads_groups_by_real_conversation_not_by_page():
    """Mirrors a real 3-message Revolut thread plus a 2-message
    back-and-forth — verifies count, joined senders, unread aggregation
    (ANY message unread -> thread unread), and ordered message_ids."""
    def fake_get(path, params=None):
        if path == "/threads":
            return {"threads": [{"id": "T1"}, {"id": "T2"}]}
        if path == "/threads/T1":
            return {
                "id": "T1",
                "messages": [
                    {"id": "M1", "labelIds": ["INBOX"], "snippet": "first",
                     "payload": {"headers": _header_list(Subject="Deal", From="Revolut <no-reply@revolut.com>", Date="Mon")}},
                    {"id": "M2", "labelIds": ["INBOX", "UNREAD"], "snippet": "second",
                     "payload": {"headers": _header_list(Subject="Re: Deal", From="Revolut <no-reply@revolut.com>", Date="Tue")}},
                    {"id": "M3", "labelIds": ["INBOX"], "snippet": "third",
                     "payload": {"headers": _header_list(Subject="Re: Deal", From="Revolut <no-reply@revolut.com>", Date="Wed")}},
                ],
            }
        if path == "/threads/T2":
            return {
                "id": "T2",
                "messages": [
                    {"id": "M4", "labelIds": ["INBOX"], "snippet": "hi",
                     "payload": {"headers": _header_list(Subject="Q", From="Alice <alice@x.com>", Date="Thu")}},
                    {"id": "M5", "labelIds": ["INBOX"], "snippet": "reply",
                     "payload": {"headers": _header_list(Subject="Re: Q", From="Bob <bob@x.com>", Date="Fri")}},
                ],
            }
        raise AssertionError(f"unexpected path {path}")

    cg._get = fake_get
    threads = cg.get_gmail_threads(folder="inbox", limit=10)

    assert threads[0]["id"] == "T1"
    assert threads[0]["count"] == 3
    assert threads[0]["from"] == "Revolut"
    assert threads[0]["unread"] is True
    assert threads[0]["message_ids"] == ["M1", "M2", "M3"]
    assert threads[0]["subject"] == "Deal"  # first message's subject, not "Re: Deal"

    assert threads[1]["from"] == "Alice, Bob"
    assert threads[1]["count"] == 2
    assert threads[1]["unread"] is False


def test_get_gmail_threads_unknown_folder_raises():
    try:
        cg.get_gmail_threads(folder="not-a-real-folder")
        assert False, "should have raised"
    except RuntimeError as e:
        assert "Unknown folder" in str(e)


# ---------------------------------------------------------------------
# connectors_gmail.get_thread_full
# ---------------------------------------------------------------------

def test_get_thread_full_returns_full_content_per_message():
    def fake_get(path, params=None):
        assert path == "/threads/T1"
        return {
            "id": "T1",
            "messages": [
                {"id": "M1", "threadId": "T1", "labelIds": ["INBOX"],
                 "payload": {"headers": _header_list(Subject="Deal", From="Revolut <a@x.com>", Date="Mon"),
                             "mimeType": "text/plain", "body": {"data": _b64url(b"first body")}}},
                {"id": "M2", "threadId": "T1", "labelIds": ["INBOX", "UNREAD"],
                 "payload": {"headers": _header_list(Subject="Re: Deal", From="Revolut <a@x.com>", Date="Tue"),
                             "mimeType": "multipart/mixed", "parts": [
                                 {"mimeType": "text/plain", "body": {"data": _b64url(b"second body")}},
                                 {"mimeType": "application/pdf", "filename": "a.pdf", "body": {"attachmentId": "AID1", "size": 111}},
                             ]}},
            ],
        }

    cg._get = fake_get
    result = cg.get_thread_full("T1")
    assert result["id"] == "T1"
    assert len(result["messages"]) == 2
    assert result["messages"][0]["body"] == "first body"
    assert result["messages"][1]["body"] == "second body"
    assert result["messages"][1]["attachments"][0]["filename"] == "a.pdf"
    assert result["messages"][1]["unread"] is True
    assert result["messages"][0]["unread"] is False


# ---------------------------------------------------------------------
# connectors_gmail thread-level actions
# ---------------------------------------------------------------------

def test_trash_thread_and_star_thread_use_real_thread_endpoints():
    calls = []

    def fake_post(path, payload):
        calls.append((path, payload))
        return {"id": "T1", "labelIds": []}

    cg._post = fake_post
    cg.trash_thread("T1")
    cg.star_thread("T1", starred=True)
    cg.star_thread("T1", starred=False)

    assert calls[0] == ("/threads/T1/trash", {})
    assert calls[1] == ("/threads/T1/modify", {"addLabelIds": ["STARRED"]})
    assert calls[2] == ("/threads/T1/modify", {"removeLabelIds": ["STARRED"]})


# ---------------------------------------------------------------------
# gmail_site rendering
# ---------------------------------------------------------------------

def test_thread_row_shows_count_badge_only_when_multiple():
    single = {"id": "T1", "from": "Alice", "subject": "Hi", "snippet": "...", "count": 1, "unread": False, "starred": False}
    multi = {"id": "T2", "from": "Revolut", "subject": "Deal", "snippet": "...", "count": 3, "unread": True, "starred": False}
    assert "thread-count" not in gmail_site._thread_row_html(single, folder="inbox")
    multi_html = gmail_site._thread_row_html(multi, folder="inbox")
    assert "(3)" in multi_html
    assert "/thread/T2?folder=inbox" in multi_html
    assert "Unread" in multi_html


def test_thread_row_shows_recipient_for_sent_and_drafts():
    thread = {"id": "T1", "from": "Me", "to": "someone@x.com", "subject": "Hi", "snippet": "", "count": 1, "unread": False, "starred": False}
    html = gmail_site._thread_row_html(thread, folder="sent")
    assert "someone@x.com" in html
    assert "To:" in html


def _sample_messages():
    return [
        {"id": "M1", "from": "Revolut <a@x.com>", "to": "me@x.com", "subject": "Deal", "date": "Mon",
         "body": "first message", "attachments": [], "starred": False},
        {"id": "M2", "from": "Revolut <a@x.com>", "to": "me@x.com", "subject": "Re: Deal", "date": "Tue",
         "body": "second, most recent message",
         "attachments": [{"filename": "a.pdf", "mime_type": "application/pdf", "size": 100, "attachment_id": "AID1"}],
         "starred": True},
    ]


def _render_thread_in_request_context(thread_id, messages, folder="inbox"):
    """render_thread() calls _flash_html(), which reads/pops real Flask
    session keys — same "needs a request context" requirement
    render_message() has always had (see test_gmail_message_linkify.py's
    Flask-context tests). studio.app.test_request_context gives it one
    without needing the full test client / a live route dispatch."""
    import studio
    user = {"name": "Kowsick"}
    with studio.app.test_request_context(f"/thread/{thread_id}"):
        from flask import session
        session["username"] = "kowsick"
        return gmail_site.render_thread(user, thread_id, messages, folder=folder)


def test_render_thread_expands_only_the_last_message():
    html = _render_thread_in_request_context("T1", _sample_messages())
    assert html.count('<details class="thread-msg"') == 2
    assert '<details class="thread-msg" open>' in html
    assert html.count(" open>") == 1  # only the last message is open
    assert "first message" in html and "second, most recent message" in html
    assert "a.pdf" in html


def test_render_thread_reply_scoped_to_last_message_with_thread_field():
    html = _render_thread_in_request_context("T1", _sample_messages())
    assert 'action="/message/M2/reply"' in html
    assert 'name="thread" value="T1"' in html
    assert 'action="/thread/T1/delete"' in html
    assert 'action="/thread/T1/star"' in html


def test_render_thread_empty_messages_does_not_crash():
    html = _render_thread_in_request_context("T1", [])
    assert "(no subject)" in html


# ---------------------------------------------------------------------
# Flask routes, end to end
# ---------------------------------------------------------------------

def _login_client():
    import studio
    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "kowsick"
    return client


def test_view_thread_route_marks_last_message_read_and_renders_all():
    thread = {"id": "T1", "messages": [
        {"id": "M1", "threadId": "T1", "from": "Revolut <a@x.com>", "to": "", "subject": "Deal", "date": "Mon",
         "message_id_header": "", "references": "", "body": "first", "attachments": [],
         "unread": False, "starred": False, "important": False},
        {"id": "M2", "threadId": "T1", "from": "Revolut <a@x.com>", "to": "", "subject": "Re: Deal", "date": "Tue",
         "message_id_header": "", "references": "", "body": "second", "attachments": [],
         "unread": True, "starred": False, "important": False},
    ]}
    marked = []

    def fake_get_thread_full(thread_id):
        assert thread_id == "T1"
        return thread

    def fake_mark_read(message_id, read=True):
        marked.append(message_id)
        return {"id": message_id}

    gmail_site.get_thread_full = fake_get_thread_full
    gmail_site.mark_read = fake_mark_read

    client = _login_client()
    resp = client.get("/thread/T1?folder=inbox")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "first" in body and "second" in body
    assert marked == ["M2"]  # only the last (auto-expanded) message gets marked read


def test_star_and_delete_thread_routes_call_real_thread_actions():
    calls = []
    gmail_site.star_thread = lambda tid, starred=True: calls.append(("star", tid, starred))
    gmail_site.trash_thread = lambda tid: calls.append(("trash", tid))

    client = _login_client()
    r1 = client.post("/thread/T1/star", data={"starred": "0", "next": "/thread/T1?folder=inbox"})
    assert r1.status_code == 302 and r1.headers["Location"] == "/thread/T1?folder=inbox"
    assert calls[-1] == ("star", "T1", True)

    r2 = client.post("/thread/T1/delete", data={"next": "/inbox?folder=inbox"})
    assert r2.status_code == 302
    assert calls[-1] == ("trash", "T1")


def test_reply_from_thread_redirects_to_thread_not_inbox():
    gmail_site.send_reply = lambda message_id, body: {"to": "someone@x.com"}
    client = _login_client()
    resp = client.post("/message/M2/reply", data={"body": "Thanks!", "folder": "inbox", "thread": "T1"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/thread/T1?folder=inbox"


def test_reply_error_from_thread_rerenders_whole_thread_not_just_one_message():
    """The bug this specifically guards against: before the thread-aware
    fix, an error reply from the conversation view would silently drop
    back to a single-message page, losing the rest of the conversation
    the person was reading."""
    thread = {"id": "T1", "messages": [
        {"id": "M1", "threadId": "T1", "from": "Revolut <a@x.com>", "to": "", "subject": "X", "date": "Mon",
         "message_id_header": "", "references": "", "body": "first", "attachments": [],
         "unread": False, "starred": False, "important": False},
        {"id": "M2", "threadId": "T1", "from": "Revolut <a@x.com>", "to": "", "subject": "Re: X", "date": "Tue",
         "message_id_header": "", "references": "", "body": "second", "attachments": [],
         "unread": False, "starred": False, "important": False},
    ]}
    gmail_site.get_thread_full = lambda tid: thread

    client = _login_client()
    resp = client.post("/message/M2/reply", data={"body": "   ", "folder": "inbox", "thread": "T1"})
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Write something before sending." in body
    assert "first" in body and "second" in body


def test_draft_from_thread_stores_under_thread_key_and_redirects_to_thread():
    def fake_get_message_full(message_id):
        return {"id": message_id, "threadId": "T1", "from": "a@x.com", "to": "", "subject": "X",
                "date": "Mon", "body": "hi", "attachments": [], "unread": False}

    gmail_site.get_message_full = fake_get_message_full
    gmail_site.draft_reply_text = lambda msg, name, instruction=None: "Sure, sounds good."

    client = _login_client()
    resp = client.post("/message/M2/draft", data={"instruction": "", "folder": "inbox", "thread": "T1"})
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/thread/T1?folder=inbox"
    with client.session_transaction() as sess:
        assert sess.get("draft:T1") == "Sure, sounds good."
        assert "draft:M2" not in sess


if __name__ == "__main__":
    test_display_name_extracts_name_or_falls_back_to_raw()
    test_get_gmail_threads_groups_by_real_conversation_not_by_page()
    test_get_gmail_threads_unknown_folder_raises()
    test_get_thread_full_returns_full_content_per_message()
    test_trash_thread_and_star_thread_use_real_thread_endpoints()
    test_thread_row_shows_count_badge_only_when_multiple()
    test_thread_row_shows_recipient_for_sent_and_drafts()
    test_render_thread_expands_only_the_last_message()
    test_render_thread_reply_scoped_to_last_message_with_thread_field()
    test_render_thread_empty_messages_does_not_crash()
    test_view_thread_route_marks_last_message_read_and_renders_all()
    test_star_and_delete_thread_routes_call_real_thread_actions()
    test_reply_from_thread_redirects_to_thread_not_inbox()
    test_reply_error_from_thread_rerenders_whole_thread_not_just_one_message()
    test_draft_from_thread_stores_under_thread_key_and_redirects_to_thread()
    print("all gmail thread-grouping tests passed")
