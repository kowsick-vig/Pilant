"""
Shared identity scoping for knowledge-base search results — factored out
2026-08-26 so agent_github.py's search_knowledge_base dispatch, and any
other connector's search_knowledge_base dispatch, apply the exact same
rule: re-run users.scope_issues() against any github-sourced
rag_index.search() hit before it's ever shown to anyone, using that hit's
carried-through `labels` metadata (see rag_index.index_documents' docstring
for why `labels` exists on search results at all).

(This briefly had a third caller too — agent_search.py, a standalone
Search connector/page. That page was removed the same day at the user's
request, since search_knowledge_base was already reachable as a tool
inside every connector's own chat and a connector-less search page was
redundant rather than additive. This module stayed, since the scoping rule
itself is still needed by every connector that has the tool.)

One rule, one implementation, every caller reuses it — not multiple copies
that could quietly drift out of sync with each other over time. This is the
same reasoning schema.py/guardrails.py/validation.py already follow for
being shared across every connector, just applied to this one specific,
security-relevant check.
"""

from users import scope_issues


def scope_knowledge_base_results(results, user):
    """Re-applies users.scope_issues()'s per-user label-based access
    control to search_knowledge_base results — the whole point of
    agent_github.py's identity scoping existing at all is to prove it holds
    on every real fetch path, and semantic search over a pre-built index is
    still a fetch path, so it doesn't get a free pass just because it goes
    through rag_index.py instead of get_github_issues(). A github-sourced
    hit is kept only if scope_issues() (applied to a one-item
    {"labels": [...]} pseudo-issue built from that hit's carried-through
    labels metadata) would keep it — same rule, same helper, not a
    reimplementation that could drift out of sync with it. Gmail/Slack hits
    have no label-scope concept and pass through untouched — this was
    never the mechanism scoping those sources."""
    kept = []
    for r in results:
        if r.get("source") != "github":
            kept.append(r)
            continue
        if scope_issues([{"labels": r.get("labels") or []}], user):
            kept.append(r)
    return kept
