"""
Per-connector ingestion for rag_index.py's knowledge base — turns each real
connector's live data into the normalized {id, source, title, text, url,
timestamp} documents rag_index.index_documents() expects. rag_index.py
itself has no idea what a Gmail message or a Slack message is; this is the
one place that translation happens, one function per connector, so adding
a new connector to the RAG index later means writing one ingest_x()
function here, not changing the index itself.

Every ingest_*() function pulls at most one call's worth of data — the
real ceiling each connector already enforces internally
(get_gmail_messages caps at 50, get_slack_messages at 100,
get_github_issues at a hardcoded 30 per page, none of them paginate). So a
sync is honestly "the most recent N items," not full history — worth
knowing going in rather than assuming a sync silently covers years of
mail. Extending any of those connectors with real pagination is a
reasonable next step, not something this file works around.

ingest_all() catches each connector's own RuntimeError (its "not
configured" signal — GMAIL_REFRESH_TOKEN missing, SLACK_CHANNEL unset,
GITHUB_REPO unset) per-connector and skips it rather than letting one
unconfigured connector block the others, since "Gmail is connected but
Slack isn't" is the normal, expected state for most people running this,
not an error to surface as one.
"""

import os

import connectors_gmail
import connectors_slack
import connectors_github
import rag_index


def ingest_gmail():
    """Indexes the most recent 50 messages across All Mail (no folder
    filter, no query — the broadest single get_gmail_messages() call can
    return) — subject + snippet as the searchable text. Uses the snippet
    already returned by get_gmail_messages rather than a second
    get_message_full() call per message, which would make a 50-message
    sync 50x slower for full body text — a reasonable trade for now;
    indexing full bodies is a natural follow-up if snippet-level search
    isn't precise enough."""
    messages = connectors_gmail.get_gmail_messages(limit=50)
    docs = []
    for m in messages:
        text = f"{m.get('subject') or ''}\n{m.get('snippet') or ''}".strip()
        docs.append({
            "id": m["id"],
            "source": "gmail",
            "title": m.get("subject") or "(no subject)",
            "text": text,
            "url": f"/message/{m['id']}",
            "timestamp": m.get("date") or "",
        })
    return rag_index.index_documents(docs)


def ingest_slack():
    """Indexes the most recent 100 channel messages. Slack's real permalink
    format needs the channel's real ID (not the "#name" SLACK_CHANNEL might
    be set to) plus the message's ts — connectors_slack._resolve_channel_id
    is that same lookup get_slack_messages() already does internally,
    reused here rather than reimplemented (it's a private helper, but
    within this same codebase that's a reasonable reach rather than
    duplicating channel-ID-resolution logic)."""
    channel_id = connectors_slack._resolve_channel_id(os.environ.get("SLACK_CHANNEL", ""))
    messages = connectors_slack.get_slack_messages(limit=100)
    docs = []
    for m in messages:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        ts = m.get("ts") or ""
        permalink = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}" if ts else ""
        docs.append({
            "id": ts or text[:40],
            "source": "slack",
            "title": text[:80],
            "text": text,
            "url": permalink,
            "timestamp": ts,
        })
    return rag_index.index_documents(docs)


def ingest_github():
    """Indexes open AND closed issues (state="all" — broader than
    agent_github.py's own default of "open", since a knowledge base is
    exactly the place someone would look for a past, already-closed issue).
    Title + labels only, not the full issue body — get_github_issues()
    doesn't fetch it, and a second API call per issue would slow a sync
    the same way a per-message get_message_full() call would for Gmail;
    see ingest_gmail's docstring for the same trade-off."""
    issues = connectors_github.get_github_issues(state="all")
    docs = []
    for it in issues:
        labels = ", ".join(it.get("labels") or [])
        title = it.get("title") or f"Issue #{it['number']}"
        text = f"{title}" + (f" [{labels}]" if labels else "")
        docs.append({
            "id": str(it["number"]),
            "source": "github",
            "title": title,
            "text": text,
            "url": it.get("url") or "",
            "timestamp": it.get("opened") or "",
            # Carried through so agent_github.py can re-apply
            # users.scope_issues()'s per-user label scoping to search
            # results — see rag_index.index_documents' docstring.
            "labels": it.get("labels") or [],
        })
    return rag_index.index_documents(docs)


def ingest_all():
    """Runs every connector's ingestion, skipping (not failing on) any
    connector that isn't configured — see this module's docstring. Returns
    {"gmail": {"indexed": N} or {"error": "..."}, "slack": ..., "github": ...}
    so the sync route/UI can show exactly what happened per source, rather
    than one opaque pass/fail for the whole run."""
    results = {}
    for name, fn in [("gmail", ingest_gmail), ("slack", ingest_slack), ("github", ingest_github)]:
        try:
            count = fn()
            results[name] = {"indexed": count}
        except RuntimeError as e:
            results[name] = {"error": str(e)}
        except Exception as e:  # a real bug or transient API failure, not a config gap
            results[name] = {"error": f"Unexpected error: {e}"}
    return results


if __name__ == "__main__":
    # Quick manual check: python3 rag_ingest.py
    import json
    print(json.dumps(ingest_all(), indent=2))
