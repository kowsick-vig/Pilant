"""
A real connector — reads live messages from a Gmail inbox via Google's
Gmail API. Same role as connectors_slack.py and connectors_github.py:
proves the agent can build a screen from data it never saw before.

Requires a Google OAuth2 refresh token — Gmail doesn't support a simple
static bearer token the way GitHub or Slack do, so the one-time setup is a
bit more involved:

  1. In Google Cloud Console (console.cloud.google.com), create a project,
     enable the "Gmail API" (APIs & Services -> Library), then create an
     OAuth 2.0 Client ID (APIs & Services -> Credentials -> Create
     Credentials -> OAuth client ID -> Desktop app). Note the Client ID and
     Client Secret.
  2. Get a one-time refresh token for your own inbox using Google's OAuth
     Playground (developers.google.com/oauthplayground): gear icon (top
     right) -> check "Use your own OAuth credentials" -> paste your Client
     ID/Secret -> in the scopes list on the left, enter
     https://www.googleapis.com/auth/gmail.readonly -> Authorize APIs ->
     sign in with the Gmail account you want to read -> Exchange
     authorization code for tokens -> copy the Refresh token shown.
  3. Set GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN in
     .env from those two steps. This connector exchanges the refresh token
     for a short-lived access token itself (cached in memory, same pattern
     as connectors_healthcare.py's Epic token handling) — you never need to
     touch access tokens directly.

Scope note, the honest kind: gmail.readonly is genuinely read-only, and
that's still all agent_gmail.py (the CLI) and everything above needs —
get_gmail_messages/get_message_full/get_account_info never write anything.

send_reply(), added 2026-08-24 for gmail_site.py's reply feature, is
different: it's a real, deliberate write action, and needs a real,
deliberate scope increase — GMAIL_REFRESH_TOKEN must be re-authorized via
the OAuth Playground with BOTH scopes present (readonly is still needed
for send_reply() to look up the message it's replying to):

  https://www.googleapis.com/auth/gmail.readonly
  https://www.googleapis.com/auth/gmail.send

send_email()/forward_message() (added 2026-08-26 for gmail_site.py's
Compose and Forward features) reuse this same gmail.send scope — no
further re-authorization needed if send_reply() already works.

trash_message()/untrash_message()/modify_labels() (and the
star_message()/mark_important()/mark_read() convenience wrappers around
modify_labels(), also added 2026-08-26) are a THIRD, separate scope
increase — Google doesn't fold delete/label-modify permission into
gmail.send, and gmail.readonly explicitly forbids it. Re-authorize
GMAIL_REFRESH_TOKEN once more via the OAuth Playground with:

  https://www.googleapis.com/auth/gmail.modify

added alongside the two scopes above (gmail.modify does not include
gmail.send, so keep all three). Until that's done, Delete/Star/Mark
unread/Mark important in gmail_site.py will fail with a 403, same "clear
message pointing at the scope issue" pattern used for send_reply().

A token issued with only gmail.readonly will fail send_reply() with a 403
— that's Google correctly enforcing the scope you actually authorized, not
a bug here. This is the honest reason gmail.send isn't just baked into the
original setup instructions above: most of this connector never needed it,
and asking for send access up front for a read-only demo would have been
the wrong default.

Capability note, the positive kind, worth contrasting with
connectors_slack.py: Gmail's search (the `query` parameter below) is REAL
Gmail search — the same operators you'd type in the Gmail search bar
(is:unread, from:, subject:, newer_than:7d, has:attachment, and so on),
not a client-side substring filter. And "unread" is a real, first-class
concept here (the UNREAD label), unlike Slack, which doesn't expose a
per-person unread state to a bot. Two different real systems, two
genuinely different capability sets — this file doesn't try to paper over
that difference.

GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN are read from
os.environ lazily, inside the functions below, same reasoning as the other
connectors — it shouldn't matter whether the caller loads .env before or
after importing this module.
"""

import base64
import email.mime.text
import email.utils
import html as _htmlmod
import os
import json
import re
import time
import unicodedata
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

# load .env manually (no extra dependency) — same pattern as connectors_healthcare.py
# and connectors_retail.py, so `python3 connectors_gmail.py` works standalone for the
# quick manual check below, not just when run via agent_gmail.py (which loads .env itself).
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API_ROOT = "https://gmail.googleapis.com/gmail/v1/users/me"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Cached like connectors_healthcare.py's Epic access token: re-fetched
# automatically once it's within 60s of expiring or hasn't been fetched yet
# (e.g. after a process restart).
_token_cache = {"token": None, "expires_at": 0}

# Added 2026-08-25 for Studio's real "Connect Gmail" flow (studio.py's
# /oauth/gmail/connect + /oauth/gmail/callback routes, via
# get_authorization_url/exchange_code_for_tokens below): an in-memory
# override for which real Gmail account this connector talks to. Whoever
# most recently completed the real Google sign-in through Studio's
# Integrations page wins — this takes priority over the static
# GMAIL_REFRESH_TOKEN in .env whenever it's set, so a person can swap which
# inbox is connected (e.g. "connect my other Gmail") just by signing in
# again, no .env edit or restart required. Still just one active
# connection for the whole process, same single-shared-inbox model as the
# rest of this file — signing in again REPLACES the previous connection,
# it doesn't add a second one.
_connected = {"refresh_token": None, "email": None}


def get_authorization_url(redirect_uri, state=None):
    """Builds the real Google OAuth consent-screen URL for Studio's
    "Connect Gmail" button to redirect the browser to. `redirect_uri` must
    be byte-identical between this call and the later
    exchange_code_for_tokens() call for the same sign-in — Google checks
    that. `state` is an opaque CSRF token the caller should generate,
    stash in session, and verify against the callback's ?state= before
    calling exchange_code_for_tokens(); Google echoes it back unchanged.

    access_type=offline + prompt=consent together are what make Google
    actually issue a refresh_token on THIS sign-in rather than silently
    reusing (or omitting) one from a previous authorization — without
    prompt=consent, re-connecting with an account that already granted
    this app access before can come back with no refresh_token at all,
    which is exactly the case exchange_code_for_tokens() below treats as a
    hard failure."""
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError(
            "GMAIL_CLIENT_ID must be set in .env before a Gmail account can be connected. "
            "See connectors_gmail.py's module docstring for the one-time Google Cloud Console setup."
        )
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    if state:
        params["state"] = state
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(code, redirect_uri):
    """Exchanges a real Google OAuth authorization code (the ?code=... query
    param Google's callback redirect carries) for tokens, and if that
    succeeds, makes this the connector's ACTIVE connected account (see
    _connected above) — replacing whichever account, if any, was connected
    before. Returns the real Gmail address now connected. Raises
    RuntimeError with Google's own error detail on any failure (expired/
    reused code, redirect_uri mismatch, no refresh token issued, ...) —
    studio.py's callback route is expected to catch this and show it as a
    plain error rather than a stack trace, same pattern as run_agent()'s
    own error handling elsewhere in this project."""
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            "GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must both be set in .env before a Gmail "
            "account can be connected."
        )

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Google OAuth sign-in failed ({e.code}): "
            f"{e.read().decode('utf-8', errors='replace')[:300]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Google's OAuth token endpoint: {e.reason}") from e

    access_token = body.get("access_token")
    refresh_token = body.get("refresh_token")
    if not access_token:
        raise RuntimeError(f"Google OAuth sign-in returned no access_token: {body}")
    if not refresh_token:
        raise RuntimeError(
            "Google didn't return a refresh token for this sign-in, so this connection "
            "wouldn't survive past the next hour. This can happen if Google decides consent "
            "isn't needed again for this app/account combo — try removing this app's access at "
            "https://myaccount.google.com/permissions first, then connect again."
        )

    # Look up which real address this access token belongs to using THIS
    # fresh token directly, deliberately bypassing _get_access_token()'s
    # cache below — that cache may currently hold a token for the account
    # that was connected before this call, not the new one.
    req2 = urllib.request.Request(
        f"{API_ROOT}/profile", headers={"Authorization": f"Bearer {access_token}"}
    )
    try:
        with urllib.request.urlopen(req2, timeout=15) as resp:
            profile = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Signed in, but couldn't confirm which Gmail address ({e.code}): "
            f"{e.read().decode('utf-8', errors='replace')[:300]}"
        ) from e
    email_address = profile.get("emailAddress")

    _connected["refresh_token"] = refresh_token
    _connected["email"] = email_address
    # Drop any cached access token from whichever account was active before
    # — otherwise the very next real call would keep serving the OLD
    # account's still-valid access token for up to an hour instead of
    # switching over immediately.
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0
    return email_address


def get_connected_account():
    """The real Gmail address this connector is currently pointed at, if it
    was connected through the real OAuth sign-in flow above — None if this
    process is instead (still) running on the static GMAIL_REFRESH_TOKEN
    from .env (which address that is isn't known until the first real API
    call, so this deliberately doesn't guess)."""
    return _connected.get("email")


def disconnect():
    """Drops the live-signed-in account, if any. Falls back to the static
    GMAIL_REFRESH_TOKEN in .env on the next call, same as if no one had
    ever connected through the OAuth flow — does NOT revoke the token on
    Google's side (that's a separate, deliberate action a person can take
    at https://myaccount.google.com/permissions if they want this app's
    access actually revoked, not just unselected here)."""
    _connected["refresh_token"] = None
    _connected["email"] = None
    _token_cache["token"] = None
    _token_cache["expires_at"] = 0


def _get_access_token():
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]

    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    # A live OAuth sign-in (via exchange_code_for_tokens above) wins over
    # the static .env token — see _connected's own comment for why.
    refresh_token = _connected.get("refresh_token") or os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError(
            "GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, and GMAIL_REFRESH_TOKEN must all be set "
            "in .env (or a Gmail account connected live through Studio's Integrations page). "
            "See connectors_gmail.py's module docstring for the one-time setup via "
            "Google Cloud Console and the OAuth Playground."
        )

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Google OAuth token refresh failed ({e.code}): "
            f"{e.read().decode('utf-8', errors='replace')[:300]} — the refresh token may have "
            "been revoked or expired; generate a new one via the OAuth Playground."
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Google's OAuth token endpoint: {e.reason}") from e

    token = body.get("access_token")
    if not token:
        raise RuntimeError(f"Google OAuth token refresh returned no access_token: {body}")

    _token_cache["token"] = token
    _token_cache["expires_at"] = time.time() + int(body.get("expires_in", 3600))
    return token


def _get(path, params=None):
    token = _get_access_token()
    url = f"{API_ROOT}{path}"
    if params:
        # doseq=True matters here: get_gmail_messages passes metadataHeaders as a real
        # Python list (["Subject", "From", "Date"]) so Gmail returns each header we
        # actually want. Without doseq, urlencode stringifies the whole list into ONE
        # value — "metadataHeaders=%5B%27Subject%27%2C+%27From%27%2C+%27Date%27%5D" — which
        # Gmail doesn't recognize as any real header name, so it silently matches nothing
        # and every message comes back with from/subject/date blank (snippet is unaffected
        # since it's returned unconditionally, not gated by metadataHeaders — which is
        # exactly the split symptom this was causing). doseq=True encodes a list value as
        # repeated params instead: metadataHeaders=Subject&metadataHeaders=From&... .
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None}, doseq=True)
        if query:
            url = f"{url}?{query}"

    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Gmail API error {e.code} for {path}: "
            f"{e.read().decode('utf-8', errors='replace')[:300]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the Gmail API: {e.reason}") from e


def _post(path, payload):
    token = _get_access_token()
    url = f"{API_ROOT}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Gmail API error {e.code} for POST {path}: "
            f"{e.read().decode('utf-8', errors='replace')[:300]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the Gmail API: {e.reason}") from e


def _header(headers, name):
    for h in headers or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


# Gmail's "snippet" field is supposed to be a short preview, but some real
# messages — observed live 2026-08-24, an onboarding/marketing email — pad
# it with hundreds of invisible zero-width-joiner (U+200D) / combining-mark
# (e.g. U+034F COMBINING GRAPHEME JOINER) characters, likely to defeat
# spam/preview filters. Invisible to a person reading it, but not to
# anything downstream that has to handle or echo the raw string — an LLM
# composing a screen from it burns its entire token budget on invisible
# characters and never finishes. Strip Unicode format (Cf) and nonspacing
# mark (Mn) categories, then hard-cap the result so no single
# malformed/adversarial message can dominate a request regardless of cause.
_MAX_SNIPPET_CHARS = 240


def _clean_snippet(snippet):
    if not snippet:
        return snippet
    # For messages that originated as HTML-only (most marketing/notification mail), Gmail's
    # snippet sometimes carries un-decoded HTML entities as literal text — "&#39;s been a
    # while" instead of "'s been a while" — because the snippet is extracted from the HTML
    # body's text before entity decoding. unescape() first so the actual DATA is clean text;
    # every renderer's own _esc()/html.escape() already re-escapes it correctly for safe
    # embedding, same as _extract_body_text's text/html fallback below already does.
    snippet = _htmlmod.unescape(snippet)
    cleaned = "".join(ch for ch in snippet if unicodedata.category(ch) not in ("Cf", "Mn"))
    cleaned = " ".join(cleaned.split())  # collapse whitespace left behind by the strip
    if len(cleaned) > _MAX_SNIPPET_CHARS:
        cleaned = cleaned[:_MAX_SNIPPET_CHARS].rstrip() + "…"
    return cleaned


# Added 2026-08-26 at the user's request ("checking spam, sent messages...
# all the inbuilt which gmail offers"): the built-in Gmail folders/views,
# each mapped to the exact real Gmail search operator that scopes to it.
# Gmail's messages.list already excludes SPAM and TRASH by default from a
# plain/empty query — these `in:`/`is:` terms are what actually reaches
# them, same as typing them into Gmail's own search bar. Any OTHER Gmail
# label (a custom one a person created) still works too, just not through
# this fixed set — pass query="label:<name>" instead, same as any other
# real Gmail search term.
#
# "inbox" corrected 2026-08-27: was just "in:inbox", which technically
# means "everything with the Inbox label" — but that is NOT what a real
# Gmail account with the tabbed inbox (Primary/Promotions/Social/Updates —
# the default for most consumer accounts) actually shows when you open
# Gmail. The visible default view is the Primary tab alone; typing
# "in:inbox" into Gmail's own search bar returns every tab's mail
# combined, which is why Pilant's "inbox" was surfacing Promotions/Updates
# mail (newsletters, offers, job alerts) ahead of what the user's real
# inbox showed. "category:primary" is the real Gmail search operator the
# Primary tab itself is built on — combining it with "in:inbox" is exactly
# what a person sees by default. (Accounts that have turned category tabs
# off don't apply CATEGORY_PERSONAL labels, so this would under-return for
# them — not something to guess around without a live account to check;
# flag it if it ever comes up.)
FOLDERS = {
    "inbox": "in:inbox category:primary",
    "sent": "in:sent",
    "spam": "in:spam",
    "drafts": "in:drafts",
    "trash": "in:trash",
    "starred": "is:starred",
    "important": "is:important",
}


def get_gmail_messages(query=None, unread_only=False, limit=10, folder=None):
    """
    Fetch real recent messages from the connected Gmail inbox, most recent
    first. `query` is REAL Gmail search syntax (e.g. "from:boss@company.com",
    "subject:invoice", "newer_than:3d") — passed straight through to
    Gmail's own search, not a client-side filter. `unread_only`, if true,
    restricts to messages carrying Gmail's real UNREAD label (prepended to
    `query` as "is:unread"). `folder`, if given, must be one of FOLDERS'
    keys ("inbox", "sent", "spam", "drafts", "trash", "starred",
    "important") and scopes the search to that real Gmail view — combines
    with `unread_only`/`query` rather than replacing them (e.g.
    folder="spam", unread_only=True finds unread spam). `limit` caps how
    many messages come back (default 10, max 50 — kept small since each one
    costs a second API call to fetch its headers/snippet).

    Returns a simplified list: who it's from, who it's to, the subject,
    when, a snippet of the body, and whether it's genuinely unread. `to` is
    mainly useful for the sent/drafts folders, where `from` is just the
    connected account's own address — everywhere else it's a fine second
    field to have but rarely the interesting one.

    Raises RuntimeError on an unrecognized `folder` value — same "fail
    loud with a clear message" policy every other real config problem in
    this file uses, rather than silently ignoring a typo'd folder name.
    """
    limit = min(max(int(limit or 10), 1), 50)
    q_parts = []
    if folder:
        folder_key = str(folder).strip().lower()
        folder_term = FOLDERS.get(folder_key)
        if folder_term is None:
            raise RuntimeError(
                f"Unknown folder '{folder}'. Known folders: {', '.join(FOLDERS)}. "
                'For any other Gmail label, use query="label:<name>" instead.'
            )
        q_parts.append(folder_term)
    if unread_only:
        q_parts.append("is:unread")
    if query:
        q_parts.append(query)
    q = " ".join(q_parts) if q_parts else None

    listing = _get("/messages", {"q": q, "maxResults": limit})
    refs = listing.get("messages", []) or []

    messages = []
    for ref in refs:
        detail = _get(f"/messages/{ref['id']}", {
            "format": "metadata",
            "metadataHeaders": ["Subject", "From", "To", "Date"],
        })
        headers = (detail.get("payload") or {}).get("headers", [])
        label_ids = detail.get("labelIds") or []
        messages.append({
            "id": ref["id"],
            "from": _header(headers, "From"),
            "to": _header(headers, "To"),
            "subject": _header(headers, "Subject") or "(no subject)",
            "date": _header(headers, "Date"),
            "snippet": _clean_snippet(detail.get("snippet", "")),
            "unread": "UNREAD" in label_ids,
            # Added 2026-08-26 alongside star_message()/mark_important() below —
            # the list view needs to know current state to render a filled vs.
            # outline star/important icon, not just to be able to change it.
            "starred": "STARRED" in label_ids,
            "important": "IMPORTANT" in label_ids,
        })
    return messages


def _display_name(header_value):
    """Extract just the display name from a raw From/To header value
    ("Revolut <no-reply@revolut.com>" -> "Revolut"), falling back to the
    bare email address when there's no display name. Added 2026-08-27 for
    get_gmail_threads() below, which needs a real Gmail-style "Alice, Bob"
    joined sender list for a conversation without dragging full email
    addresses into it. get_gmail_messages()/get_message_full() deliberately
    keep the raw header everywhere else — that's the real, correct data,
    and other callers may want the full form (a reply needs the actual
    address, not just a display name)."""
    if not header_value:
        return ""
    match = re.match(r'^\s*"?([^"<]*?)"?\s*<[^>]+>\s*$', header_value)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return header_value.strip()


def get_gmail_threads(query=None, unread_only=False, limit=10, folder=None):
    """
    Fetch real conversations — Gmail's own thread grouping — from the
    connected inbox, most recent first. The thread-level counterpart to
    get_gmail_messages() above, added 2026-08-27 to match real Gmail's
    actual inbox behavior: several messages sharing a conversation (three
    separate Revolut emails, a back-and-forth with a colleague) collapse
    into ONE row with a count — "Revolut (3)" — not three separate rows,
    which is what get_gmail_messages()'s flat per-message list produces.

    Deliberately built on Gmail's real /threads endpoint rather than
    grouping get_gmail_messages()'s results after the fact. Post-hoc
    grouping would only ever group whatever happened to land inside one
    page of `limit` individual MESSAGES — silently undercounting any
    thread whose other messages fell outside that page, or showing a
    thread's count as 1 when its other messages just didn't make the cut.
    /threads already returns one row per real conversation, respecting
    the exact same q= scoping (folder + search, via the same FOLDERS
    mapping) as get_gmail_messages — so `limit` here genuinely means
    `limit` conversations, matching what a person actually sees paging
    through their real Gmail inbox.

    Same `query`/`unread_only`/`folder` semantics and error behavior as
    get_gmail_messages. Each returned thread carries `message_ids` (every
    real message id in it, oldest first) so gmail_site.py's thread-detail
    view can open any of them.
    """
    limit = min(max(int(limit or 10), 1), 50)
    q_parts = []
    if folder:
        folder_key = str(folder).strip().lower()
        folder_term = FOLDERS.get(folder_key)
        if folder_term is None:
            raise RuntimeError(
                f"Unknown folder '{folder}'. Known folders: {', '.join(FOLDERS)}. "
                'For any other Gmail label, use query="label:<name>" instead.'
            )
        q_parts.append(folder_term)
    if unread_only:
        q_parts.append("is:unread")
    if query:
        q_parts.append(query)
    q = " ".join(q_parts) if q_parts else None

    listing = _get("/threads", {"q": q, "maxResults": limit})
    refs = listing.get("threads", []) or []

    threads = []
    for ref in refs:
        detail = _get(f"/threads/{ref['id']}", {
            "format": "metadata",
            "metadataHeaders": ["Subject", "From", "To", "Date"],
        })
        messages = detail.get("messages") or []
        if not messages:
            continue
        first_headers = (messages[0].get("payload") or {}).get("headers", [])
        last = messages[-1]
        last_headers = (last.get("payload") or {}).get("headers", [])

        # Real Gmail's thread row joins every unique sender across the
        # whole conversation (in the order they first appear) — "Alice,
        # Bob" for a back-and-forth, just "Revolut" when every message in
        # it is from the same sender, which is the common case for a
        # marketing/notification thread.
        senders = []
        seen = set()
        for m in messages:
            name = _display_name(_header((m.get("payload") or {}).get("headers", []), "From"))
            if name and name not in seen:
                seen.add(name)
                senders.append(name)

        label_ids_all = set()
        for m in messages:
            label_ids_all.update(m.get("labelIds") or [])

        threads.append({
            "id": ref["id"],
            "subject": _header(first_headers, "Subject") or "(no subject)",
            "from": ", ".join(senders) if senders else _header(last_headers, "From"),
            # "to" added so gmail_site.py's thread row can show the real
            # recipient for Sent/Drafts (where "from" is just the
            # connected account's own address, never useful there) —
            # same reasoning get_gmail_messages()'s per-message "to" has
            # always used, applied at the thread level via the most
            # recent message's To header.
            "to": _header(last_headers, "To"),
            "date": _header(last_headers, "Date"),
            "snippet": _clean_snippet(last.get("snippet", "")),
            "count": len(messages),
            "message_ids": [m["id"] for m in messages],
            "unread": "UNREAD" in label_ids_all,
            "starred": "STARRED" in label_ids_all,
            "important": "IMPORTANT" in label_ids_all,
        })
    return threads


def _b64url_decode(data):
    """Gmail's message body data is base64url (RFC 4648 §5), which stdlib's
    base64.urlsafe_b64decode wants padded to a multiple of 4 — Gmail's API
    omits the padding, so add it back before decoding."""
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _b64url_decode_bytes(data):
    """Same base64url padding fix as _b64url_decode, but returns raw bytes
    instead of decoding as UTF-8 text — for get_attachment() below, where
    the content is a real PDF/image/etc., not text."""
    if not data:
        return b""
    padded = data + "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except Exception:
        return b""


def _walk_parts(payload, mime_type):
    """Depth-first search through a (possibly multipart, possibly nested —
    Gmail messages commonly are, e.g. multipart/alternative inside
    multipart/mixed for an attachment) MIME payload for the first part
    matching mime_type. Returns its decoded text, or None if not found."""
    if not payload:
        return None
    if payload.get("mimeType") == mime_type:
        data = (payload.get("body") or {}).get("data")
        if data:
            return _b64url_decode(data)
    for part in payload.get("parts") or []:
        found = _walk_parts(part, mime_type)
        if found is not None:
            return found
    return None


def _walk_attachments(payload):
    """Depth-first walk collecting every real attachment in a message —
    Gmail's own signal that a MIME part IS an attachment (rather than a
    body part) is a non-empty `filename`. Recurses the same way
    _walk_parts does, since a real message can nest multipart/mixed
    (attachments) inside multipart/alternative (the two body versions) or
    the other way round depending on what sent it — the Revolut email
    above (4 PDF attachments) nests this way. Returns metadata only —
    filename/type/size/attachment_id — never the attachment bytes
    themselves; get_attachment() below fetches one attachment's actual
    content on demand, only when someone asks to download it, not
    speculatively for every message in a list."""
    found = []
    if not payload:
        return found
    filename = payload.get("filename")
    if filename:
        body = payload.get("body") or {}
        attachment_id = body.get("attachmentId")
        if attachment_id:
            found.append({
                "filename": filename,
                "mime_type": payload.get("mimeType") or "application/octet-stream",
                "size": body.get("size") or 0,
                "attachment_id": attachment_id,
            })
    for part in payload.get("parts") or []:
        found.extend(_walk_attachments(part))
    return found


_TAG_RE = re.compile(r"<[^>]+>")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Added 2026-08-27: observed live on a Revolut/MJML-built marketing email
# (the "mj-column-per-100" class name is MJML's own naming convention) —
# its "plain text" alternative was actually its HTML source (the same
# quirk _clean_body_text's docstring already documents), and its raw CSS
# — @font-face blocks, @import, media queries — showed up as literal
# visible text in Pilant's message view. _TAG_RE only strips the <style>
# and <script> TAGS themselves; it was never designed to strip what's
# BETWEEN a <style>...</style> or <script>...</script> pair, since that's
# plain text as far as that regex is concerned, not a tag. Real browsers
# never render that text because they know style/script content isn't
# document text — Pilant's tag-stripping approach has no such concept, so
# this removes the whole block (tag + everything inside it) before
# _TAG_RE ever sees it, same as _HTML_COMMENT_RE already does for
# comments.
_STYLE_SCRIPT_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
_MAX_BODY_CHARS = 20000


def _dedupe_adjacent_repeats(text):
    """Marketing-template quirk observed live 2026-08-27 (a Base44 "What's
    new" newsletter, flagged by the user as a rendered message not
    matching real Gmail): these templates often carry a hidden "preheader"
    teaser sentence at the very top of the email — the preview text Gmail
    shows next to the subject in the inbox list — hidden via CSS
    (display:none) in the HTML version, so real Gmail's own render never
    shows it. The plain-text alternative this file extracts has no CSS to
    hide anything with, so that exact sentence (often with the exact same
    link) shows up twice, back-to-back: once as the hidden preheader, once
    as the real opening line/paragraph.

    This collapses an EXACT duplicate that immediately follows itself, at
    both line and paragraph granularity. Deliberately narrow — adjacent
    only, exact-match only — so it can't eat a legitimately repeated word
    or a coincidentally similar sentence elsewhere in a long email; it
    only removes the specific back-to-back "echo" this bug is about, not
    anything that merely looks similar."""
    lines = text.split("\n")
    deduped_lines = []
    for line in lines:
        if deduped_lines and line.strip() and line.strip() == deduped_lines[-1].strip():
            continue
        deduped_lines.append(line)
    text = "\n".join(deduped_lines)

    blocks = re.split(r"\n\n+", text)
    deduped_blocks = []
    for block in blocks:
        if deduped_blocks and block.strip() and block.strip() == deduped_blocks[-1].strip():
            continue
        deduped_blocks.append(block)
    return "\n\n".join(deduped_blocks)


def _clean_body_text(text):
    """Shared cleanup for whatever text _extract_body_text finds, regardless
    of which MIME part it came from. Originally this only ran on the
    text/html fallback path — real text/plain was trusted as-is. Observed
    live 2026-08-24: some marketing templates' "plain text" alternative
    isn't actually plain — it's their HTML source with Outlook conditional
    comments (<!--[if !mso]><!-->) left in, badly auto-stripped. _TAG_RE
    alone doesn't catch those: a conditional comment like <!--[if !mso]> has
    no `-->` before its `>`, so it needs the dedicated comment pattern first
    (DOTALL, so a comment spanning multiple lines is still matched as one
    unit) — otherwise its shorter fragments end up matching _TAG_RE
    separately and unpredictably. _STYLE_SCRIPT_RE runs first, for the
    same reason but for <style>/<script> block CONTENT rather than tags
    or comments — see its own comment above for the live case that
    exposed it (a template's CSS rendering as literal message text).
    Runs on every source now, not just HTML,
    since trusting "the MIME type says text/plain" turned out not to be
    enough — and it's harmless on genuinely clean plain text, which simply
    has nothing for either pattern to match. Preserves paragraph breaks
    (collapsing only 3+ blank lines down to one) rather than flattening
    everything to a single line, since gmail_site.py displays this with
    real line breaks."""
    if not text:
        return text
    cleaned = _STYLE_SCRIPT_RE.sub(" ", text)
    cleaned = _HTML_COMMENT_RE.sub(" ", cleaned)
    cleaned = _TAG_RE.sub(" ", cleaned)
    cleaned = _htmlmod.unescape(cleaned)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n[ \t]*(\n[ \t]*)+", "\n\n", cleaned)
    cleaned = "\n".join(line.strip() for line in cleaned.split("\n"))
    cleaned = _dedupe_adjacent_repeats(cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) > _MAX_BODY_CHARS:
        cleaned = cleaned[:_MAX_BODY_CHARS].rstrip() + "\n\n… (truncated — this message is unusually long)"
    return cleaned


def _extract_body_text(payload):
    """Prefer the real text/plain part; fall back to text/html (most
    marketing/notification mail only ships that) with the same cleanup —
    good enough to show a person the gist and let an LLM draft a reply from
    it, not a real HTML renderer. Last resort: a non-multipart message's
    body sits directly on the top-level payload, not in `parts` at all."""
    text = _walk_parts(payload, "text/plain")
    if text is not None:
        return _clean_body_text(text)
    html_text = _walk_parts(payload, "text/html")
    if html_text is not None:
        return _clean_body_text(html_text)
    data = (payload.get("body") or {}).get("data") if payload else None
    if data:
        return _clean_body_text(_b64url_decode(data))
    return ""


def _message_dict_from_raw(raw_message):
    """Builds the common message shape (id/threadId/from/to/subject/date/
    body/attachments/unread/starred/important) both get_message_full()
    and get_thread_full() below return, from one raw Gmail message
    resource — the {id, threadId, labelIds, payload: {headers, ...}}
    shape Gmail's API returns both for a single messages.get(format=full)
    and for each entry in threads.get(format=full)'s "messages" array.
    Factored out 2026-08-27 when get_thread_full() was added, so the two
    callers can't quietly drift out of sync on what fields a "message"
    has — before this, get_message_full() built this dict inline."""
    payload = raw_message.get("payload") or {}
    headers = payload.get("headers", [])
    label_ids = raw_message.get("labelIds") or []
    return {
        "id": raw_message.get("id"),
        "threadId": raw_message.get("threadId"),
        "from": _header(headers, "From"),
        "to": _header(headers, "To"),
        "subject": _header(headers, "Subject") or "(no subject)",
        "date": _header(headers, "Date"),
        "message_id_header": _header(headers, "Message-ID"),
        "references": _header(headers, "References"),
        "body": _extract_body_text(payload),
        "attachments": _walk_attachments(payload),
        "unread": "UNREAD" in label_ids,
        "starred": "STARRED" in label_ids,
        "important": "IMPORTANT" in label_ids,
    }


def get_message_full(message_id):
    """
    Fetch one real message's full content — for the reply interface, which
    needs the actual body (not just the snippet get_gmail_messages returns)
    plus the real headers needed to thread a reply correctly (Message-ID,
    References, and Gmail's own threadId).

    `attachments` added 2026-08-27 at the user's request ("what about
    attachments"), after the Revolut message-view comparison showed real
    Gmail's "4 Attachments" strip with nothing corresponding to it on
    Pilant's side — not a rendering bug, this metadata was simply never
    being read before now. Each entry is real metadata only
    (filename/mime_type/size/attachment_id); see get_attachment() below
    for fetching one's actual bytes on demand.
    """
    detail = _get(f"/messages/{message_id}", {"format": "full"})
    return _message_dict_from_raw(detail)


def get_thread_full(thread_id):
    """
    Fetch an entire real conversation's full content — every message's
    actual body, attachments, and headers, not just the metadata
    get_gmail_threads() above uses for the list view — in ONE Gmail API
    call. Added 2026-08-27 alongside get_gmail_threads() for
    gmail_site.py's thread-detail page (opened by clicking a conversation
    row), which needs every message's real content, not just who/when/
    snippet. Reuses _message_dict_from_raw() for each entry in Gmail's
    own threads.get(format=full) "messages" array — one real API call for
    the whole conversation, not N separate calls to get_message_full().
    """
    detail = _get(f"/threads/{thread_id}", {"format": "full"})
    return {
        "id": detail.get("id"),
        "messages": [_message_dict_from_raw(m) for m in detail.get("messages") or []],
    }


def get_attachment(message_id, attachment_id):
    """
    Fetch one real attachment's actual bytes plus its filename/mime type,
    for gmail_site.py's download route — called only when someone clicks
    a specific attachment, never speculatively (attachments can be large;
    get_message_full()/_walk_attachments() above only ever fetch metadata
    for the list). Gmail's messages.attachments.get returns {size, data}
    — data is the same base64url encoding used everywhere else in this
    file, just decoded to raw bytes here (_b64url_decode_bytes) instead
    of UTF-8 text, since this is real binary content (a PDF, an image,
    ...), not something meant to be read as text.
    """
    detail = _get(f"/messages/{message_id}/attachments/{attachment_id}")
    return _b64url_decode_bytes(detail.get("data"))


def send_reply(message_id, body_text):
    """
    Sends a REAL reply through the connected Gmail account — the one
    genuinely write action this connector has (see this module's docstring
    for the separate gmail.send scope this requires, on top of
    gmail.readonly). Threads correctly: uses the original message's real
    Message-ID (In-Reply-To / References headers) and Gmail's threadId, so
    it lands in the same conversation instead of starting a new one.

    `body_text` is sent exactly as given — this function has no review
    step of its own. gmail_site.py's reply route is responsible for having
    a person confirm the text first; by the time this is called, sending
    is meant to actually happen.
    """
    original = get_message_full(message_id)
    to_addr = email.utils.parseaddr(original["from"])[1] or original["from"]
    subject = original["subject"]
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    msg = email.mime.text.MIMEText(body_text or "", "plain", "utf-8")
    msg["To"] = to_addr
    msg["Subject"] = subject
    if original["message_id_header"]:
        msg["In-Reply-To"] = original["message_id_header"]
        msg["References"] = (
            f"{original['references']} {original['message_id_header']}".strip()
            if original["references"] else original["message_id_header"]
        )

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    payload = {"raw": raw}
    if original["threadId"]:
        payload["threadId"] = original["threadId"]

    result = _post("/messages/send", payload)
    return {
        "id": result.get("id"),
        "threadId": result.get("threadId"),
        "to": to_addr,
        "subject": subject,
    }


def send_email(to, subject, body, cc=None, bcc=None):
    """
    Sends a brand-new email through the connected Gmail account — the real
    "compose and send" action, distinct from send_reply() above: no
    In-Reply-To/References/threadId, because this isn't a reply to
    anything, it's a new conversation. Same gmail.send scope as
    send_reply() (see this module's docstring); no further OAuth
    re-authorization needed if send_reply() already works.

    `to` is required and used exactly as given (a real address, or Gmail
    accepts comma-separated multiple addresses in one string — that's
    Gmail's own syntax, not something this function parses). `cc`/`bcc`
    are optional, same comma-separated-string convention. Like
    send_reply(), sends immediately with no review step of its own —
    gmail_site.py's compose route is responsible for a person confirming
    the text first via a real Send click.
    """
    to = (to or "").strip()
    if not to:
        raise RuntimeError("send_email requires a 'to' address.")
    msg = email.mime.text.MIMEText(body or "", "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    if cc:
        msg["Cc"] = cc
    if bcc:
        msg["Bcc"] = bcc
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    result = _post("/messages/send", {"raw": raw})
    return {
        "id": result.get("id"),
        "threadId": result.get("threadId"),
        "to": to,
        "subject": msg["Subject"],
    }


def forward_message(message_id, to, note="", cc=None):
    """
    Forwards a real message to someone new — fetches the original's real
    subject/body/headers and builds the classic
    "---------- Forwarded message ---------" quoted block Gmail itself
    uses, with `note` (optional) as the forwarder's own text above it. Not
    threaded to the original conversation via In-Reply-To/References —
    same as real Gmail, a forward is a new message to a (usually) new
    recipient, not a reply in the original thread.

    Uses the same gmail.send scope as send_email()/send_reply() — NOT
    gmail.modify, since sending isn't a label/delete operation.
    """
    to = (to or "").strip()
    if not to:
        raise RuntimeError("forward_message requires a 'to' address.")
    original = get_message_full(message_id)
    subject = original["subject"]
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"

    forwarded_block = (
        "---------- Forwarded message ---------\n"
        f"From: {original['from']}\n"
        f"Date: {original['date']}\n"
        f"Subject: {original['subject']}\n"
        f"To: {original['to']}\n\n"
        f"{original['body']}"
    )
    full_body = f"{note.rstrip()}\n\n{forwarded_block}" if (note or "").strip() else forwarded_block

    msg = email.mime.text.MIMEText(full_body, "plain", "utf-8")
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    result = _post("/messages/send", {"raw": raw})
    return {
        "id": result.get("id"),
        "threadId": result.get("threadId"),
        "to": to,
        "subject": subject,
    }


def trash_message(message_id):
    """
    Moves a message to Trash — real Gmail's actual "Delete" button
    behavior (recoverable for 30 days, same as clicking the trash icon in
    Gmail itself), NOT gmail.googleapis.com's separate permanent-delete
    endpoint, which this connector deliberately never calls — there's no
    "permanently delete" button in gmail_site.py, only this recoverable
    one, matching what a person clicking Delete in real Gmail actually
    gets. Requires the gmail.modify scope (see this module's docstring —
    a separate re-authorization from gmail.send).
    """
    result = _post(f"/messages/{message_id}/trash", {})
    return {"id": result.get("id"), "trashed": True}


def untrash_message(message_id):
    """Restores a message out of Trash back to wherever it was (Gmail
    remembers). Requires gmail.modify, same as trash_message()."""
    result = _post(f"/messages/{message_id}/untrash", {})
    return {"id": result.get("id"), "trashed": False}


def trash_thread(thread_id):
    """Moves an ENTIRE conversation to Trash in one real Gmail API call —
    added 2026-08-27 alongside get_gmail_threads(), for the thread-grouped
    list view's Delete action, matching real Gmail: clicking Delete on a
    conversation row trashes every message in it, not just the one most
    recently shown. Uses Gmail's own /threads/{id}/trash endpoint rather
    than looping trash_message() over every message id — one real API
    call instead of N, and it's what Gmail's own client does too."""
    result = _post(f"/threads/{thread_id}/trash", {})
    return {"id": result.get("id"), "trashed": True}


def untrash_thread(thread_id):
    """Restores an entire conversation out of Trash. Requires
    gmail.modify, same as trash_thread()."""
    result = _post(f"/threads/{thread_id}/untrash", {})
    return {"id": result.get("id"), "trashed": False}


def modify_labels(message_id, add=None, remove=None):
    """
    Adds/removes real Gmail labels on one message via the API's own
    /modify endpoint — the underlying primitive star_message(),
    mark_important(), and mark_read() below are all built on. Requires
    gmail.modify (see this module's docstring). `add`/`remove` are lists
    of Gmail label IDs (e.g. "STARRED", "IMPORTANT", "UNREAD") — exposed
    directly here in case a caller ever needs a label combination the
    convenience wrappers below don't cover; most callers should use those
    wrappers instead.
    """
    payload = {}
    if add:
        payload["addLabelIds"] = list(add)
    if remove:
        payload["removeLabelIds"] = list(remove)
    result = _post(f"/messages/{message_id}/modify", payload)
    return {"id": result.get("id"), "labelIds": result.get("labelIds", [])}


def modify_thread_labels(thread_id, add=None, remove=None):
    """Thread-level counterpart to modify_labels() — Gmail's real
    /threads/{id}/modify endpoint applies the label change to every
    message in the conversation in one call. Added 2026-08-27 alongside
    get_gmail_threads() for the thread-grouped list view's star action."""
    payload = {}
    if add:
        payload["addLabelIds"] = list(add)
    if remove:
        payload["removeLabelIds"] = list(remove)
    result = _post(f"/threads/{thread_id}/modify", payload)
    return {"id": result.get("id")}


def star_message(message_id, starred=True):
    """Toggles Gmail's real STARRED label on one message."""
    if starred:
        return modify_labels(message_id, add=["STARRED"])
    return modify_labels(message_id, remove=["STARRED"])


def star_thread(thread_id, starred=True):
    """Toggles Gmail's real STARRED label across an entire conversation —
    matches real Gmail: starring from the thread-grouped list view stars
    every message in it, not just the most recent one."""
    if starred:
        return modify_thread_labels(thread_id, add=["STARRED"])
    return modify_thread_labels(thread_id, remove=["STARRED"])


def mark_important(message_id, important=True):
    """Toggles Gmail's real IMPORTANT label on one message."""
    if important:
        return modify_labels(message_id, add=["IMPORTANT"])
    return modify_labels(message_id, remove=["IMPORTANT"])


def mark_read(message_id, read=True):
    """
    Toggles Gmail's real UNREAD label on one message — *removing* UNREAD
    marks it read (real Gmail's own representation: there's no separate
    "READ" label, the absence of UNREAD IS read), *adding* it back marks
    it unread again.
    """
    if read:
        return modify_labels(message_id, remove=["UNREAD"])
    return modify_labels(message_id, add=["UNREAD"])


def get_account_info():
    """
    Which real Gmail account this refresh token is actually authorized
    against, plus Gmail's own estimate of how many messages currently match
    is:unread — independent of `limit` in get_gmail_messages, which only
    caps how many full message bodies get fetched per call, not how many
    actually exist. Exists for exactly one reason: OAuth Playground
    authorizes whichever Google account happens to be signed in in that
    browser tab at the time, which silently may not be the inbox you meant
    to connect — this is the fast way to confirm (or catch) that mismatch
    without guessing.
    """
    profile = _get("/profile")
    unread_listing = _get("/messages", {"q": "is:unread", "maxResults": 1})
    return {
        "emailAddress": profile.get("emailAddress"),
        "messagesTotal": profile.get("messagesTotal"),
        "threadsTotal": profile.get("threadsTotal"),
        "unread_resultSizeEstimate": unread_listing.get("resultSizeEstimate"),
    }


if __name__ == "__main__":
    # Quick manual check: python3 connectors_gmail.py
    import sys
    configured = all(os.environ.get(k) for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"))
    print(f"Gmail credentials configured: {configured}", file=sys.stderr)
    print("Connected account:", file=sys.stderr)
    print(json.dumps(get_account_info(), indent=2), file=sys.stderr)
    print(json.dumps(get_gmail_messages(unread_only=True, limit=5), indent=2))
