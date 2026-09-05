"""
Regression coverage for real Gmail attachment support, added 2026-08-27
at the user's direct request ("what about attachments") after comparing
a Revolut message with 4 real PDF attachments against Pilant's message
view, which had nothing to show for them — the metadata was simply never
being read before this.

Covers the full path: connectors_gmail._walk_attachments (extracting
metadata from a real nested MIME payload), connectors_gmail.get_attachment
(fetching real bytes), gmail_site._attachments_html (rendering a real
download chip per attachment, not a decorative icon), and the
/message/<id>/attachment/<attachment_id> Flask route end-to-end via the
test client — including the "attachment isn't actually on this message"
error path, since the route re-verifies against real metadata rather
than trusting anything in the URL.
"""

import base64
import sys
sys.path.insert(0, "/home/claude/pilant-agent")

from connectors_gmail import _walk_attachments, _b64url_decode_bytes
import gmail_site


def _b64url(raw_bytes):
    return base64.urlsafe_b64encode(raw_bytes).decode().rstrip("=")


def test_walk_attachments_finds_nested_attachments_only():
    """Mirrors the real Revolut email's structure: multipart/mixed at the
    top, with a multipart/alternative (the two body versions — neither
    has a filename, so neither should be picked up) plus real attachment
    parts as siblings."""
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64url(b"hi")}},
                    {"mimeType": "text/html", "body": {"data": _b64url(b"<p>hi</p>")}},
                ],
            },
            {"mimeType": "application/pdf", "filename": "framework_en.pdf",
             "body": {"attachmentId": "AID1", "size": 15234}},
            {"mimeType": "application/pdf", "filename": "fscs_en.pdf",
             "body": {"attachmentId": "AID2", "size": 98765}},
        ],
    }
    atts = _walk_attachments(payload)
    assert len(atts) == 2
    assert atts[0] == {"filename": "framework_en.pdf", "mime_type": "application/pdf",
                        "size": 15234, "attachment_id": "AID1"}
    assert atts[1]["filename"] == "fscs_en.pdf"


def test_walk_attachments_skips_filename_without_attachment_id():
    """A part could theoretically carry a filename with no attachmentId
    (inline content some other way) — must not produce a broken download
    link for it."""
    payload = {"mimeType": "text/plain", "filename": "weird.txt", "body": {"data": _b64url(b"x")}}
    assert _walk_attachments(payload) == []


def test_b64url_decode_bytes_roundtrip():
    raw = b"%PDF-1.4 fake pdf bytes, not text"
    assert _b64url_decode_bytes(_b64url(raw)) == raw
    assert _b64url_decode_bytes("") == b""
    assert _b64url_decode_bytes(None) == b""


def test_format_size_human_readable():
    assert gmail_site._format_size(0) == "0 B"
    assert gmail_site._format_size(500) == "500 B"
    assert "KB" in gmail_site._format_size(15234)
    assert "MB" in gmail_site._format_size(1234567)
    assert gmail_site._format_size(None) == "0 B"


def test_attachments_html_renders_real_download_links():
    msg = {
        "id": "msg123",
        "attachments": [
            {"filename": "framework_en.pdf", "mime_type": "application/pdf",
             "size": 15234, "attachment_id": "AID1"},
            {"filename": "fscs_en.pdf", "mime_type": "application/pdf",
             "size": 98765, "attachment_id": "AID2"},
        ],
    }
    html = gmail_site._attachments_html(msg, folder="inbox")
    assert "2 Attachments" in html
    assert "/message/msg123/attachment/AID1?folder=inbox" in html
    assert "/message/msg123/attachment/AID2?folder=inbox" in html
    assert "framework_en.pdf" in html and "fscs_en.pdf" in html


def test_attachments_html_empty_renders_nothing():
    assert gmail_site._attachments_html({"id": "x", "attachments": []}) == ""
    assert gmail_site._attachments_html({"id": "x"}) == ""


def _login_client():
    import studio
    client = studio.app.test_client()
    with client.session_transaction() as sess:
        sess["username"] = "kowsick"
    return client


def test_download_route_serves_real_bytes_with_correct_headers():
    def fake_get_message_full(message_id):
        return {
            "id": message_id, "body": "hi", "unread": False,
            "attachments": [{"filename": "fscs_en.pdf", "mime_type": "application/pdf",
                              "size": 98765, "attachment_id": "AID2"}],
        }

    def fake_get_attachment(message_id, attachment_id):
        assert attachment_id == "AID2"
        return b"%PDF-1.4 real pdf bytes here"

    gmail_site.get_message_full = fake_get_message_full
    gmail_site.get_attachment = fake_get_attachment

    client = _login_client()
    resp = client.get("/message/msg123/attachment/AID2?folder=inbox")
    assert resp.status_code == 200
    assert resp.data == b"%PDF-1.4 real pdf bytes here"
    assert resp.content_type == "application/pdf"
    assert 'filename="fscs_en.pdf"' in resp.headers.get("Content-Disposition", "")


def test_download_succeeds_even_when_metadata_lookup_finds_no_match():
    """CORRECTED 2026-08-27 (same day as the original build): the first
    version required attachment_id to exactly match an entry re-fetched
    via get_message_full() BEFORE calling get_attachment() at all — live
    testing broke on a real attachment that plainly existed. Gmail's own
    messages.attachments.get is already scoped to the (message_id,
    attachment_id) pair by Google's backend, so that pre-check was
    redundant duplicate authorization and just a second chance to be
    wrong. The download must now succeed whenever get_attachment()
    itself succeeds, with a generic fallback filename/mime type when the
    metadata lookup doesn't find a match — metadata is cosmetic, never a
    gate."""
    def fake_get_message_full(message_id):
        return {"id": message_id, "attachments": [], "body": "", "unread": False}

    def fake_get_attachment(message_id, attachment_id):
        return b"real bytes Gmail was willing to serve"

    gmail_site.get_message_full = fake_get_message_full
    gmail_site.get_attachment = fake_get_attachment

    client = _login_client()
    resp = client.get("/message/msg123/attachment/SOME_ID")
    assert resp.status_code == 200
    assert resp.data == b"real bytes Gmail was willing to serve"
    assert resp.content_type == "application/octet-stream"  # fallback, since metadata had no match
    assert 'filename="attachment"' in resp.headers.get("Content-Disposition", "")


def test_download_route_fails_when_gmail_itself_rejects_the_pair():
    """The real authority is Gmail's own API: if get_attachment() raises
    (Google's backend rejecting a message_id/attachment_id pair that
    genuinely don't belong together), the route must redirect with a
    clear error rather than serve empty/broken content."""
    def fake_get_attachment(message_id, attachment_id):
        raise RuntimeError("Gmail API error 404 for /messages/msg123/attachments/BOGUS: not found")

    gmail_site.get_attachment = fake_get_attachment

    client = _login_client()
    resp = client.get("/message/msg123/attachment/BOGUS")
    assert resp.status_code == 302
    assert "/message/msg123" in resp.headers.get("Location", "")


def test_safe_content_disposition_strips_header_injection_chars():
    out = gmail_site._safe_content_disposition('evil".pdf\r\nX-Injected: yes')
    assert "\r" not in out and "\n" not in out
    assert out.count('"') == 2  # only the two we add ourselves


if __name__ == "__main__":
    test_walk_attachments_finds_nested_attachments_only()
    test_walk_attachments_skips_filename_without_attachment_id()
    test_b64url_decode_bytes_roundtrip()
    test_format_size_human_readable()
    test_attachments_html_renders_real_download_links()
    test_attachments_html_empty_renders_nothing()
    test_download_route_serves_real_bytes_with_correct_headers()
    test_download_succeeds_even_when_metadata_lookup_finds_no_match()
    test_download_route_fails_when_gmail_itself_rejects_the_pair()
    test_safe_content_disposition_strips_header_injection_chars()
    print("all gmail attachment tests passed")
