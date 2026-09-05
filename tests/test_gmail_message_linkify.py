"""
Regression coverage for the Gmail message-detail design fix (2026-08-27):
raw marketing-email tracking links (e.g. Base44's newsletter) were showing
as huge, unreadable, unwrapped URL strings in Pilant Mail's message view —
see gmail_site.py's _linkify_and_nl2br docstring for the full story. This
is a rendering-layer fix only; connectors_gmail.py's real plain-text
extraction is untouched.
"""

import sys
sys.path.insert(0, "/home/claude/pilant-agent")

from gmail_site import _linkify_and_nl2br


def test_url_shown_as_domain_not_raw_token():
    long_url = "https://click.base44.com/f/a/ud7-zCfvYULolznxShht4Q~~/AAQRxRA~/WktlxqKg7lNW3MumyKVL"
    html = _linkify_and_nl2br(f"What's new ({long_url})")
    assert long_url not in html.split(">")[2], "raw tracking URL leaked into visible text"
    assert ">click.base44.com<" in html
    assert f'href="{long_url}"' in html, "real URL must still be the actual link target"


def test_trailing_punctuation_not_swallowed_into_href():
    html = _linkify_and_nl2br("See https://example.com/path?a=1, then reply.")
    assert 'href="https://example.com/path?a=1"' in html
    assert ", then reply." in html


def test_multiple_urls_on_one_line():
    html = _linkify_and_nl2br("First https://a.com/x then https://b.com/y done.")
    assert html.count("msg-link") == 2


def test_blank_lines_become_paragraphs():
    html = _linkify_and_nl2br("Paragraph one.\n\nParagraph two.")
    assert html.count('class="msg-para"') == 2


def test_empty_body_returns_empty():
    assert _linkify_and_nl2br("") == ""


def test_plain_text_with_no_urls_unaffected():
    html = _linkify_and_nl2br("Just a normal message with no links at all.")
    assert "msg-link" not in html
    assert "Just a normal message with no links at all." in html


if __name__ == "__main__":
    test_url_shown_as_domain_not_raw_token()
    test_trailing_punctuation_not_swallowed_into_href()
    test_multiple_urls_on_one_line()
    test_blank_lines_become_paragraphs()
    test_empty_body_returns_empty()
    test_plain_text_with_no_urls_unaffected()
    print("all gmail linkify tests passed")
