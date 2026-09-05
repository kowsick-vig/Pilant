"""
Local RAG (retrieval-augmented generation) index — built 2026-08-26 at the
user's explicit request, after asking "if we use rag what benefit we will
get?" and then "do I want to buy agentic rag or you can built?" (answer:
built directly into this codebase, no third-party product). Setup follows
the user's own picks: a local vector store (Chroma — no cloud signup, no
ongoing infra cost, data never leaves this machine) covering Gmail + the
other real connectors so a request can search across history too large to
fit in a single live-fetch call (connectors_gmail.get_gmail_messages caps
at 50 messages per call, connectors_slack.get_slack_messages at 100, with
no pagination exposed by either) or that isn't phrased as exact
Gmail/Slack search syntax.

EMBEDDINGS PROVIDER, switched 2026-08-26: originally built against
OpenAI's embeddings API, but switched to Amazon Bedrock (Titan Text
Embeddings V2) before ever going live, once it turned out the user already
had a Bedrock long-term API key (generated from Amazon Bedrock console ->
API keys -> Long-term API keys) and would rather keep this on their
existing AWS bill than sign up for a separate OpenAI account/billing just
for embeddings. Functionally equivalent from index_documents()/search()'s
point of view — only _embed() and is_configured() below know which
provider is actually behind them.

This module owns the vector store and the embed/search primitives only —
it has no idea what a "Gmail message" or "Slack message" is. Per-connector
ingestion (turning each connector's live data into normalized documents)
lives in rag_ingest.py, mirroring this codebase's existing
connectors_*.py / agent_*.py split: one file per real concern, not one
file that knows about everything.

Requires AWS_BEARER_TOKEN_BEDROCK in .env — a NEW requirement, separate
from ANTHROPIC_API_KEY which the rest of this app runs on directly against
Anthropic's own API, not through Bedrock. AWS_BEARER_TOKEN_BEDROCK is one
of Bedrock's own "long-term API keys" (a bearer token, not an
access-key/secret-key pair) — boto3/botocore picks it up automatically
from that exact environment variable name for bedrock-runtime calls, which
is why this module never has to touch it directly beyond reading it in
is_configured(). Needs a boto3/botocore recent enough to support Bedrock
API keys (mid-2025 or later) — see requirements.txt's comment on the
pinned version; if authentication fails with an old-looking error,
`pip install --upgrade boto3` first. `chromadb` and `boto3` are both new
project dependencies (added to requirements.txt).

The index persists to disk at RAG_STORE_DIR (default ./rag_store/) so it
survives process restarts — add rag_store/ to .gitignore if it isn't
already there, same as any other local, regenerable, credentials-adjacent
directory. Documents are upserted by (source, id), so re-running ingestion
is always safe — it refreshes existing entries rather than duplicating
them, never grows unbounded from repeated syncs.
"""

import json
import os
from pathlib import Path

import boto3
import chromadb

RAG_STORE_DIR = Path(__file__).parent / "rag_store"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024
# Bedrock is regional — the user's console screenshot showing this key was
# on "United States (N. Virginia)" (us-east-1), so that's the default here;
# override with BEDROCK_REGION in .env if the model access / key was set
# up in a different region.
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
COLLECTION_NAME = "pilant_knowledge_base"
# Titan Text Embeddings V2's practical input ceiling is measured in tokens
# (~8K), not characters — this is a cheap, rough character-count safety net
# against pathologically long input (not exact token counting), same spirit
# as any other "don't send something absurdly oversized" guard in this
# codebase.
_MAX_EMBED_CHARS = 8000

_chroma_client = None
_collection = None
_bedrock_client = None


def is_configured():
    """True if AWS_BEARER_TOKEN_BEDROCK is set — used to hide/disable
    RAG-dependent UI and agent tools gracefully when it isn't, same pattern
    connectors_gmail.py's own `configured` check uses for GMAIL_* env vars.
    Only checks the env var is present, not that it's actually valid —
    same shallow check every other connector's is_configured()-equivalent
    does; a genuinely bad/expired key still surfaces as a clear RuntimeError
    from _get_bedrock_client() the first time it's actually used, not here."""
    return bool(os.environ.get("AWS_BEARER_TOKEN_BEDROCK", "").strip())


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        if not is_configured():
            raise RuntimeError(
                "AWS_BEARER_TOKEN_BEDROCK is not set in .env — required for RAG embeddings "
                "(a separate credential from ANTHROPIC_API_KEY, which the rest of this app "
                "runs on directly against Anthropic's API, not through Bedrock). Generate a "
                "long-term API key at Amazon Bedrock console -> API keys -> Long-term API "
                "keys -> Generate long-term API keys, and add it to .env."
            )
        # boto3/botocore auto-detects AWS_BEARER_TOKEN_BEDROCK from the
        # environment for bedrock-runtime calls — no explicit credential
        # wiring needed here beyond confirming it's present above.
        _bedrock_client = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
    return _bedrock_client


def _get_collection():
    global _chroma_client, _collection
    if _collection is None:
        RAG_STORE_DIR.mkdir(exist_ok=True)
        _chroma_client = chromadb.PersistentClient(path=str(RAG_STORE_DIR))
        # Explicit cosine space: Titan V2 embeddings are requested
        # unit-normalized (see _embed's normalize=True below), and search()'s
        # score below assumes a cosine distance in [0, 2] — Chroma's actual
        # default ("l2") would make that math meaningless.
        _collection = _chroma_client.get_or_create_collection(
            COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
        )
    return _collection


def _embed(texts):
    """Embeds a list of strings via Amazon Titan Text Embeddings V2 on
    Bedrock. UNLIKE the original OpenAI version, Bedrock's invoke_model
    doesn't accept a batch of inputs in one call — Titan V2 takes exactly
    one inputText per request, so this loops and makes one real Bedrock
    call per text. Worth knowing honestly: a 50-message Gmail sync is 50
    real requests, not 1 — slower than the batched call this replaced, but
    functionally equivalent (index_documents()/search() above don't know
    or care how many real calls happened underneath)."""
    if not texts:
        return []
    client = _get_bedrock_client()
    embeddings = []
    for text in texts:
        body = json.dumps({
            "inputText": text[:_MAX_EMBED_CHARS],
            "dimensions": EMBEDDING_DIMENSIONS,
            "normalize": True,
        })
        try:
            resp = client.invoke_model(
                modelId=EMBEDDING_MODEL_ID, body=body,
                contentType="application/json", accept="application/json",
            )
        except Exception as e:
            raise RuntimeError(
                f"Bedrock embedding request failed: {e}. If this looks like an auth "
                "error, confirm AWS_BEARER_TOKEN_BEDROCK is a valid, unexpired long-term "
                "API key, that boto3 is up to date (pip install --upgrade boto3), and "
                f"that model access for {EMBEDDING_MODEL_ID} is enabled in the "
                f"{BEDROCK_REGION} region."
            ) from e
        payload = json.loads(resp["body"].read())
        embeddings.append(payload["embedding"])
    return embeddings


def index_documents(docs):
    """
    Embeds and upserts a batch of documents into the shared knowledge base.
    Each doc must be a dict with:
      id        - a string unique within `source` (a Gmail message id, a
                  Slack message ts, a GitHub issue number) — combined with
                  `source` to form the real Chroma id, so the same raw id
                  from two different connectors never collides.
      source    - which connector this came from ("gmail"/"slack"/"github").
      title     - a short human label (subject line, issue title, or a
                  truncated first line) shown with every search result.
      text      - the actual searchable content — this is what gets embedded.
      url       - a real link back to the original, shown with every result
                  so a person can jump straight to the source.
      timestamp - whatever date/time string the source provides, stored as
                  metadata (not currently used for ranking — similarity
                  search only for now; recency-weighting is a reasonable
                  future addition, not built here).
      labels    - OPTIONAL list of strings (currently only GitHub issues
                  set this) — stored comma-joined since Chroma metadata
                  values must be scalars, not lists, and returned back out
                  as a real list by search() below. Exists so
                  agent_github.py's search_knowledge_base handler can
                  re-apply users.scope_issues()'s per-user label-based
                  access control to knowledge-base results the same way
                  get_github_issues()'s live results already are —
                  semantic search must not be a backdoor around that.
    Skips any doc with empty/missing text (nothing to embed). Returns how
    many documents were actually indexed. Re-indexing a (source, id) that
    already exists overwrites it — safe to run repeatedly.
    """
    docs = [d for d in docs if (d.get("text") or "").strip()]
    if not docs:
        return 0
    collection = _get_collection()
    ids = [f"{d['source']}:{d['id']}" for d in docs]
    embeddings = _embed([d["text"] for d in docs])
    metadatas = [
        {
            "source": d["source"],
            "title": d.get("title") or "",
            "url": d.get("url") or "",
            "timestamp": d.get("timestamp") or "",
            "labels": ",".join(d.get("labels") or []),
        }
        for d in docs
    ]
    documents = [d["text"] for d in docs]
    collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=documents)
    return len(docs)


def search(query, top_k=8, source=None):
    """
    Semantic search over the indexed knowledge base. `source`, if given
    ("gmail"/"slack"/"github"), restricts results to that one connector —
    omit it to search across everything indexed at once, which is the
    whole point of one shared collection instead of one index per
    connector. Returns a list of dicts:
      {source, title, url, timestamp, snippet, score, labels}
    ordered most-relevant first. `labels` is a list (usually empty except
    for GitHub-sourced results — see index_documents' docstring on why
    it's carried through: agent_github.py needs it to re-apply per-user
    label scoping to these results, the same as it already does for
    get_github_issues()'s live results). `score` is a rough 0-1ish similarity
    (higher = more relevant; Chroma returns cosine distance, converted here
    as score = 1 - distance) — useful for sorting/display, not a precise
    probability. Returns [] on an empty/uninitialized index rather than
    erroring, since "search before anything's been synced" is a normal
    state to handle gracefully, not a failure."""
    if not (query or "").strip():
        return []
    collection = _get_collection()
    if collection.count() == 0:
        return []
    query_embedding = _embed([query])[0]
    where = {"source": source} if source else None
    result = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        where=where,
    )
    hits = []
    ids = result.get("ids", [[]])[0]
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    dists = result.get("distances", [[]])[0]
    for i in range(len(ids)):
        meta = metas[i] or {}
        dist = dists[i]
        labels_raw = meta.get("labels", "") or ""
        hits.append({
            "source": meta.get("source", ""),
            "title": meta.get("title", ""),
            "url": meta.get("url", ""),
            "timestamp": meta.get("timestamp", ""),
            "snippet": (docs[i] or "")[:400],
            "score": round(1 - dist, 4) if dist is not None else None,
            "labels": [l for l in labels_raw.split(",") if l],
        })
    return hits


def stats():
    """Returns {"total": N, "by_source": {"gmail": N, "slack": N, ...}} —
    used by the Integrations page to show what's actually indexed, and by
    the sync route to report what a run added/refreshed."""
    collection = _get_collection()
    total = collection.count()
    by_source = {}
    if total:
        got = collection.get(include=["metadatas"])
        for meta in got.get("metadatas", []):
            src = (meta or {}).get("source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
    return {"total": total, "by_source": by_source}


if __name__ == "__main__":
    # Quick manual check: python3 rag_index.py "some query"
    import sys
    import json as _json
    q = " ".join(sys.argv[1:]) or "test"
    print(f"AWS_BEARER_TOKEN_BEDROCK configured: {is_configured()}", file=sys.stderr)
    print(f"stats: {stats()}", file=sys.stderr)
    print(_json.dumps(search(q), indent=2))
