"""
The hosted portal — the actual "no default dashboard, just a query box"
product, running as a real local web server instead of a CLI script.

A real login now sits in front of it: nothing past /login is reachable
without an authenticated session. Once logged in, the identity that
scopes what you see (users.py) is your session, not a dropdown you could
pick freely — that's the difference between "personalized" and "actually
authorized."

Open http://localhost:5001, log in as one of the mock accounts (see
README.md for the demo usernames/passwords), and you land on a page with
nothing but an input, matching the core mechanic exactly: no default
view, nothing pre-built. Type a request, submit, and the server runs the
real Composition Engine (agent_github.run_agent, connected to a live
GitHub repo, scoped to whoever's logged in) and renders a real screen for
exactly that request.
"""

import html as _html
import os
import secrets
import sys
from functools import wraps
from pathlib import Path

from flask import Flask, request, session, redirect, url_for

from agent_github import run_agent
from users import get_user, verify_login
from renderer import render_html, render_editable_html
from saved_views import list_views, get_view, save_view, delete_view, save_layout, apply_layout

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

app = Flask(__name__)
# Falls back to a random per-process secret if FLASK_SECRET_KEY isn't set in
# .env — fine for a local prototype; it just means sessions reset whenever
# the server restarts, since no key persists across runs to re-sign them.
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


SHARED_CSS = """
:root { --ground:#0B1220; --surface:#141C30; --border:#2A3552; --text:#EDEFF5; --text-muted:#8791A8; --accent:#6C7CFF; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center; background:var(--ground); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; }
.wrap { width:100%; max-width:640px; padding:24px; text-align:center; }
.eyebrow { font-family:'Sora',sans-serif; font-weight:700; font-size:1.4rem; letter-spacing:.02em; color:var(--text); margin:0 0 34px; }
form { display:flex; flex-direction:column; gap:14px; }
.session-row { display:flex; justify-content:center; gap:10px; font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); align-items:center; margin-top:4px; }
.session-row a { color:var(--text-muted); }
.session-row a:hover { color:var(--accent); }
input[type=text], input[type=password] { width:100%; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:18px 20px; font-size:1.05rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
input[type=text]:focus, input[type=password]:focus { outline:none; border-color:var(--accent); }
input[type=text]::placeholder, input[type=password]::placeholder { color:var(--text-muted); }
button { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:14px; font-size:.95rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }
button:hover { opacity:.92; }
.chips { display:flex; flex-wrap:wrap; gap:8px; justify-content:center; margin-top:6px; }
.chip { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); border:1px solid var(--border); border-radius:999px; padding:6px 12px; background:transparent; cursor:pointer; }
.chip:hover { border-color:var(--accent); color:var(--text); }
.error { color:#E8A5A0; font-family:'IBM Plex Mono',monospace; font-size:.8rem; margin-top:16px; }
.saved-views { margin-top:26px; text-align:left; }
.saved-title { font-family:'IBM Plex Mono',monospace; font-size:.7rem; letter-spacing:.05em; text-transform:uppercase; color:var(--text-muted); margin:0 0 10px; }
.saved-list { display:flex; flex-direction:column; gap:6px; }
.saved-row { display:flex; justify-content:space-between; align-items:center; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:10px 14px; }
.saved-link { color:var(--text); text-decoration:none; font-size:.88rem; }
.saved-link:hover { color:var(--accent); }
.saved-del-form { margin:0; }
.saved-del { background:none; border:none; color:var(--text-muted); font-family:'IBM Plex Mono',monospace; font-size:.7rem; cursor:pointer; padding:2px 6px; }
.saved-del:hover { color:#E8A5A0; }
.transcript { display:flex; flex-direction:column; gap:10px; margin-bottom:18px; text-align:left; }
.bubble { border-radius:10px; padding:12px 16px; font-size:.9rem; line-height:1.45; max-width:88%; }
.bubble-user { background:var(--surface); border:1px solid var(--border); color:var(--text); align-self:flex-end; }
.bubble-assistant { background:transparent; border:1px dashed var(--border); color:var(--text-muted); align-self:flex-start; }
.bubble-assistant::before { content:"Pilant asks: "; color:var(--accent); font-family:'IBM Plex Mono',monospace; font-size:.68rem; text-transform:uppercase; letter-spacing:.04em; display:block; margin-bottom:4px; }
.start-over { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); text-decoration:underline; }
.start-over:hover { color:var(--accent); }
"""

LOGIN_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant — log in</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}</style></head><body>"
    "<div class=\"wrap\">"
    "<p class=\"eyebrow\">Pilant</p>"
    "<form method=\"post\" action=\"/login\">"
    "<input type=\"text\" name=\"username\" placeholder=\"username\" autofocus required>"
    "<input type=\"password\" name=\"password\" placeholder=\"password\" required>"
    "<button type=\"submit\">Log in</button>"
    "</form>"
    "{{ERROR_HTML}}"
    "</div></body></html>"
)

LANDING_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}</style></head><body>"
    "<div class=\"wrap\">"
    "<p class=\"eyebrow\">Pilant</p>"
    "{{TRANSCRIPT}}"
    "<form method=\"post\" action=\"/generate\">"
    "<input type=\"text\" name=\"query\" id=\"query\" value=\"{{PREFILL_QUERY}}\" "
    "placeholder=\"{{PLACEHOLDER}}\" autofocus required>"
    "<button type=\"submit\">{{BUTTON_LABEL}}</button>"
    "<div class=\"chips\">{{CHIPS}}</div>"
    "</form>"
    "{{START_OVER}}"
    "{{SAVED_VIEWS}}"
    "<div class=\"session-row\">logged in as {{DISPLAY_NAME}} &middot; <a href=\"/logout\">log out</a></div>"
    "{{ERROR_HTML}}"
    "</div>"
    "<script>"
    "document.querySelectorAll('.chip').forEach(function(el) {"
    "  el.addEventListener('click', function() {"
    "    document.getElementById('query').value = el.dataset.q;"
    "    document.getElementById('query').focus();"
    "  });"
    "});"
    "</script>"
    "</body></html>"
)

EXAMPLE_PROMPTS = [
    "what open issues need attention right now?",
    "show me anything labeled critical",
    "what's waiting to be documented?",
]


def render_login(error=None):
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    out = LOGIN_PAGE
    out = out.replace("{{CSS}}", SHARED_CSS)
    out = out.replace("{{ERROR_HTML}}", error_html)
    return out


def render_saved_views(username):
    saved = list_views(username)
    if not saved:
        return ""
    rows = "".join(
        f'<div class="saved-row"><a href="/view/{_esc(v["id"])}" class="saved-link">{_esc(v["name"])}</a>'
        f'<form method="post" action="/views/{_esc(v["id"])}/delete" class="saved-del-form">'
        f'<button type="submit" class="saved-del">remove</button></form></div>'
        for v in saved
    )
    return f'<div class="saved-views"><p class="saved-title">Your saved views</p><div class="saved-list">{rows}</div></div>'


def render_transcript(transcript):
    """
    transcript is a list of (role, text) tuples, role in ("user", "assistant").
    Only non-empty mid-conversation: a fresh landing page has no transcript.
    """
    if not transcript:
        return ""
    bubbles = "".join(
        f'<div class="bubble bubble-{role}">{_esc(text)}</div>'
        for role, text in transcript
    )
    return f'<div class="transcript">{bubbles}</div>'


def render_landing(user, error=None, prefill_query="", transcript=None):
    chips_html = "".join(
        f'<button type="button" class="chip" data-q="{_esc(c)}">{_esc(c)}</button>'
        for c in EXAMPLE_PROMPTS
    )
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    mid_conversation = bool(transcript)

    out = LANDING_PAGE
    out = out.replace("{{CSS}}", SHARED_CSS)
    out = out.replace("{{TRANSCRIPT}}", render_transcript(transcript))
    out = out.replace("{{PREFILL_QUERY}}", _esc(prefill_query))
    out = out.replace(
        "{{PLACEHOLDER}}",
        "Your answer..." if mid_conversation else "Ask for exactly what you need...",
    )
    out = out.replace("{{BUTTON_LABEL}}", "Send" if mid_conversation else "Generate")
    out = out.replace(
        "{{START_OVER}}",
        '<a href="/" class="start-over">start over</a>' if mid_conversation else "",
    )
    out = out.replace("{{DISPLAY_NAME}}", _esc(user["name"]))
    out = out.replace("{{CHIPS}}", chips_html)
    out = out.replace("{{SAVED_VIEWS}}", render_saved_views(user["username"]))
    out = out.replace("{{ERROR_HTML}}", error_html)
    return out


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_login()

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""
    user = verify_login(username, password)
    if user is None:
        return render_login(error="Incorrect username or password.")

    session.clear()
    session["username"] = user["username"]
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    user = get_user(session["username"])
    # Visiting the landing page fresh means abandoning any clarifying
    # question that was in progress — a deliberate "start over" point.
    session.pop("conversation", None)
    return render_landing(user)


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    """
    Now a multi-turn conversation, not a one-shot form post. session's
    "conversation" key (when present) means the Composition Engine asked a
    clarifying question last time and is waiting for the answer — this
    request's "query" field is that answer, not a fresh request. When
    absent, "query" is a brand-new request. Either way this route can end
    with another clarifying question (loop back to the same page with the
    question added to the transcript) or a finished screen (conversation
    state cleared).
    """
    user = get_user(session["username"])
    text = (request.form.get("query") or "").strip()
    convo = session.get("conversation")

    if not text:
        return render_landing(user, error="Type something to ask for.", transcript=convo["transcript"] if convo else None)

    if convo:
        # Resuming: the person's reply becomes the next turn in the SAME
        # conversation the agent paused — not a new request.
        messages = convo["messages"] + [{"role": "user", "content": text}]
        result = run_agent(
            user=user,
            verbose=True,
            messages=messages,
            fetched_data=convo.get("fetched_data", False),
        )
        original_query = convo["query"]
        transcript = convo["transcript"] + [("user", text)]
    else:
        print(f"[portal] {user['username']}: {text}", file=sys.stderr)
        result = run_agent(text, user, verbose=True)
        original_query = text
        transcript = [("user", text)]

    if "clarify" in result:
        transcript = transcript + [("assistant", result["clarify"])]
        session["conversation"] = {
            "messages": result["messages"],
            "fetched_data": result.get("fetched_data", False),
            "query": original_query,
            "transcript": transcript,
        }
        return render_landing(user, transcript=transcript)

    # Either a finished screen or a real failure — the conversation is over
    # either way, so nothing stale is left in the session for next time.
    session.pop("conversation", None)

    if "error" in result:
        return render_landing(
            user,
            error=f"Couldn't generate a screen for that request ({result['error']}). Try rephrasing.",
            prefill_query=original_query,
        )

    save_form_html = (
        '<form method="post" action="/views" class="save-row">'
        f'<input type="hidden" name="query" value="{_esc(original_query)}">'
        '<input type="text" name="name" placeholder="Name this view to save it...">'
        '<button type="submit">Save view</button>'
        '</form>'
    )
    nav_html = '<a href="/" class="back-link">&larr; New request</a>' + save_form_html
    return render_html(result["render"], original_query, nav_html=nav_html)


@app.route("/view/<view_id>")
@login_required
def view_saved(view_id):
    user = get_user(session["username"])
    saved = get_view(user["username"], view_id)
    if saved is None:
        # Either this view never existed, or it belongs to someone else —
        # both look identical from here, same principle as verify_login()
        # not distinguishing "wrong password" from "unknown username."
        return redirect(url_for("index"))

    print(f"[portal] {user['username']}: (saved: {saved['name']}) {saved['query']}", file=sys.stderr)
    result = run_agent(saved["query"], user, verbose=True)

    if "clarify" in result:
        # A saved query that now needs clarification (the model wants to
        # ask something it didn't need to ask when this was first saved) —
        # there's no interactive loop here to answer it, so surface this as
        # a plain error rather than trying to build a multi-turn reopen flow.
        return render_landing(
            user,
            error=(
                f"Couldn't regenerate \"{saved['name']}\" — it needs clarification: "
                f"\"{result['clarify']}\". Try running it as a new request instead."
            ),
        )

    if "error" in result:
        return render_landing(
            user,
            error=f"Couldn't regenerate \"{saved['name']}\" ({result['error']}). Try again shortly.",
        )

    view_result = result["render"]
    # Fresh data every open (the whole point of a saved view, not a
    # snapshot) — but the SHAPE the user chose last time still applies.
    # Position-based, not content-based: see apply_layout()'s docstring.
    view_result["components"] = apply_layout(view_result.get("components", []), saved.get("layout"))

    nav_html = (
        '<a href="/" class="back-link">&larr; New request</a>'
        f'<span class="saved-tag"> &middot; saved view: {_esc(saved["name"])} '
        '(regenerated from live data just now)</span>'
    )
    return render_editable_html(
        view_result, saved["query"], nav_html, save_action=f"/views/{view_id}/layout"
    )


@app.route("/views", methods=["POST"])
@login_required
def create_view():
    user = get_user(session["username"])
    query = (request.form.get("query") or "").strip()
    name = (request.form.get("name") or "").strip()
    if query:
        save_view(user["username"], name, query)
    return redirect(url_for("index"))


@app.route("/views/<view_id>/delete", methods=["POST"])
@login_required
def remove_view(view_id):
    user = get_user(session["username"])
    delete_view(user["username"], view_id)
    return redirect(url_for("index"))


@app.route("/views/<view_id>/layout", methods=["POST"])
@login_required
def update_layout(view_id):
    user = get_user(session["username"])
    order_raw = (request.form.get("order") or "").strip()
    order = [int(x) for x in order_raw.split(",") if x.strip().isdigit()] if order_raw else []
    try:
        total = int(request.form.get("total", len(order)))
    except ValueError:
        total = len(order)
    save_layout(user["username"], view_id, order, total)
    return redirect(url_for("view_saved", view_id=view_id))


if __name__ == "__main__":
    print("Pilant portal running at http://localhost:5001", file=sys.stderr)
    app.run(host="127.0.0.1", port=5001, debug=False)
