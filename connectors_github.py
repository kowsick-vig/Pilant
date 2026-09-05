"""
A real connector — reads live issues from a configured GitHub repository via
GitHub's REST API. This replaces connectors.py's fabricated incident data
with an actual external system, proving the agent can build a screen from
data it never saw before, not data baked into the prompt.

No token is required to read a PUBLIC repo (GitHub allows unauthenticated
reads at a lower rate limit — 60 requests/hour). Set GITHUB_TOKEN in .env to
raise that limit, and it's required if GITHUB_REPO is private.

GITHUB_REPO / GITHUB_TOKEN are read from os.environ lazily, inside the
functions below, rather than as module-level constants — that way it
doesn't matter whether the caller loads .env into os.environ before or
after importing this module.
"""

import os
import json
import urllib.request
import urllib.error

API_ROOT = "https://api.github.com"


def _get(path, params=None):
    url = f"{API_ROOT}{path}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
        if query:
            url = f"{url}?{query}"

    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "pilant-agent",
    }
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API error {e.code} for {path}: {body[:300]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach GitHub API: {e.reason}") from e


def get_github_issues(state="open", label=None):
    """
    Fetch real issues from the configured GitHub repo (GITHUB_REPO in .env,
    format "owner/repo"). Returns a simplified list: title, number, state,
    labels, when it was opened, how many comments it has, and its URL.
    Pull requests are excluded (GitHub's issues endpoint includes them).
    """
    repo = os.environ.get("GITHUB_REPO", "")
    if not repo:
        raise RuntimeError(
            "GITHUB_REPO is not set in .env. Add a line like "
            "GITHUB_REPO=octocat/Hello-World pointing at a real repo."
        )

    raw = _get(f"/repos/{repo}/issues", {
        "state": state,
        "labels": label,
        "per_page": 30,
    })

    if isinstance(raw, dict) and raw.get("message"):
        # GitHub returns an error object (e.g. repo not found) as a dict, not a list
        raise RuntimeError(f"GitHub API: {raw['message']}")

    issues = []
    for item in raw:
        if "pull_request" in item:
            continue
        issues.append({
            "number": item["number"],
            "title": item["title"],
            "state": item["state"],
            "labels": [lbl["name"] for lbl in item.get("labels", [])],
            "opened": item["created_at"],
            "comments": item["comments"],
            "url": item["html_url"],
        })
    return issues


if __name__ == "__main__":
    # Quick manual check: python3 connectors_github.py
    import sys
    print(f"GITHUB_REPO = {os.environ.get('GITHUB_REPO') or '(not set)'}", file=sys.stderr)
    print(json.dumps(get_github_issues(), indent=2))
