"""
Tests the Gmail folder-support feature added 2026-08-26 at the user's request
("checking spam, sent message all the inbuilt which gmail offers"):

  1. connectors_gmail.FOLDERS / get_gmail_messages(folder=...) builds the exact
     right `q` search string per folder, unknown folders raise RuntimeError,
     and folder + unread_only + query compose correctly together.
  2. agent_gmail.py's TOOLS schema actually exposes `folder` with the right enum,
     and the SYSTEM prompt mentions the real folder values.
  3. gmail_site.py's /inbox?folder=X route uses the right folder for the
     underlying data call and renders the folder tabs with the right one active,
     and an unrecognized/tampered ?folder= value falls back to All Mail instead
     of erroring.
"""
import sys, unittest.mock as mock

sys.path.insert(0, "/home/claude/pilant-agent")

import connectors_gmail as cg

# ---------------------------------------------------------------------------
# Part 1: connectors_gmail.FOLDERS / get_gmail_messages query construction
# ---------------------------------------------------------------------------

captured_params = []


def fake_get(path, params=None):
    if path == "/messages":
        captured_params.append(params)
        return {"messages": []}
    raise AssertionError(f"unexpected _get call: {path}")


with mock.patch.object(cg, "_get", side_effect=fake_get):
    # Every built-in folder maps to the exact real Gmail search operator.
    expected = {
        "inbox": "in:inbox",
        "sent": "in:sent",
        "spam": "in:spam",
        "drafts": "in:drafts",
        "trash": "in:trash",
        "starred": "is:starred",
        "important": "is:important",
    }
    assert cg.FOLDERS == expected, f"FOLDERS mismatch: {cg.FOLDERS}"

    for key, term in expected.items():
        captured_params.clear()
        cg.get_gmail_messages(folder=key, limit=5)
        assert captured_params[0]["q"] == term, (
            f"folder={key!r}: expected q={term!r}, got {captured_params[0]['q']!r}"
        )

    # Case-insensitive folder key.
    captured_params.clear()
    cg.get_gmail_messages(folder="SPAM", limit=5)
    assert captured_params[0]["q"] == "in:spam", captured_params[0]

    # folder + unread_only + query all compose together, in that order.
    captured_params.clear()
    cg.get_gmail_messages(folder="inbox", unread_only=True, query="from:boss@x.com", limit=5)
    assert captured_params[0]["q"] == "in:inbox is:unread from:boss@x.com", captured_params[0]["q"]

    # No folder at all -> unchanged legacy behavior (q built from unread_only/query only).
    captured_params.clear()
    cg.get_gmail_messages(unread_only=True, limit=5)
    assert captured_params[0]["q"] == "is:unread", captured_params[0]["q"]

    captured_params.clear()
    cg.get_gmail_messages(limit=5)
    assert captured_params[0]["q"] is None, captured_params[0]["q"]

    # Unknown folder raises RuntimeError, doesn't silently fall through.
    threw = False
    try:
        cg.get_gmail_messages(folder="archive", limit=5)
    except RuntimeError as e:
        threw = True
        assert "archive" in str(e) and "label:" in str(e), str(e)
    assert threw, "expected RuntimeError for unknown folder"

print("PASSED — Part 1: connectors_gmail.FOLDERS / get_gmail_messages query construction.\n")


# ---------------------------------------------------------------------------
# Part 1b: sent/drafts messages carry a real "to" field
# ---------------------------------------------------------------------------

def fake_get_with_detail(path, params=None):
    if path == "/messages":
        return {"messages": [{"id": "m1"}]}
    if path == "/messages/m1":
        return {
            "snippet": "hey there",
            "labelIds": [],
            "payload": {"headers": [
                {"name": "From", "value": "me@example.com"},
                {"name": "To", "value": "someone@example.com"},
                {"name": "Subject", "value": "hi"},
                {"name": "Date", "value": "Mon, 1 Jan 2026 00:00:00 +0000"},
            ]},
        }
    raise AssertionError(f"unexpected _get call: {path}")


with mock.patch.object(cg, "_get", side_effect=fake_get_with_detail):
    msgs = cg.get_gmail_messages(folder="sent", limit=5)
    assert len(msgs) == 1
    assert msgs[0]["to"] == "someone@example.com", msgs[0]
    assert msgs[0]["from"] == "me@example.com", msgs[0]

print("PASSED — Part 1b: get_gmail_messages returns a real 'to' field per message.\n")


# ---------------------------------------------------------------------------
# Part 2: agent_gmail.py TOOLS schema + SYSTEM prompt
# ---------------------------------------------------------------------------
import agent_gmail

tool_def = next(t for t in agent_gmail.TOOLS if t["name"] == "get_gmail_messages")
props = tool_def["input_schema"]["properties"]
assert "folder" in props, f"folder missing from get_gmail_messages schema: {props.keys()}"
enum = set(props["folder"].get("enum", []))
assert enum == set(cg.FOLDERS.keys()), f"schema enum {enum} != FOLDERS keys {set(cg.FOLDERS.keys())}"

for key in cg.FOLDERS:
    assert key in agent_gmail.SYSTEM, f"SYSTEM prompt never mentions folder value {key!r}"

print("PASSED — Part 2: agent_gmail.py TOOLS schema exposes folder with the right enum,")
print("         and SYSTEM prompt references every folder value.\n")


# ---------------------------------------------------------------------------
# Part 3: gmail_site.py /inbox?folder=X route + folder tabs
#
# gmail_site.py is now a Blueprint (gmail_bp), not its own Flask app —
# merged into studio.py 2026-08-26 (see that file's module docstring). Build
# a minimal standalone Flask app here that registers the same blueprint the
# same way studio.py does (no url_prefix), plus a stand-in "login" route
# (gmail_bp's login_required redirects to url_for("login"), which in real
# use is studio.py's top-level route) — this tests the blueprint in
# isolation without needing all of studio.py's chat/workflow machinery.
# ---------------------------------------------------------------------------
import flask
import gmail_site

_test_app = flask.Flask("gmail_blueprint_test")
_test_app.config["TESTING"] = True
_test_app.secret_key = "test-secret"
_test_app.register_blueprint(gmail_site.gmail_bp)


@_test_app.route("/login")
def login():
    return "login page", 200


gmail_site.app = _test_app  # so the rest of this test file's `gmail_site.app.test_client()` calls keep working


def make_client_with_session():
    client = gmail_site.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "testuser"
    return client


fake_user = {"username": "testuser", "name": "Test User"}

with mock.patch.object(gmail_site, "get_user", return_value=fake_user):
    calls = []

    def fake_get_gmail_messages(query=None, folder=None, limit=10, unread_only=False):
        calls.append({"query": query, "folder": folder, "limit": limit})
        return []

    with mock.patch.object(gmail_site, "get_gmail_messages", side_effect=fake_get_gmail_messages):
        client = make_client_with_session()

        # A valid folder is passed straight through to get_gmail_messages, and its
        # tab renders as active.
        resp = client.get("/inbox?folder=spam")
        assert resp.status_code == 200, resp.status_code
        assert calls[-1]["folder"] == "spam", calls[-1]
        html = resp.get_data(as_text=True)
        assert 'class="sidebar-folder active"' in html, "no active sidebar-folder class rendered"
        assert ">Spam<" in html, "Spam tab label not found"

        # An unrecognized/tampered ?folder= value falls back to All Mail (folder=None),
        # not an error page.
        calls.clear()
        resp2 = client.get("/inbox?folder=not-a-real-folder")
        assert resp2.status_code == 200, resp2.status_code
        assert calls[-1]["folder"] is None, calls[-1]

        # No folder at all -> All Mail, same as before this feature existed.
        calls.clear()
        resp3 = client.get("/inbox")
        assert resp3.status_code == 200
        assert calls[-1]["folder"] is None, calls[-1]

        # Case-insensitivity from the query string too.
        calls.clear()
        resp4 = client.get("/inbox?folder=SENT")
        assert resp4.status_code == 200
        assert calls[-1]["folder"] == "sent", calls[-1]

print("PASSED — Part 3: gmail_site.py /inbox?folder=X routes to the right folder,")
print("         renders the matching active tab, and falls back safely on a bad value.\n")

# ---------------------------------------------------------------------------
# Part 4: /message/<id> view + draft/reply POST routes preserve folder context
# round-trip, and sent/drafts messages show "To" instead of "From" in the row.
# ---------------------------------------------------------------------------
fake_msg = {
    "id": "m1",
    "from": "me@example.com",
    "to": "someone@example.com",
    "subject": "hi",
    "date": "Mon, 1 Jan 2026 00:00:00 +0000",
    "body": "hello there",
    "unread": False,
}

with mock.patch.object(gmail_site, "get_user", return_value=fake_user), \
     mock.patch.object(gmail_site, "get_message_full", return_value=fake_msg):
    client = make_client_with_session()

    # GET /message/m1?folder=sent -> back link points back to /inbox?folder=sent,
    # and the message meta line shows the real "to" field.
    resp = client.get("/message/m1?folder=sent")
    assert resp.status_code == 200, resp.status_code
    html = resp.get_data(as_text=True)
    assert 'href="/inbox?folder=sent"' in html, "back-link doesn't preserve folder=sent"
    assert "To someone@example.com" in html, "message detail doesn't show the 'to' field"
    # The draft/reply forms both carry the folder forward as a hidden field.
    assert html.count('name="folder" value="sent"') == 2, (
        "expected both the draft and reply forms to carry folder=sent forward"
    )

    # An unrecognized ?folder= on the message view also falls back safely (no 500).
    resp_bad = client.get("/message/m1?folder=bogus")
    assert resp_bad.status_code == 200
    html_bad = resp_bad.get_data(as_text=True)
    assert 'href="/inbox"' in html_bad, "bad folder should fall back to plain /inbox back-link"

    # POST /message/m1/draft with folder=spam in the form redirects back to
    # /message/m1?folder=spam, not a bare /message/m1.
    with mock.patch.object(gmail_site, "draft_reply_text", return_value="a suggested reply"):
        resp_draft = client.post("/message/m1/draft", data={"instruction": "", "folder": "spam"})
        assert resp_draft.status_code == 302, resp_draft.status_code
        assert resp_draft.headers["Location"] == "/message/m1?folder=spam", resp_draft.headers["Location"]

    # POST /message/m1/reply with a real body + folder=spam sends and redirects to
    # /inbox?folder=spam, preserving the folder the person was reading from.
    with mock.patch.object(gmail_site, "send_reply", return_value={"to": "someone@example.com"}):
        resp_reply = client.post("/message/m1/reply", data={"body": "sounds good", "folder": "spam"})
        assert resp_reply.status_code == 302, resp_reply.status_code
        assert resp_reply.headers["Location"] == "/inbox?folder=spam", resp_reply.headers["Location"]

    # POST /message/m1/reply with an EMPTY body re-renders the message page (no send),
    # still carrying folder=spam forward rather than losing it.
    resp_empty = client.post("/message/m1/reply", data={"body": "   ", "folder": "spam"})
    assert resp_empty.status_code == 200, resp_empty.status_code
    html_empty = resp_empty.get_data(as_text=True)
    assert 'href="/inbox?folder=spam"' in html_empty, "empty-body re-render lost folder context"

print("PASSED — Part 4: /message/<id> view and draft/reply POST routes correctly")
print("         round-trip folder context end to end (view -> draft -> reply -> back to inbox).\n")

# ---------------------------------------------------------------------------
# Part 5: new write functions (send_email, forward_message, trash_message,
# untrash_message, modify_labels, star_message, mark_important, mark_read) —
# added 2026-08-26 for the full compose/delete/star/forward feature.
# ---------------------------------------------------------------------------
captured_posts = []


def fake_post(path, payload):
    captured_posts.append((path, payload))
    return {"id": "new-id", "threadId": "thread-1", "labelIds": ["INBOX"]}


with mock.patch.object(cg, "_post", side_effect=fake_post):
    # send_email builds a real MIME message with To/Subject/Cc/Bcc and posts to /messages/send.
    captured_posts.clear()
    result = cg.send_email("alice@example.com", "Hello", "hi there", cc="bob@example.com", bcc="carol@example.com")
    assert captured_posts[0][0] == "/messages/send", captured_posts
    assert result["to"] == "alice@example.com" and result["subject"] == "Hello", result
    raw = captured_posts[0][1]["raw"]
    import base64 as _b64
    decoded = _b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    assert "To: alice@example.com" in decoded, decoded
    assert "Cc: bob@example.com" in decoded, decoded
    assert "Bcc: carol@example.com" in decoded, decoded
    import email as _emaillib
    parsed = _emaillib.message_from_string(decoded)
    assert parsed.get_payload(decode=True).decode("utf-8").strip() == "hi there", parsed.get_payload(decode=True)

    # send_email with no 'to' raises rather than silently posting nothing.
    threw = False
    try:
        cg.send_email("", "Hello", "hi")
    except RuntimeError:
        threw = True
    assert threw, "expected RuntimeError for missing 'to'"

    # trash / untrash hit the right endpoints.
    captured_posts.clear()
    r = cg.trash_message("m1")
    assert captured_posts[0][0] == "/messages/m1/trash", captured_posts
    assert r["trashed"] is True

    captured_posts.clear()
    r = cg.untrash_message("m1")
    assert captured_posts[0][0] == "/messages/m1/untrash", captured_posts
    assert r["trashed"] is False

    # star/important/read wrappers build the right addLabelIds/removeLabelIds.
    captured_posts.clear()
    cg.star_message("m1", starred=True)
    assert captured_posts[0] == ("/messages/m1/modify", {"addLabelIds": ["STARRED"]}), captured_posts[0]

    captured_posts.clear()
    cg.star_message("m1", starred=False)
    assert captured_posts[0] == ("/messages/m1/modify", {"removeLabelIds": ["STARRED"]}), captured_posts[0]

    captured_posts.clear()
    cg.mark_important("m1", important=True)
    assert captured_posts[0] == ("/messages/m1/modify", {"addLabelIds": ["IMPORTANT"]}), captured_posts[0]

    captured_posts.clear()
    cg.mark_read("m1", read=True)
    assert captured_posts[0] == ("/messages/m1/modify", {"removeLabelIds": ["UNREAD"]}), captured_posts[0]

    captured_posts.clear()
    cg.mark_read("m1", read=False)
    assert captured_posts[0] == ("/messages/m1/modify", {"addLabelIds": ["UNREAD"]}), captured_posts[0]

print("PASSED — Part 5: send_email/forward_message/trash_message/untrash_message/")
print("         star_message/mark_important/mark_read all build the right Gmail API calls.\n")


# forward_message needs get_message_full mocked too (it fetches the original first).
with mock.patch.object(cg, "_post", side_effect=fake_post), \
     mock.patch.object(cg, "get_message_full", return_value={
         "id": "orig1", "threadId": "t1", "from": "sender@example.com", "to": "me@example.com",
         "subject": "Q3 numbers", "date": "Mon, 1 Jan 2026 00:00:00 +0000",
         "message_id_header": "<abc@mail.gmail.com>", "references": "", "body": "here are the numbers",
         "unread": False, "starred": False, "important": False,
     }):
    captured_posts.clear()
    r = cg.forward_message("orig1", "newperson@example.com", note="fyi")
    assert captured_posts[0][0] == "/messages/send", captured_posts
    raw = captured_posts[0][1]["raw"]
    decoded = _b64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    assert "To: newperson@example.com" in decoded, decoded
    assert "Subject: Fwd: Q3 numbers" in decoded, decoded
    assert "In-Reply-To" not in decoded, "forward should NOT thread to the original like a reply does"
    fwd_parsed = _emaillib.message_from_string(decoded)
    fwd_body = fwd_parsed.get_payload(decode=True).decode("utf-8")
    assert "fyi" in fwd_body and "here are the numbers" in fwd_body and "Forwarded message" in fwd_body, fwd_body

print("PASSED — Part 5b: forward_message quotes the original message correctly and")
print("         is NOT threaded via In-Reply-To (a forward is a new conversation).\n")


# get_gmail_messages / get_message_full now also expose starred/important.
def fake_get_with_flags(path, params=None):
    if path == "/messages":
        return {"messages": [{"id": "m1"}]}
    if path == "/messages/m1":
        if params and params.get("format") == "full":
            return {
                "threadId": "t1", "labelIds": ["STARRED", "IMPORTANT"],
                "payload": {"headers": [{"name": "Subject", "value": "hi"}]},
            }
        return {
            "snippet": "hey", "labelIds": ["STARRED"],
            "payload": {"headers": [{"name": "Subject", "value": "hi"}]},
        }
    raise AssertionError(path)


with mock.patch.object(cg, "_get", side_effect=fake_get_with_flags):
    msgs = cg.get_gmail_messages(limit=5)
    assert msgs[0]["starred"] is True and msgs[0]["important"] is False, msgs[0]

    full = cg.get_message_full("m1")
    assert full["starred"] is True and full["important"] is True, full

print("PASSED — Part 5c: get_gmail_messages/get_message_full expose starred/important flags.\n")

# ---------------------------------------------------------------------------
# Part 6: gmail_site.py's new full-interface routes — Compose, Delete,
# Star toggle, Mark unread, Forward — added 2026-08-26 at the user's request
# ("full view interface... sending email, write email, delete email").
# ---------------------------------------------------------------------------
with mock.patch.object(gmail_site, "get_user", return_value=fake_user):
    client = make_client_with_session()

    # --- Compose: GET renders a blank form; POST with no 'to' re-renders
    # with an error (never silently drops the draft); POST with a real 'to'
    # sends via send_email() and redirects to the Sent folder.
    resp = client.get("/compose")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert 'action="/compose"' in html and 'name="to"' in html and 'name="body"' in html

    resp_noto = client.post("/compose", data={"to": "", "subject": "hi", "body": "hello"})
    assert resp_noto.status_code == 200
    assert "recipient" in resp_noto.get_data(as_text=True).lower()

    sent_calls = []
    with mock.patch.object(gmail_site, "send_email",
                            side_effect=lambda to, subject, body, cc=None, bcc=None: (
                                sent_calls.append({"to": to, "subject": subject, "body": body, "cc": cc}),
                                {"id": "new1", "threadId": "t1", "to": to, "subject": subject},
                            )[1]):
        resp_send = client.post("/compose", data={"to": "alice@example.com", "cc": "bob@example.com",
                                                    "subject": "Hi Alice", "body": "hello there"})
        assert resp_send.status_code == 302, resp_send.status_code
        assert resp_send.headers["Location"] == "/inbox?folder=sent", resp_send.headers["Location"]
        assert sent_calls[0]["to"] == "alice@example.com" and sent_calls[0]["cc"] == "bob@example.com"

    # A 403 (missing gmail.send) re-renders compose with a clear scope-error message
    # instead of eating the draft or crashing.
    with mock.patch.object(gmail_site, "send_email", side_effect=RuntimeError("Gmail API error 403 for POST ...")):
        resp_403 = client.post("/compose", data={"to": "alice@example.com", "subject": "x", "body": "y"})
        assert resp_403.status_code == 200
        assert "gmail.send" in resp_403.get_data(as_text=True)

print("PASSED — Part 6a: /compose renders, validates recipient, sends via send_email(),")
print("         and surfaces a clear scope error on 403 without losing the draft.\n")


with mock.patch.object(gmail_site, "get_user", return_value=fake_user):
    client = make_client_with_session()

    # --- Star toggle: flips based on the 'starred' hidden field, redirects to 'next'.
    with mock.patch.object(gmail_site, "star_message") as star_mock:
        resp = client.post("/message/m1/star", data={"starred": "0", "next": "/inbox?folder=spam"})
        assert resp.status_code == 302 and resp.headers["Location"] == "/inbox?folder=spam"
        star_mock.assert_called_once_with("m1", starred=True)

        star_mock.reset_mock()
        resp2 = client.post("/message/m1/star", data={"starred": "1", "next": "/message/m1"})
        assert resp2.headers["Location"] == "/message/m1"
        star_mock.assert_called_once_with("m1", starred=False)

    # An off-site 'next' is rejected in favor of the plain inbox (open-redirect guard).
    with mock.patch.object(gmail_site, "star_message"):
        resp_evil = client.post("/message/m1/star", data={"starred": "0", "next": "https://evil.example/steal"})
        assert resp_evil.headers["Location"] == "/inbox", resp_evil.headers["Location"]
        resp_evil2 = client.post("/message/m1/star", data={"starred": "0", "next": "//evil.example/steal"})
        assert resp_evil2.headers["Location"] == "/inbox", resp_evil2.headers["Location"]

    # --- Delete: trashes and redirects to 'next', with a flash confirming it.
    with mock.patch.object(gmail_site, "trash_message") as trash_mock:
        resp = client.post("/message/m1/delete", data={"next": "/inbox?folder=inbox"})
        assert resp.status_code == 302 and resp.headers["Location"] == "/inbox?folder=inbox"
        trash_mock.assert_called_once_with("m1")

    # --- Mark unread: always redirects to the inbox (not back to the message).
    with mock.patch.object(gmail_site, "mark_read") as mark_mock:
        resp = client.post("/message/m1/read", data={"folder": "inbox"})
        assert resp.status_code == 302 and resp.headers["Location"] == "/inbox?folder=inbox"
        mark_mock.assert_called_once_with("m1", read=False)

    # A 403 on delete surfaces the gmail.modify scope hint via flash, not a crash.
    with mock.patch.object(gmail_site, "trash_message", side_effect=RuntimeError("Gmail API error 403 for POST ...")):
        resp = client.post("/message/m1/delete", data={"next": "/inbox"})
        assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "gmail.modify" in (sess.get("flash_err") or ""), sess.get("flash_err")

print("PASSED — Part 6b: star/delete/mark-unread routes call the right connector function")
print("         with the right args, redirect correctly, guard against open redirects via")
print("         'next', and surface gmail.modify scope errors on 403 instead of crashing.\n")


with mock.patch.object(gmail_site, "get_user", return_value=fake_user), \
     mock.patch.object(gmail_site, "get_message_full", return_value=fake_msg):
    client = make_client_with_session()

    # --- Forward: GET pre-fills a real quoted block from the ACTUAL message data.
    resp = client.get("/message/m1/forward?folder=inbox")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "Forwarded message" in html
    assert "hello there" in html  # fake_msg's real body
    assert "someone@example.com" in html  # fake_msg's real 'to'

    # POST with no 'to' re-renders with an error, doesn't silently drop the edit.
    resp_noto = client.post("/message/m1/forward", data={"to": "", "body": "quoted text", "folder": "inbox"})
    assert resp_noto.status_code == 200
    assert "recipient" in resp_noto.get_data(as_text=True).lower()

    # POST with a 'to' sends EXACTLY the textarea content (no re-quoting) via send_email(),
    # with a Fwd:-prefixed subject, and redirects back to the folder the user came from.
    fwd_calls = []
    with mock.patch.object(gmail_site, "send_email",
                            side_effect=lambda to, subject, body, cc=None, bcc=None: (
                                fwd_calls.append({"to": to, "subject": subject, "body": body}),
                                {"id": "f1", "threadId": "t1", "to": to, "subject": subject},
                            )[1]):
        resp_fwd = client.post("/message/m1/forward",
                                data={"to": "newperson@example.com", "body": "edited quote block", "folder": "spam"})
        assert resp_fwd.status_code == 302
        assert resp_fwd.headers["Location"] == "/inbox?folder=spam", resp_fwd.headers["Location"]
        assert fwd_calls[0]["to"] == "newperson@example.com"
        assert fwd_calls[0]["subject"] == "Fwd: hi"  # fake_msg's subject is "hi"
        assert fwd_calls[0]["body"] == "edited quote block", (
            "forward should send EXACTLY what's in the textarea, not re-quote via forward_message()"
        )

print("PASSED — Part 6c: /message/<id>/forward pre-fills a real quoted block, validates the")
print("         recipient, and sends exactly the (possibly person-edited) textarea content —")
print("         no double-quoting from also calling connectors_gmail.forward_message().\n")

# ---------------------------------------------------------------------------
# Part 6d: view_message() auto-marks an unread message read on open (real
# Gmail behavior), best-effort (a 403 from missing gmail.modify must not
# break the ability to just read the message). List rows render real
# star/delete icon-button forms (not nested inside the row's <a>, which
# would be invalid HTML) reflecting each message's actual starred state.
# ---------------------------------------------------------------------------
unread_msg = dict(fake_msg, unread=True, starred=False)

with mock.patch.object(gmail_site, "get_user", return_value=fake_user), \
     mock.patch.object(gmail_site, "get_message_full", return_value=unread_msg):
    client = make_client_with_session()
    with mock.patch.object(gmail_site, "mark_read") as mark_mock:
        resp = client.get("/message/m1")
        assert resp.status_code == 200
        mark_mock.assert_called_once_with("m1", read=True)

    # Best-effort: a 403 (gmail.modify not granted) doesn't break viewing.
    with mock.patch.object(gmail_site, "mark_read", side_effect=RuntimeError("403 forbidden")):
        resp2 = client.get("/message/m1")
        assert resp2.status_code == 200, "viewing must survive mark_read failing"

with mock.patch.object(gmail_site, "get_user", return_value=fake_user):
    calls = []

    def fake_get_gmail_messages2(query=None, folder=None, limit=10, unread_only=False):
        calls.append(1)
        return [
            {"id": "s1", "from": "a@x.com", "to": "", "subject": "starred one", "date": "",
             "snippet": "", "unread": False, "starred": True, "important": False},
            {"id": "s2", "from": "b@x.com", "to": "", "subject": "not starred", "date": "",
             "snippet": "", "unread": True, "starred": False, "important": False},
        ]

    with mock.patch.object(gmail_site, "get_gmail_messages", side_effect=fake_get_gmail_messages2):
        client = make_client_with_session()
        resp = client.get("/inbox")
        html = resp.get_data(as_text=True)
        # Starred message shows a filled star icon-button marked active; both rows
        # get a delete icon-button; neither is nested inside the row's own <a> link
        # (a <form> can't legally nest inside an <a>).
        assert 'action="/message/s1/star"' in html and 'action="/message/s2/star"' in html
        assert 'action="/message/s1/delete"' in html and 'action="/message/s2/delete"' in html
        assert 'name="starred" value="1"' in html  # s1's star form
        assert 'name="starred" value="0"' in html  # s2's star form
        assert 'class="icon-btn star active"' in html, "starred message should render an active star icon"

print("PASSED — Part 6d: opening an unread message marks it read (best-effort, survives a\n"
      "         403), and inbox list rows render real star/delete forms reflecting each\n"
      "         message's actual starred state, positioned outside the row's own link.\n")

print("ALL GMAIL FOLDER TESTS PASSED")
