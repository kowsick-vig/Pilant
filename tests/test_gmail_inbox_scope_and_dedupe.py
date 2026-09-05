"""
Regression coverage for the 2026-08-27 fix to two real bugs the user
caught by directly comparing Pilant Mail against their actual Gmail:

  1. "it's not fetching in the same order messages" — Pilant's Gmail
     views (both the embedded Studio panel and every link into the full
     /inbox page) called get_gmail_messages() with no `folder`, which
     connectors_gmail.py defaults to unscoped "All Mail" (everything
     except Spam/Trash — Sent, Promotions, Social, Updates, all of it).
     Real Gmail's Inbox tab is a different, narrower scope, so the
     message set and order never matched. Fixed by scoping
     gmail_site.render_inbox_panel() to folder="inbox" and pointing
     every studio.py link at /inbox?folder=inbox instead of bare /inbox
     (the bare /inbox default is untouched — it's what the sidebar's own
     "All Mail" tab intentionally relies on).

     That alone wasn't enough — a follow-up screenshot comparison showed
     Promotions-category mail (Base44, Uber One, SpareRoom, ...) still at
     the top of Pilant's list even though it was scoped to folder="inbox".
     Root cause: FOLDERS["inbox"] was "in:inbox" alone, which is
     "everything with the Inbox label" — not what Gmail's own tabbed
     inbox actually shows by default, which is the Primary tab
     (in:inbox category:primary). Fixed by adding category:primary to
     the "inbox" folder's real Gmail search operator in connectors_gmail.
     FOLDERS. (Separately: the local Studio server runs with debug=False,
     so it does NOT hot-reload — a code fix alone won't show up in the
     browser until the server process is actually restarted. Worth
     ruling out first any time a fix "doesn't seem to work.")

  2. "second image is my same message it does not look like the third
     image" (a duplicated "What's new (click.base44.com)" line) —
     traced to connectors_gmail._clean_body_text now deliberately
     collapsing an EXACT, ADJACENT duplicate line/paragraph, matching the
     "hidden preheader" marketing-template pattern where a teaser
     sentence is CSS-hidden in the HTML version (so real Gmail's render
     never shows it) but has no CSS to hide it in the plain-text
     alternative Pilant reads.

This file covers (2) directly (connectors_gmail is fully unit-testable
with no network) and (1) structurally, by checking studio.py's actual
generated link text — the fastest way to keep the entry points honest
without spinning up a live Gmail-backed Flask request.
"""

import sys
sys.path.insert(0, "/home/claude/pilant-agent")

from connectors_gmail import _clean_body_text, _dedupe_adjacent_repeats, FOLDERS


def test_style_and_script_content_stripped_not_just_tags():
    """A real bug the user caught live 2026-08-27: a Revolut/MJML-built
    marketing email's "plain text" alternative was actually its HTML
    source, and its raw CSS (@font-face, @import, media queries) showed
    up as literal visible text in the message view, because _TAG_RE only
    strips <style>/<script> TAGS — the text between them survived
    untouched. This must now strip the whole block, tag and content."""
    raw = (
        "<style>\n"
        "@font-face { font-family: 'Aeonik Pro'; src: url('cdn.revolut.com') format('woff2'); }\n"
        "@import url(fonts.googleapis.com);\n"
        ".mj-column-per-100 { width:100% !important; }\n"
        "</style>\n"
        "<script>console.log('tracking');</script>\n"
        "<p>Hi Kowsick,</p>\n"
        "<p>Your account is now active.</p>"
    )
    out = _clean_body_text(raw)
    assert "font-face" not in out
    assert "mj-column" not in out
    assert "@import" not in out
    assert "console.log" not in out
    assert "Hi Kowsick," in out
    assert "Your account is now active." in out


def test_inbox_folder_scopes_to_primary_category():
    """FOLDERS["inbox"] must match what Gmail's own tabbed inbox shows by
    default (the Primary tab), not just the bare Inbox label — otherwise
    Promotions/Updates/Social mail leaks into what's supposed to be "my
    inbox", same as it did before this fix."""
    assert FOLDERS["inbox"] == "in:inbox category:primary"


def test_adjacent_paragraph_duplicate_collapsed():
    raw = "What's new (click.base44.com)\n\nWhat's new (click.base44.com)\n\nThis week's update..."
    out = _clean_body_text(raw)
    assert out.count("What's new (click.base44.com)") == 1
    assert "This week's update..." in out


def test_adjacent_line_duplicate_collapsed():
    raw = "Track your order: https://example.com/track\nTrack your order: https://example.com/track\nThanks."
    out = _clean_body_text(raw)
    assert out.count("Track your order:") == 1
    assert "Thanks." in out


def test_non_adjacent_repeat_not_touched():
    """A paragraph that legitimately recurs later in the message (e.g. a
    closing "Thanks!" that echoes an opening one) must survive — only a
    duplicate that immediately follows itself is a preheader echo."""
    raw = "Thanks!\n\nSee details below.\n\nThanks!"
    out = _clean_body_text(raw)
    assert out.count("Thanks!") == 2


def test_similar_but_not_identical_not_touched():
    """Two paragraphs that merely share a prefix are two different
    sentences, not a duplicate — must never be merged/dropped."""
    raw = "What's new\n\nWhat's new this week: three big updates shipped."
    out = _clean_body_text(raw)
    assert "What's new this week: three big updates shipped." in out
    assert out.count("What's new") == 2  # once standalone, once as the real prefix


def test_dedupe_helper_handles_empty_and_single_block():
    assert _dedupe_adjacent_repeats("") == ""
    assert _dedupe_adjacent_repeats("just one line") == "just one line"


def test_studio_full_inbox_links_scope_to_inbox_folder():
    """Every link studio.py renders into the full Gmail inbox must pass
    folder=inbox explicitly now — a bare /inbox href silently lands on
    unscoped "All Mail", which is exactly bug (1) above."""
    with open("/home/claude/pilant-agent/studio.py") as f:
        src = f.read()
    assert 'href="/inbox"' not in src, "found a link back to unscoped All Mail"
    assert src.count('/inbox?folder=inbox') >= 4


def test_render_inbox_panel_scopes_to_inbox():
    """Superseded 2026-08-29 by the folder-tab strip (render_inbox_panel
    now takes a real `folder` argument instead of a single hardcoded
    "inbox" call) — rewritten from a source-text match to an actual
    behavioral check, since the literal call this used to grep for no
    longer exists verbatim. The property that matters is unchanged: no
    folder argument (or an invalid one) must still resolve to "inbox",
    never silently widen to unscoped "All Mail" — exactly the bug this
    test file exists to guard against."""
    import unittest.mock as mock
    import gmail_site

    calls = []

    def fake_get_gmail_messages(unread_only=False, limit=10, folder=None, query=None):
        calls.append(folder)
        return []

    with mock.patch("gmail_site.get_gmail_messages", side_effect=fake_get_gmail_messages):
        gmail_site.render_inbox_panel(next_url="/studio")  # no folder passed at all
        gmail_site.render_inbox_panel(next_url="/studio", folder="not-a-real-folder")  # tampered
        gmail_site.render_inbox_panel(next_url="/studio", folder="sent")  # a real, explicit choice

    assert calls == ["inbox", "inbox", "sent"], calls


if __name__ == "__main__":
    test_style_and_script_content_stripped_not_just_tags()
    test_inbox_folder_scopes_to_primary_category()
    test_adjacent_paragraph_duplicate_collapsed()
    test_adjacent_line_duplicate_collapsed()
    test_non_adjacent_repeat_not_touched()
    test_similar_but_not_identical_not_touched()
    test_dedupe_helper_handles_empty_and_single_block()
    test_studio_full_inbox_links_scope_to_inbox_folder()
    test_render_inbox_panel_scopes_to_inbox()
    print("all gmail inbox-scope/dedupe tests passed")
