"""
Fourth demo, and the one built specifically to be impossible to break in a
demo: no live GitHub repo, no rate limit, no GITHUB_TOKEN, no external data
dependency of any kind. The only thing this needs to reach the network for
is the NVIDIA model call itself — everything it shows comes from the static
TICKETS list in connectors_helpdesk.py.

Same core mechanic as server.py: log in, land on a page with nothing but an
input, type a request, get back a real generated screen (or a clarifying
question first, if the request is genuinely ambiguous — same one-question
conversational flow as server.py, same agent_helpdesk.run_agent()).

What's deliberately NOT here, on purpose, to keep this demo minimal:
saved views and the drag-to-reshape editor. Those already have a full,
tested implementation in server.py against live GitHub data — repeating
that story here would just be more surface area for the same proof.
Logins still gate this page (reusing users.py) for consistency with the
rest of the project, even though ticket data itself isn't scoped per user
(see agent_helpdesk.py's docstring for why: this demo isn't re-proving the
access-control story, that's already covered elsewhere).
"""

import html as _html
import os
import secrets
import sys
from functools import wraps
from pathlib import Path

from flask import Flask, request, session, redirect, url_for

from agent_helpdesk import run_agent
from users import get_user, verify_login
from renderer import render_html

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

app = Flask(__name__)
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
.eyebrow { font-family:'Sora',sans-serif; font-weight:700; font-size:1.4rem; letter-spacing:.02em; color:var(--text); margin:0 0 6px; }
.subeyebrow { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); margin:0 0 28px; }
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
.transcript { display:flex; flex-direction:column; gap:10px; margin-bottom:18px; text-align:left; }
.bubble { border-radius:10px; padding:12px 16px; font-size:.9rem; line-height:1.45; max-width:88%; }
.bubble-user { background:var(--surface); border:1px solid var(--border); color:var(--text); align-self:flex-end; }
.bubble-assistant { background:transparent; border:1px dashed var(--border); color:var(--text-muted); align-self:flex-start; }
.bubble-assistant::before { content:"Pilant asks: "; color:var(--accent); font-family:'IBM Plex Mono',monospace; font-size:.68rem; text-transform:uppercase; letter-spacing:.04em; display:block; margin-bottom:4px; }
.start-over { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); text-decoration:underline; }
.start-over:hover { color:var(--accent); }
"""

LOGIN_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant Helpdesk — log in</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}</style></head><body>"
    "<div class=\"wrap\">"
    "<p class=\"eyebrow\">Pilant</p>"
    "<p class=\"subeyebrow\">helpdesk demo &middot; static data</p>"
    "<form method=\"post\" action=\"/login\">"
    "<input type=\"text\" name=\"username\" placeholder=\"username\" autofocus required>"
    "<input type=\"password\" name=\"password\" placeholder=\"password\" required>"
    "<button type=\"submit\">Log in</button>"
    "</form>"
    "{{ERROR_HTML}}"
    "</div></body></html>"
)

LANDING_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant Helpdesk</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}</style></head><body>"
    "<div class=\"wrap\">"
    "<p class=\"eyebrow\">Pilant</p>"
    "<p class=\"subeyebrow\">helpdesk demo &middot; static data</p>"
    "{{TRANSCRIPT}}"
    "<form method=\"post\" action=\"/generate\">"
    "<input type=\"text\" name=\"query\" id=\"query\" value=\"{{PREFILL_QUERY}}\" "
    "placeholder=\"{{PLACEHOLDER}}\" autofocus required>"
    "<button type=\"submit\">{{BUTTON_LABEL}}</button>"
    "<div class=\"chips\">{{CHIPS}}</div>"
    "</form>"
    "{{START_OVER}}"
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
    "what open tickets need attention right now?",
    "show me anything critical or escalated",
    "what's waiting on billing?",
]


def render_login(error=None):
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    out = LOGIN_PAGE
    out = out.replace("{{CSS}}", SHARED_CSS)
    out = out.replace("{{ERROR_HTML}}", error_html)
    return out


def render_transcript(transcript):
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
    session.pop("conversation", None)
    return render_landing(get_user(session["username"]))


@app.route("/generate", methods=["POST"])
@login_required
def generate():
    """
    Same multi-turn conversation shape as server.py's /generate — see that
    file's docstring for the full explanation. The only difference here is
    the call into agent_helpdesk.run_agent(), which takes no `user` argument
    (this demo doesn't scope tickets by identity).
    """
    user = get_user(session["username"])
    text = (request.form.get("query") or "").strip()
    convo = session.get("conversation")

    if not text:
        return render_landing(user, error="Type something to ask for.", transcript=convo["transcript"] if convo else None)

    if convo:
        messages = convo["messages"] + [{"role": "user", "content": text}]
        result = run_agent(
            verbose=True,
            messages=messages,
            fetched_data=convo.get("fetched_data", False),
        )
        original_query = convo["query"]
        transcript = convo["transcript"] + [("user", text)]
    else:
        print(f"[helpdesk] {user['username']}: {text}", file=sys.stderr)
        result = run_agent(text, verbose=True)
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

    session.pop("conversation", None)

    if "error" in result:
        return render_landing(
            user,
            error=f"Couldn't generate a screen for that request ({result['error']}). Try rephrasing.",
            prefill_query=original_query,
        )

    nav_html = '<a href="/" class="back-link">&larr; New request</a>'
    return render_html(result["render"], original_query, nav_html=nav_html)


if __name__ == "__main__":
    print("Pilant helpdesk demo running at http://localhost:5004", file=sys.stderr)
    app.run(host="127.0.0.1", port=5004, debug=False)
