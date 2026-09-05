"""
Tests for the 2026-08-26 embeddings-provider swap: rag_index.py moved from
OpenAI's embeddings API to Amazon Bedrock (Titan Text Embeddings V2), at
the user's request, once it turned out they already had a Bedrock
long-term API key and wanted to stay on one AWS bill instead of signing up
for OpenAI separately. All boto3 calls are mocked — this validates the
wiring (env var checked, request shape sent to Bedrock, response parsed,
errors wrapped), not real Bedrock connectivity or embedding quality.
"""
import json
import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/claude/pilant-agent")

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK   {label}")
    else:
        failed += 1
        print(f"  FAIL {label}")


import rag_index

# Reset module-level singletons between checks so each test starts clean —
# these are cached lazily (see _get_bedrock_client/_get_collection), which
# would otherwise leak a mock client from one check into the next.
def _reset():
    rag_index._bedrock_client = None


print("== rag_index.is_configured() checks the Bedrock env var, not OpenAI's ==")
with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": ""}, clear=False):
    if "AWS_BEARER_TOKEN_BEDROCK" in os.environ:
        del os.environ["AWS_BEARER_TOKEN_BEDROCK"]
    check("is_configured() is False with no AWS_BEARER_TOKEN_BEDROCK set", rag_index.is_configured() is False)

with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "BedrockAPIKey-fake-at-000000000000"}):
    check("is_configured() is True once AWS_BEARER_TOKEN_BEDROCK is set", rag_index.is_configured() is True)


print("== rag_index._get_bedrock_client() ==")
_reset()
with patch.dict(os.environ, {}, clear=False):
    os.environ.pop("AWS_BEARER_TOKEN_BEDROCK", None)
    try:
        rag_index._get_bedrock_client()
        check("_get_bedrock_client() raises when not configured", False)
    except RuntimeError as e:
        check("_get_bedrock_client() raises a clear RuntimeError naming AWS_BEARER_TOKEN_BEDROCK when not configured",
              "AWS_BEARER_TOKEN_BEDROCK" in str(e) and "Bedrock console" in str(e))

_reset()
with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "fake-token"}), patch("rag_index.boto3.client") as m_boto:
    m_client = MagicMock()
    m_boto.return_value = m_client
    client = rag_index._get_bedrock_client()
    check("_get_bedrock_client() builds a bedrock-runtime client in the configured region",
          m_boto.call_args[0][0] == "bedrock-runtime" and m_boto.call_args[1]["region_name"] == rag_index.BEDROCK_REGION)
    check("_get_bedrock_client() caches the client across calls (doesn't rebuild every time)",
          rag_index._get_bedrock_client() is client and m_boto.call_count == 1)


print("== rag_index._embed() ==")
_reset()


def _fake_bedrock_response(vector):
    body = MagicMock()
    body.read.return_value = json.dumps({"embedding": vector, "inputTextTokenCount": 5}).encode()
    return {"body": body}


with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "fake-token"}), patch("rag_index.boto3.client") as m_boto:
    m_client = MagicMock()
    m_client.invoke_model.side_effect = [
        _fake_bedrock_response([0.1, 0.2, 0.3]),
        _fake_bedrock_response([0.4, 0.5, 0.6]),
    ]
    m_boto.return_value = m_client
    result = rag_index._embed(["first text", "second text"])
    check("_embed() returns one real embedding vector per input text", result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    check("_embed() makes one invoke_model call per text (Bedrock has no batch input, unlike the old OpenAI call)",
          m_client.invoke_model.call_count == 2)
    first_call_kwargs = m_client.invoke_model.call_args_list[0][1]
    check("_embed() calls the configured Titan model id", first_call_kwargs["modelId"] == rag_index.EMBEDDING_MODEL_ID)
    sent_body = json.loads(first_call_kwargs["body"])
    check("_embed() sends the real text as inputText", sent_body["inputText"] == "first text")
    check("_embed() requests normalized, fixed-dimension embeddings (matches the cosine-space Chroma collection)",
          sent_body["normalize"] is True and sent_body["dimensions"] == rag_index.EMBEDDING_DIMENSIONS)

_reset()
check("_embed([]) short-circuits without touching Bedrock at all", rag_index._embed([]) == [])

_reset()
with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "fake-token"}), patch("rag_index.boto3.client") as m_boto:
    m_client = MagicMock()
    m_client.invoke_model.side_effect = Exception("AccessDeniedException: model access not granted")
    m_boto.return_value = m_client
    try:
        rag_index._embed(["x"])
        check("_embed() raises on a genuine Bedrock failure instead of silently returning garbage", False)
    except RuntimeError as e:
        check("_embed() wraps a real Bedrock error into a RuntimeError with actionable guidance",
              "AWS_BEARER_TOKEN_BEDROCK" in str(e) and "model access" in str(e).lower() and rag_index.EMBEDDING_MODEL_ID in str(e))

_reset()
with patch.dict(os.environ, {"AWS_BEARER_TOKEN_BEDROCK": "fake-token"}), patch("rag_index.boto3.client") as m_boto:
    m_client = MagicMock()
    m_client.invoke_model.return_value = _fake_bedrock_response([0.0] * 1024)
    m_boto.return_value = m_client
    long_text = "x" * 50000
    rag_index._embed([long_text])
    sent_body = json.loads(m_client.invoke_model.call_args[1]["body"])
    check("_embed() truncates pathologically long input as a rough safety net instead of sending it raw",
          len(sent_body["inputText"]) == rag_index._MAX_EMBED_CHARS)


print("== end-to-end through index_documents()/search() with _embed mocked (Chroma logic unaffected by the provider swap) ==")
import tempfile
import shutil

_test_store = tempfile.mkdtemp(prefix="rag_bedrock_test_")
with patch("rag_index.RAG_STORE_DIR", __import__("pathlib").Path(_test_store)):
    rag_index._chroma_client = None
    rag_index._collection = None
    with patch("rag_index._embed") as m_embed:
        # bag-of-words-ish fake vectors so "budget" and "budget plan" are close, unrelated text is far
        def fake_embed(texts):
            return [[1.0, 0.0, 0.0] if "budget" in t.lower() else [0.0, 1.0, 0.0] for t in texts]
        m_embed.side_effect = fake_embed

        count = rag_index.index_documents([
            {"id": "1", "source": "gmail", "title": "Q2 budget", "text": "the Q2 budget plan", "url": "/message/1", "timestamp": "2026-01-01"},
            {"id": "2", "source": "slack", "title": "release", "text": "the release shipped today", "url": "https://x", "timestamp": ""},
        ])
        check("index_documents() indexes both real docs (embedding provider swap didn't break indexing)", count == 2)

        hits = rag_index.search("budget", top_k=5)
        check("search() still returns real hits with the swapped embedding provider", len(hits) == 2)
        check("search() ranks the actually-relevant (budget) doc first", hits[0]["title"] == "Q2 budget")

        stats = rag_index.stats()
        check("stats() still reports totals correctly", stats["total"] == 2 and stats["by_source"] == {"gmail": 1, "slack": 1})

rag_index._chroma_client = None
rag_index._collection = None
shutil.rmtree(_test_store, ignore_errors=True)


print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
