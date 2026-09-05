"""
Harriet & Co's real customer-facing account portal — a SEPARATE persona
and a separate Flask app (port 5003) from both server.py (the internal
Pilant hosted portal, staff identity) and retail_site.py (the internal
staff ops tool, staff identity). A real end customer logs in here and
sees NOTHING by default — no order list, no account summary — just an
"Ask" box, exactly like the rest of Pilant. Whatever they ask (e.g.
"where's my order?") runs through agent_customer.run_agent(), which is
scoped in TRUSTED CODE (customers.scope_orders) to only ever return
orders belonging to the logged-in customer, no matter how the request is
phrased. This proves the identity/scoping model already proven for
internal staff roles (users.py + agent_github.py) holds up the same way
for a real external customer.
"""

import html as _html
import os
import secrets
import sys
from functools import wraps

from flask import Flask, request, session, redirect, url_for

from agent_customer import run_agent
from renderer import render_fragment
from customers import verify_login, get_customer

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("customer_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


CSS = """
:root { --bg:#FAF7F2; --card:#FFFFFF; --border:#E8E1D6; --text:#2B2620; --muted:#8A8172; --accent:#B5652B; }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; min-height:100vh; display:flex; align-items:flex-start; justify-content:center; padding:60px 24px; }
.card { background:var(--card); border:1px solid var(--border); border-radius:14px; padding:36px 40px; width:100%; max-width:440px; }
.brand { font-family:'Sora',sans-serif; font-weight:700; font-size:1.1rem; margin:0 0 2px; }
.brand-sub { font-size:.72rem; color:var(--muted); font-family:'IBM Plex Mono',monospace; letter-spacing:.03em; margin:0 0 26px; }
h1 { font-family:'Sora',sans-serif; font-weight:600; font-size:1.25rem; margin:0 0 6px; }
.sub { font-size:.85rem; color:var(--muted); margin:0 0 22px; }
label { display:block; font-size:.75rem; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); font-family:'IBM Plex Mono',monospace; margin-bottom:6px; }
input[type=email], input[type=password], input[type=text] { width:100%; border:1px solid var(--border); border-radius:8px; padding:11px 13px; font-size:.92rem; font-family:'IBM Plex Sans',sans-serif; margin-bottom:16px; }
input:focus { outline:none; border-color:var(--accent); }
button { width:100%; background:var(--accent); color:#fff; border:none; border-radius:8px; padding:12px; font-size:.92rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }
button:disabled { opacity:.6; cursor:default; }
.error { background:#FBEAE8; border:1px solid #F0C9C4; color:#A8352A; border-radius:8px; padding:10px 13px; font-size:.82rem; margin-bottom:16px; }
.hint { font-size:.74rem; color:var(--muted); margin-top:18px; font-family:'IBM Plex Mono',monospace; line-height:1.7; }
.topline { display:flex; justify-content:space-between; align-items:center; margin-bottom:22px; }
.logout { font-size:.78rem; color:var(--muted); text-decoration:none; }
.logout:hover { color:var(--accent); }
.chips { display:flex; flex-wrap:wrap; gap:7px; margin-top:10px; }
.chip { font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:var(--muted); border:1px solid var(--border); border-radius:999px; padding:5px 11px; background:transparent; cursor:pointer; }
.chip:hover { border-color:var(--accent); color:var(--text); }
.status { font-size:.78rem; color:var(--muted); margin-top:10px; font-family:'IBM Plex Mono',monospace; }
"""

# Same component-rendering CSS scope as retail_site.py's PILANT_WIDGET_CSS
# (the .app-window scope, not renderer.py's :root/body/page rules) — only
# the result box should look like Pilant, not the whole account page.
PILANT_WIDGET_CSS = """
.app-window { --app-bg:#F4F6FB; --app-card:#FFFFFF; --app-border:#E3E7F1; --app-text:#171B2E; --app-muted:#6B7385; --app-accent:#5B6EF5; --app-critical:#C6392F; --app-critical-bg:#FBEAE8; --app-warning:#A56A0E; --app-warning-bg:#FBF1DE; --app-good:#227A55; --app-good-bg:#E6F5EE; background:var(--app-bg); color:var(--app-text); padding:22px; min-height:60px; border-radius:10px; margin-top:16px; }
.app-window:empty { padding:0; min-height:0; margin-top:0; }
.app-h1 { font-family:'Sora',sans-serif; font-weight:600; font-size:1.1rem; margin:0 0 4px; }
.app-meta { font-size:.82rem; color:var(--app-muted); margin:0 0 14px; }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:10px; margin-bottom:14px; }
.stat-card { background:var(--app-card); border:1px solid var(--app-border); border-left:3px solid var(--app-border); border-radius:6px; padding:12px 13px; }
.stat-card.tone-critical { border-left-color:var(--app-critical); }
.stat-card.tone-warning { border-left-color:var(--app-warning); }
.stat-card.tone-good { border-left-color:var(--app-good); }
.stat-label { font-size:.66rem; letter-spacing:.05em; text-transform:uppercase; color:var(--app-muted); display:block; margin-bottom:5px; font-family:'IBM Plex Mono',monospace; }
.stat-value { font-family:'Sora',sans-serif; font-weight:700; font-size:1.2rem; }
.panel { background:var(--app-card); border:1px solid var(--app-border); border-radius:8px; padding:14px 16px; margin-bottom:10px; }
.panel-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:8px; }
.panel-title { font-family:'Sora',sans-serif; font-weight:600; font-size:.92rem; margin:0 0 3px; }
.panel-sub { font-size:.78rem; color:var(--app-muted); margin:0; }
.badge { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.64rem; font-weight:500; letter-spacing:.03em; text-transform:uppercase; padding:3px 8px; border-radius:4px; flex:none; }
.badge.critical { background:var(--app-critical-bg); color:var(--app-critical); }
.badge.warning { background:var(--app-warning-bg); color:var(--app-warning); }
.badge.good { background:var(--app-good-bg); color:var(--app-good); }
.badge.default { background:var(--app-border); color:var(--app-muted); }
.kv-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px 20px; margin:12px 0; }
.kv-grid dt { font-size:.64rem; text-transform:uppercase; letter-spacing:.04em; color:var(--app-muted); font-family:'IBM Plex Mono',monospace; margin-bottom:2px; }
.kv-grid dd { margin:0; font-size:.82rem; }
.app-btn { font-family:'IBM Plex Sans',sans-serif; font-weight:500; font-size:.78rem; background:var(--app-accent); color:#fff; border:none; border-radius:6px; padding:7px 13px; cursor:pointer; }
.list-row { display:flex; justify-content:space-between; align-items:center; gap:14px; padding:10px 0; border-bottom:1px solid var(--app-border); }
.list-row:last-child { border-bottom:none; }
.list-name { font-weight:600; font-size:.84rem; }
.list-note { font-size:.74rem; color:var(--app-muted); margin-top:2px; }
.list-action { font-size:.72rem; color:var(--app-accent); font-weight:500; white-space:nowrap; }
"""

LOGIN_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Harriet & Co — My Account</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}</style></head><body>"
    "<div class=\"card\">"
    "<p class=\"brand\">Harriet &amp; Co</p>"
    "<p class=\"brand-sub\">MY ACCOUNT</p>"
    "<h1>Sign in</h1>"
    "<p class=\"sub\">Check your orders and returns.</p>"
    "{{ERROR}}"
    "<form method=\"post\">"
    "<label>Email</label><input type=\"email\" name=\"email\" autofocus>"
    "<label>Password</label><input type=\"password\" name=\"password\">"
    "<button type=\"submit\">Sign in</button>"
    "</form>"
    "<p class=\"hint\">Demo accounts:<br>"
    "lferreira@example.com / wraps123<br>"
    "jokafor@example.com / return456<br>"
    "mchen@example.com / trousers789<br>"
    "apetrova@example.com / linen000</p>"
    "</div></body></html>"
)

DASHBOARD_PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Harriet & Co — My Account</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{CSS}}{{PILANT_CSS}}</style></head><body>"
    "<div class=\"card\" style=\"max-width:560px;\">"
    "<div class=\"topline\"><div>"
    "<p class=\"brand\">Harriet &amp; Co</p><p class=\"brand-sub\">MY ACCOUNT</p>"
    "</div><a class=\"logout\" href=\"/logout\">Sign out</a></div>"
    "<h1>Welcome back, {{NAME}}</h1>"
    "<p class=\"sub\">Ask about your orders or returns — nothing shows until you ask.</p>"
    "<form id=\"pilant-form\">"
    "<label>Ask about your account</label>"
    "<input type=\"text\" id=\"pilant-query\" placeholder=\"e.g. where's my order?\">"
    "<button type=\"submit\">Ask</button>"
    "</form>"
    "<div class=\"chips\">{{CHIPS}}</div>"
    "<div class=\"status\" id=\"pilant-status\"></div>"
    "<div class=\"app-window\" id=\"pilant-result\"></div>"
    "</div>"
    "<script>"
    "var form = document.getElementById('pilant-form');"
    "var input = document.getElementById('pilant-query');"
    "var status = document.getElementById('pilant-status');"
    "var result = document.getElementById('pilant-result');"
    "var button = form.querySelector('button');"
    "function ask(q) {"
    "  input.value = q;"
    "  button.disabled = true;"
    "  status.textContent = 'Checking...';"
    "  result.innerHTML = '';"
    "  fetch('/ask', {"
    "    method: 'POST',"
    "    headers: {'Content-Type': 'application/x-www-form-urlencoded'},"
    "    body: 'query=' + encodeURIComponent(q)"
    "  }).then(function(r) { return r.text(); }).then(function(html) {"
    "    result.innerHTML = html;"
    "    status.textContent = '';"
    "    button.disabled = false;"
    "  }).catch(function(err) {"
    "    status.textContent = 'Something went wrong: ' + err;"
    "    button.disabled = false;"
    "  });"
    "}"
    "form.addEventListener('submit', function(e) {"
    "  e.preventDefault();"
    "  if (input.value.trim()) ask(input.value.trim());"
    "});"
    "document.querySelectorAll('.chip').forEach(function(el) {"
    "  el.addEventListener('click', function() { ask(el.dataset.q); });"
    "});"
    "</script>"
    "</body></html>"
)

CUSTOMER_PROMPTS = [
    "what's the status of my order?",
    "has my return been processed?",
    "show me all my orders",
]


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return LOGIN_PAGE.replace("{{CSS}}", CSS).replace("{{ERROR}}", "")

    email = request.form.get("email", "")
    password = request.form.get("password", "")
    customer = verify_login(email, password)
    if not customer:
        out = LOGIN_PAGE.replace("{{CSS}}", CSS)
        out = out.replace("{{ERROR}}", '<div class="error">Incorrect email or password.</div>')
        return out, 401

    session["customer_id"] = customer["customer_id"]
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    customer = get_customer(session["customer_id"])
    chips_html = "".join(
        f'<button type="button" class="chip" data-q="{_esc(p)}">{_esc(p)}</button>'
        for p in CUSTOMER_PROMPTS
    )
    out = DASHBOARD_PAGE
    out = out.replace("{{CSS}}", CSS)
    out = out.replace("{{PILANT_CSS}}", PILANT_WIDGET_CSS)
    out = out.replace("{{NAME}}", _esc(customer["name"]))
    out = out.replace("{{CHIPS}}", chips_html)
    return out


@app.route("/ask", methods=["POST"])
@login_required
def ask():
    query = (request.form.get("query") or "").strip()
    if not query:
        return '<p class="panel-sub">Type something to ask for.</p>'

    customer = get_customer(session["customer_id"])
    print(f"[customer:{customer['customer_id']}] {query}", file=sys.stderr)
    result = run_agent(query, customer, verbose=True)

    if "error" in result:
        return f'<p class="panel-sub">Couldn\'t generate a screen for that request ({_esc(result["error"])}). Try rephrasing.</p>'

    return render_fragment(result)


if __name__ == "__main__":
    print("Harriet & Co customer portal running at http://localhost:5003", file=sys.stderr)
    app.run(host="127.0.0.1", port=5003, debug=False)
