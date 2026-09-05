"""
A real connector — reads live messages from a configured Slack channel via
Slack's Web API. Same role as connectors_github.py and
connectors_healthcare.py: proves the agent can build a screen from data it
never saw before, this time from Slack instead of GitHub issues or Epic
FHIR appointments.

Requires a Slack bot token (SLACK_BOT_TOKEN in .env, starts with "xoxb-")
from an app installed to the workspace, with at least the `channels:history`,
`channels:read`, and `users:read` scopes (add `groups:history`/
`groups:read` too if the channel is private). Also set SLACK_CHANNEL —
either a channel ID (e.g. "C0123456789") or a name starting with "#" (e.g.
"#general"); a name is resolved to an ID via conversations.list on each
call, which costs one extra request but avoids needing IDs looked up by
hand.

Honest scope limits, so nobody's surprised later: this only reads recent
channel history plus a simple client-side text filter — it does NOT do real
Slack search (search.messages requires a user token, not a bot token, so
it's out of reach for a bot-token-only setup like this one), and it does
NOT know what's "unread" for any particular person (that's a per-user
concept Slack exposes to its own client, not to bots). If either of those
turns out to matter, it's a real scope increase — a different kind of
Slack app auth — not a small tweak to this file.

SLACK_BOT_TOKEN / SLACK_CHANNEL are read from os.environ lazily, inside the
functions below, same reasoning as connectors_github.py: it shouldn't
matter whether the caller loads .env before or after importing this module.
"""

import os
import json
import urllib.request
import urllib.error
import urllib.parse

API_ROOT = "https://slack.com/api"


def _call(method, params=None):
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "SLACK_BOT_TOKEN is not set in .env. Create a Slack app, install it to your "
            "workspace, and add a line like SLACK_BOT_TOKEN=xoxb-... pointing at its bot token."
        )
    url = f"{API_ROOT}/{method}"
    data = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}).encode()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"Slack API HTTP error {e.code} calling {method}: "
            f"{e.read().decode('utf-8', errors='replace')[:300]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Slack API: {e.reason}") from e

    if not body.get("ok"):
        raise RuntimeError(f"Slack API error calling {method}: {body.get('error', 'unknown error')}")
    return body


def _resolve_channel_id(channel):
    """Accepts a channel ID already (starts with C/G/D, Slack's own ID
    prefixes) or a "#name" — looks up the ID for a name via
    conversations.list. Checks up to 5 pages (1000 channels), which covers
    almost every real workspace this demo would run against."""
    if not channel:
        raise RuntimeError(
            "SLACK_CHANNEL is not set in .env. Add a line like SLACK_CHANNEL=#general or "
            "SLACK_CHANNEL=C0123456789 pointing at a real channel your bot has been added to."
        )
    if channel[:1] in ("C", "G", "D") and channel[1:].isalnum():
        return channel  # already looks like a channel ID, not a "#name"

    name = channel.lstrip("#")
    cursor = None
    for _ in range(5):
        body = _call("conversations.list", {
            "limit": 200,
            "cursor": cursor,
            "types": "public_channel,private_channel",
        })
        for ch in body.get("channels", []):
            if ch.get("name") == name:
                return ch["id"]
        cursor = (body.get("response_metadata") or {}).get("next_cursor")
        if not cursor:
            break
    raise RuntimeError(f"Could not find a Slack channel named '#{name}' that this bot is a member of.")


_user_name_cache = {}


def _resolve_user_name(user_id):
    """Slack's history API returns raw user IDs ('U0123ABC'), not display
    names — showing those in a generated screen would look broken, so this
    resolves each one via users.info, cached per process run since the same
    few people usually post most of the messages in one channel fetch."""
    if not user_id:
        return "unknown"
    if user_id in _user_name_cache:
        return _user_name_cache[user_id]
    try:
        body = _call("users.info", {"user": user_id})
        profile = body.get("user", {}) or {}
        name = profile.get("real_name") or profile.get("name") or user_id
    except RuntimeError:
        name = user_id  # couldn't resolve it — fall back to the raw ID rather than failing the whole fetch
    _user_name_cache[user_id] = name
    return name


def get_slack_messages(contains=None, limit=20):
    """
    Fetch real recent messages from the configured Slack channel
    (SLACK_CHANNEL in .env), most recent first. `contains`, if given, is a
    simple case-insensitive substring filter applied after fetching — not
    real Slack search (see module docstring for why not). System messages
    (joins, leaves, channel renames, etc.) are skipped — they're not real
    conversation. Returns a simplified list: who sent it (resolved to a
    real display name), the text, when, and how many replies it has.
    """
    channel_id = _resolve_channel_id(os.environ.get("SLACK_CHANNEL", ""))
    body = _call("conversations.history", {
        "channel": channel_id,
        "limit": min(max(int(limit or 20), 1), 100),
    })

    messages = []
    for m in body.get("messages", []):
        if m.get("subtype"):
            continue
        text = m.get("text", "")
        if contains and contains.lower() not in text.lower():
            continue
        messages.append({
            "user": _resolve_user_name(m.get("user")),
            "text": text,
            "ts": m.get("ts"),
            "reply_count": m.get("reply_count", 0),
        })
    return messages


if __name__ == "__main__":
    # Quick manual check: python3 connectors_slack.py
    import sys
    print(f"SLACK_CHANNEL = {os.environ.get('SLACK_CHANNEL') or '(not set)'}", file=sys.stderr)
    print(json.dumps(get_slack_messages(), indent=2))
