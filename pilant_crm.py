"""
Pilant's own internal CRM/finance tracker — real customers, a real sales
pipeline, real revenue, real expenses. Added 2026-08-30 at the user's
direct request ("Do I want CRM page for monitoring my sales, customer,
revenue, finance... for pilant business") after confirming this is for
actually tracking Pilant's own business, not another demo, and that there
is no real external CRM/accounting tool connected yet.

Because of that last point, this deliberately does NOT show sample/mock
numbers dressed up as real data (unlike, say, healthcare_dashboard.py's
Finance/HR mock sections, which are honestly labeled "sample data" because
there's a real product behind them that just isn't connected to a real
HR system) — every number on every page here comes straight from
connectors_pilant_crm.py's real, persisted store. A brand-new install of
this file shows all zeros and empty states, on purpose, until you actually
enter something through its forms.

Same login gate (users.py) as every other Pilant demo in this project, and
the same IBM Plex Sans/Mono visual language as healthcare_dashboard.py —
now on a light/white theme (switched 2026-08-30 at the user's request) and
with an AI Copilot docked panel (agent_pilant_crm.py, backed by
connectors_pilant_crm.py's same real store) answering real questions about
customers, the pipeline, revenue, and expenses. The Copilot's full-screen
"open" view is styled with this file's own dashboard chrome, the same
approach healthcare_dashboard.py uses, rather than a generic floating card.
"""

import html as _html
import os
import secrets
import sys
from collections import defaultdict
from functools import wraps

from flask import Flask, request, session, redirect, url_for

import connectors_pilant_crm as crm
from users import get_user, verify_login

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _fmt_money(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        v = 0.0
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


SHARED_CSS = """
:root { --ground:#F4F6FB; --surface:#FFFFFF; --surface-2:#F0F2F7; --border:#E3E7F1; --text:#171B2E; --text-muted:#6B7385; --accent:#3FB6A8; --critical:#C6392F; --warning:#A56A0E; --good:#227A55; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; background:var(--ground); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; }
a { color:inherit; }

.login-wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; }
.login-box { width:100%; max-width:400px; padding:24px; text-align:center; }
.eyebrow { font-family:'Sora',sans-serif; font-weight:700; font-size:1.4rem; letter-spacing:.02em; margin:0 0 6px; }
.subeyebrow { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); margin:0 0 28px; }
form.login-form { display:flex; flex-direction:column; gap:14px; }
input[type=text], input[type=password], input[type=number], input[type=date], input[type=email], select, textarea { width:100%; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:12px 14px; font-size:.9rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent); }
input::placeholder, textarea::placeholder { color:var(--text-muted); }
button, .btn { background:var(--accent); color:#0B1220; border:none; border-radius:8px; padding:12px 18px; font-size:.88rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }
button:hover, .btn:hover { opacity:.92; }
.error { color:var(--critical); font-family:'IBM Plex Mono',monospace; font-size:.8rem; margin-top:16px; }
.demo-accounts { margin-top:26px; font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:var(--text-muted); line-height:1.7; text-align:left; border-top:1px solid var(--border); padding-top:16px; }
.demo-accounts b { color:var(--text); }

.shell { display:flex; min-height:100vh; }
.sidebar { width:220px; flex-shrink:0; background:var(--surface); border-right:1px solid var(--border); padding:22px 16px; display:flex; flex-direction:column; }
.brand { font-family:'Sora',sans-serif; font-weight:700; font-size:1.15rem; padding:0 8px 4px; display:flex; align-items:center; gap:8px; }
.brand .dot { width:8px; height:8px; border-radius:50%; background:var(--accent); box-shadow:0 0 8px 1px var(--accent); }
.brand-sub { font-family:'IBM Plex Mono',monospace; font-size:.66rem; color:var(--text-muted); padding:0 8px 20px; }
.navlist { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:2px; }
.navlist a { display:flex; align-items:center; gap:10px; padding:10px 12px; border-radius:8px; font-size:.86rem; color:var(--text-muted); text-decoration:none; }
.navlist a:hover { background:var(--surface-2); color:var(--text); }
.navlist a.active { background:var(--surface-2); color:var(--text); font-weight:600; box-shadow:inset 2px 0 0 var(--accent); }
.navlist .icon { width:16px; text-align:center; opacity:.85; }
.sidebar-foot { margin-top:auto; padding-top:16px; border-top:1px solid var(--border); font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:var(--text-muted); }
.sidebar-foot .who { color:var(--text); font-weight:600; }
.sidebar-foot a { color:var(--text-muted); text-decoration:underline; }
.sidebar-foot a:hover { color:var(--accent); }

.main { flex:1; min-width:0; }
.topbar { height:64px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; padding:0 26px; gap:20px; }
.topbar h1 { font-size:1.05rem; margin:0; font-weight:600; }
.topbar .sub { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); margin-top:2px; }
.avatar { width:32px; height:32px; border-radius:50%; background:var(--accent); color:#0B1220; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:.78rem; flex-shrink:0; }

.content { padding:26px; max-width:1100px; }
.banner-error { background:rgba(232,134,123,0.1); border:1px solid var(--critical); color:var(--critical); border-radius:10px; padding:14px 18px; font-size:.86rem; margin-bottom:20px; font-family:'IBM Plex Mono',monospace; }
.banner-ok { background:rgba(95,208,138,0.1); border:1px solid var(--good); color:var(--good); border-radius:10px; padding:14px 18px; font-size:.86rem; margin-bottom:20px; font-family:'IBM Plex Mono',monospace; }

.stat-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:14px; margin-bottom:26px; }
.stat-card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px 18px; }
.stat-card .v { font-size:1.6rem; font-weight:700; font-family:'Sora',sans-serif; }
.stat-card .l { font-size:.76rem; color:var(--text-muted); margin-top:4px; }
.stat-card.tone-critical .v { color:var(--critical); }
.stat-card.tone-warning .v { color:var(--warning); }
.stat-card.tone-good .v { color:var(--good); }

.panel { background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; margin-bottom:22px; }
.panel-head { padding:16px 20px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; }
.panel-head h2 { font-size:.92rem; margin:0; }
.panel-head .count { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); }
table { width:100%; border-collapse:collapse; }
th { text-align:left; font-size:.7rem; text-transform:uppercase; letter-spacing:.04em; color:var(--text-muted); font-weight:600; padding:10px 20px; border-bottom:1px solid var(--border); }
td { padding:13px 20px; font-size:.86rem; border-bottom:1px solid var(--border); vertical-align:middle; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:var(--surface-2); }
.name-cell { font-weight:600; }
.muted-cell { color:var(--text-muted); }
.money-cell { font-family:'IBM Plex Mono',monospace; font-variant-numeric:tabular-nums; }
.badge { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.68rem; padding:4px 9px; border-radius:999px; border:1px solid var(--border); text-transform:uppercase; letter-spacing:.03em; }
.badge.tone-critical { color:var(--critical); border-color:var(--critical); }
.badge.tone-warning { color:var(--warning); border-color:var(--warning); }
.badge.tone-good { color:var(--good); border-color:var(--good); }
.badge.tone-default { color:var(--text-muted); }
.empty-state { padding:40px 20px; text-align:center; color:var(--text-muted); font-size:.86rem; }
.stage-form select { width:auto; padding:7px 10px; font-size:.78rem; }

.form-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; padding:20px; }
.form-grid .span2 { grid-column:1/-1; }
.form-grid label { display:block; font-size:.72rem; color:var(--text-muted); margin-bottom:6px; font-family:'IBM Plex Mono',monospace; text-transform:uppercase; letter-spacing:.03em; }
.form-actions { padding:0 20px 20px; }

/* ---- simple settings-style rows (used by Copilot's "panel" component) ---- */
.card-list { display:flex; flex-direction:column; gap:10px; }
.info-row { display:flex; justify-content:space-between; padding:14px 18px; background:var(--surface-2); border:1px solid var(--border); border-radius:10px; font-size:.86rem; }
.info-row .k { color:var(--text-muted); }

/* ---- inline Copilot follow-up box on the full-screen generated view ---- */
.followup-row { display:flex; gap:8px; margin-bottom:14px; max-width:420px; }
.followup-row input { flex:1; }
.followup-row button { flex-shrink:0; }

/* ---- sample/AI-suggestion tag ---- */
.sample-tag { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.66rem; letter-spacing:.03em; text-transform:uppercase; color:var(--text-muted); border:1px dashed var(--border); border-radius:999px; padding:3px 10px; margin-left:10px; vertical-align:middle; }

/* ---- topbar Copilot toggle ---- */
.topbar-copilot-btn { display:inline-flex; align-items:center; gap:7px; font-family:'IBM Plex Sans',sans-serif; font-size:.82rem; font-weight:600; color:var(--text); background:var(--surface-2); border:1px solid var(--border); border-radius:8px; padding:8px 15px; text-decoration:none; white-space:nowrap; flex-shrink:0; }
.topbar-copilot-btn:hover { border-color:var(--accent); }
.topbar-copilot-btn.active { background:var(--accent); color:#0B1220; border-color:var(--accent); }

/* ---- Pilant Copilot (docked chat panel, right side) ---- */
.copilot-panel { width:380px; flex-shrink:0; background:var(--surface); border-left:1px solid var(--border); display:flex; flex-direction:column; height:100vh; position:sticky; top:0; }
.copilot-head { padding:16px 18px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; flex-shrink:0; }
.copilot-head h2 { font-size:.92rem; margin:0; font-family:'Sora',sans-serif; }
.copilot-head a { font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:var(--text-muted); text-decoration:underline; }
.copilot-head a:hover { color:var(--accent); }
.copilot-body { flex:1; min-height:0; overflow-y:auto; padding:16px 18px; display:flex; flex-direction:column; gap:12px; }
.copilot-empty { color:var(--text-muted); font-size:.82rem; line-height:1.55; }
.copilot-empty .ex { display:block; margin-top:8px; font-family:'IBM Plex Mono',monospace; font-size:.74rem; color:var(--accent); }
.copilot-bubble { border-radius:10px; padding:11px 14px; font-size:.84rem; line-height:1.45; max-width:92%; }
.copilot-bubble-user { background:var(--surface-2); border:1px solid var(--border); color:var(--text); align-self:flex-end; }
.copilot-bubble-assistant { background:transparent; border:1px dashed var(--border); color:var(--text-muted); align-self:flex-start; }
.copilot-card { border:1px solid var(--border); border-radius:10px; overflow:hidden; background:#fff; }
.copilot-card-head { display:flex; justify-content:space-between; align-items:center; gap:8px; padding:9px 12px; background:var(--surface-2); border-bottom:1px solid var(--border); }
.copilot-card-head span { font-family:'IBM Plex Mono',monospace; font-size:.68rem; color:var(--text-muted); text-transform:uppercase; letter-spacing:.03em; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.copilot-card-head a { font-family:'IBM Plex Mono',monospace; font-size:.68rem; color:var(--accent); text-decoration:none; flex-shrink:0; }
.copilot-card iframe { width:100%; height:320px; border:none; display:block; }
.copilot-foot { border-top:1px solid var(--border); padding:14px 18px; flex-shrink:0; }
.copilot-foot form { display:flex; gap:8px; }
.copilot-foot input[type=text] { flex:1; padding:10px 12px; font-size:.84rem; }
.copilot-foot button { flex-shrink:0; padding:10px 16px; }
"""

NAV_ITEMS = [
    ("dashboard", "/", "&#9635;", "Dashboard"),
    ("customers", "/customers", "&#128100;", "Customers"),
    ("deals", "/deals", "&#128200;", "Pipeline"),
    ("revenue", "/revenue", "&#128176;", "Revenue"),
    ("expenses", "/expenses", "&#128179;", "Expenses"),
]

STAGE_TONE = {"Lead": "default", "Qualified": "default", "Proposal": "warning", "Negotiation": "warning", "Won": "good", "Lost": "critical"}
STATUS_TONE = {"lead": "default", "prospect": "default", "active": "good", "churned": "critical"}


def _badge(text, tone):
    return f'<span class="badge tone-{_esc(tone)}">{_esc(text)}</span>'


def _sidebar(active):
    items = "".join(
        f'<li><a href="{href}" class="{"active" if key == active else ""}">'
        f'<span class="icon">{icon}</span>{label}</a></li>'
        for key, href, icon, label in NAV_ITEMS
    )
    return f'<ul class="navlist">{items}</ul>'


def _copilot_panel():
    """Docked Copilot chat panel — same technique as
    healthcare_dashboard.py's _copilot_panel(): real conversation history
    kept in the session, each entry either a plain text bubble or, for a
    completed request, a compact "generated view" card embedding the
    rendered screen in an iframe (renderer.render_html, self-contained so
    its CSS never collides with this page's own)."""
    from renderer import render_html

    transcript = session.get("copilot_transcript", [])
    return_to = request.path
    mid_clarify = bool(session.get("copilot_active_conversation"))

    if not transcript:
        body = (
            '<div class="copilot-empty">Ask about customers, deals, revenue, or expenses and '
            'Copilot will pull the real numbers on record and build a screen for you, right '
            'here in the chat.'
            '<span class="ex">&ldquo;what&rsquo;s our open pipeline worth?&rdquo;</span>'
            '<span class="ex">&ldquo;which customers are active?&rdquo;</span>'
            '</div>'
        )
    else:
        pieces = []
        for i, entry in enumerate(transcript):
            if entry.get("kind") == "render":
                view = entry["view"]
                query = entry.get("query", "")
                doc = render_html(view, query)
                heading = view.get("heading") or query or "Generated screen"
                pieces.append(f'<div class="copilot-bubble copilot-bubble-user">{_esc(query)}</div>')
                pieces.append(
                    '<div class="copilot-card">'
                    f'<div class="copilot-card-head"><span>{_esc(heading)}</span>'
                    f'<a href="/copilot/expand/{i}" target="_blank" rel="noopener">open full screen &#8599;</a></div>'
                    f'<iframe srcdoc="{_esc(doc)}"></iframe>'
                    '</div>'
                )
            else:
                role = entry.get("role")
                cls = "copilot-bubble-user" if role == "user" else "copilot-bubble-assistant"
                pieces.append(f'<div class="copilot-bubble {cls}">{_esc(entry.get("text", ""))}</div>')
        body = "".join(pieces)

    clear_link = f'<a href="/copilot/clear?return_to={_esc(return_to)}">clear</a>' if transcript else ""
    placeholder = "Type your answer..." if mid_clarify else "Ask about the business..."
    return (
        '<aside class="copilot-panel">'
        '<div class="copilot-head"><h2>Pilant Copilot</h2>'
        f'{clear_link}'
        '</div>'
        f'<div class="copilot-body">{body}</div>'
        '<div class="copilot-foot">'
        '<form method="post" action="/copilot/send">'
        f'<input type="hidden" name="return_to" value="{_esc(return_to)}">'
        f'<input type="text" name="message" placeholder="{_esc(placeholder)}" autocomplete="off">'
        '<button type="submit">Send</button>'
        '</form>'
        '</div>'
        '</aside>'
    )


def _shell(user, active, title, subtitle, body_html, error=None, ok=None, show_copilot=True):
    """show_copilot=False suppresses both the docked Copilot panel and its
    topbar toggle — used only by _render_generated_view_page(), which has
    its own full-screen follow-up box instead (same reasoning as
    healthcare_dashboard.py's _shell())."""
    error_html = f'<div class="banner-error">{_esc(error)}</div>' if error else ""
    ok_html = f'<div class="banner-ok">{_esc(ok)}</div>' if ok else ""
    initial = (user["name"] or "?")[0].upper()
    copilot_open = session.get("copilot_open", False)
    toggle_href = f"/copilot/toggle?return_to={_esc(request.path)}"
    toggle_label = "Close Copilot" if copilot_open else "Copilot"
    toggle_html = (
        f'<a class="topbar-copilot-btn{" active" if copilot_open else ""}" href="{toggle_href}">&#10024; {toggle_label}</a>'
        if show_copilot else ""
    )
    copilot_html = _copilot_panel() if (show_copilot and copilot_open) else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        f"<title>Pilant CRM — {_esc(title)}</title>"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
        f"<style>{SHARED_CSS}</style></head><body>"
        '<div class="shell">'
        '<div class="sidebar">'
        '<div class="brand"><span class="dot"></span>Pilant</div>'
        '<div class="brand-sub">internal CRM &middot; real data only</div>'
        f'{_sidebar(active)}'
        '<div class="sidebar-foot">'
        f'<div class="who">{_esc(user["name"])}</div>'
        f'<div>{_esc(user["role"])} &middot; <a href="/logout">log out</a></div>'
        '</div>'
        '</div>'
        '<div class="main">'
        '<div class="topbar">'
        f'<div><h1>{_esc(title)}</h1><div class="sub">{_esc(subtitle)}</div></div>'
        f'{toggle_html}'
        f'<div class="avatar">{_esc(initial)}</div>'
        '</div>'
        f'<div class="content">{error_html}{ok_html}{body_html}</div>'
        '</div>'
        f'{copilot_html}'
        '</div>'
        "</body></html>"
    )


def render_login(error=None):
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant CRM — log in</title>"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
        f"<style>{SHARED_CSS}</style></head><body>"
        '<div class="login-wrap"><div class="login-box">'
        '<p class="eyebrow">Pilant</p>'
        '<p class="subeyebrow">internal CRM &middot; customers, pipeline, revenue, expenses</p>'
        '<form class="login-form" method="post" action="/login">'
        '<input type="text" name="username" placeholder="username" autofocus required>'
        '<input type="password" name="password" placeholder="password" required>'
        '<button type="submit">Log in</button>'
        '</form>'
        f'{error_html}'
        '<div class="demo-accounts">'
        '<div><b>kowsick</b> / owner123 — owner</div>'
        '</div>'
        '</div></div>'
        "</body></html>"
    )


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
    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    user = get_user(session["username"])
    s = crm.dashboard_summary()

    stat_cards = (
        f'<div class="stat-card"><div class="v">{s["total_customers"]}</div><div class="l">Customers</div></div>'
        f'<div class="stat-card tone-warning"><div class="v">{_fmt_money(s["pipeline_value"])}</div><div class="l">Open pipeline</div></div>'
        f'<div class="stat-card tone-good"><div class="v">{_fmt_money(s["total_revenue"])}</div><div class="l">Total revenue</div></div>'
        f'<div class="stat-card"><div class="v">{_fmt_money(s["total_expenses"])}</div><div class="l">Total expenses</div></div>'
        f'<div class="stat-card tone-{"good" if s["net"] >= 0 else "critical"}"><div class="v">{_fmt_money(s["net"])}</div><div class="l">Net (all time)</div></div>'
    )
    month_cards = (
        f'<div class="stat-card tone-good"><div class="v">{_fmt_money(s["revenue_this_month"])}</div><div class="l">Revenue this month</div></div>'
        f'<div class="stat-card"><div class="v">{_fmt_money(s["expenses_this_month"])}</div><div class="l">Expenses this month</div></div>'
        f'<div class="stat-card tone-{"good" if s["net_this_month"] >= 0 else "critical"}"><div class="v">{_fmt_money(s["net_this_month"])}</div><div class="l">Net this month</div></div>'
    )

    stage_rows = "".join(
        f"<tr><td class=\"name-cell\">{_esc(stage)}</td><td>{s['deals_by_stage'].get(stage, 0)}</td>"
        f"<td class=\"money-cell\">{_fmt_money(s['value_by_stage'].get(stage, 0))}</td></tr>"
        for stage in crm.DEAL_STAGES
    )

    recent_deals = sorted(crm.list_deals(), key=lambda d: d["created_at"], reverse=True)[:5]
    recent_html = _deal_rows(recent_deals, {c["id"]: c for c in crm.list_customers()})

    body = f"""
    <div class="stat-grid">{stat_cards}</div>
    <div class="stat-grid">{month_cards}</div>
    <div class="panel">
      <div class="panel-head"><h2>Pipeline by stage</h2></div>
      <table><thead><tr><th>Stage</th><th>Deals</th><th>Value</th></tr></thead><tbody>{stage_rows}</tbody></table>
    </div>
    <div class="panel">
      <div class="panel-head"><h2>Recent deals</h2><a href="/deals" class="btn" style="padding:6px 12px;font-size:.76rem;">View pipeline</a></div>
      {recent_html}
    </div>
    """
    return _shell(user, "dashboard", "Dashboard", "real numbers, computed from what's actually been entered below", body)


def _customer_options(customers, selected=None):
    if not customers:
        return '<option value="">No customers yet — add one first</option>'
    opts = []
    for c in customers:
        sel = " selected" if c["id"] == selected else ""
        label = f'{c["name"]}{" — " + c["company"] if c.get("company") else ""}'
        opts.append(f'<option value="{_esc(c["id"])}"{sel}>{_esc(label)}</option>')
    return "".join(opts)


@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers_page():
    user = get_user(session["username"])
    error = None
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            error = "A customer needs at least a name."
        else:
            crm.add_customer(
                name=name,
                company=request.form.get("company", ""),
                email=request.form.get("email", ""),
                phone=request.form.get("phone", ""),
                status=request.form.get("status", "lead"),
                notes=request.form.get("notes", ""),
            )
            return redirect(url_for("customers_page"))

    customers = sorted(crm.list_customers(), key=lambda c: c["created_at"], reverse=True)
    status_opts = "".join(f'<option value="{s}">{s.title()}</option>' for s in crm.CUSTOMER_STATUSES)

    if not customers:
        rows_html = '<div class="empty-state">No customers yet — add your first one below.</div>'
    else:
        trs = "".join(
            "<tr>"
            f'<td class="name-cell">{_esc(c["name"])}</td>'
            f'<td class="muted-cell">{_esc(c.get("company") or "—")}</td>'
            f'<td class="muted-cell">{_esc(c.get("email") or "—")}</td>'
            f'<td>{_badge(c["status"].title(), STATUS_TONE.get(c["status"], "default"))}</td>'
            "</tr>"
            for c in customers
        )
        rows_html = f"<table><thead><tr><th>Name</th><th>Company</th><th>Email</th><th>Status</th></tr></thead><tbody>{trs}</tbody></table>"

    body = f"""
    <div class="panel">
      <div class="panel-head"><h2>Customers</h2><span class="count">{len(customers)}</span></div>
      {rows_html}
    </div>
    <div class="panel">
      <div class="panel-head"><h2>Add a customer</h2></div>
      <form method="post" action="/customers">
        <div class="form-grid">
          <div><label>Name</label><input type="text" name="name" required></div>
          <div><label>Company</label><input type="text" name="company"></div>
          <div><label>Email</label><input type="email" name="email"></div>
          <div><label>Phone</label><input type="text" name="phone"></div>
          <div><label>Status</label><select name="status">{status_opts}</select></div>
          <div class="span2"><label>Notes</label><textarea name="notes" rows="2"></textarea></div>
        </div>
        <div class="form-actions"><button type="submit">Add customer</button></div>
      </form>
    </div>
    """
    return _shell(user, "customers", "Customers", f"{len(customers)} on record", body, error=error)


def _deal_rows(deals, customers_by_id):
    if not deals:
        return '<div class="empty-state">No deals yet.</div>'
    stage_opts_by_deal = "".join(f'<option value="{s}">{s}</option>' for s in crm.DEAL_STAGES)
    trs = []
    for d in deals:
        cust = customers_by_id.get(d["customer_id"])
        cust_name = cust["name"] if cust else "—"
        opts = "".join(
            f'<option value="{s}"{" selected" if s == d["stage"] else ""}>{s}</option>'
            for s in crm.DEAL_STAGES
        )
        trs.append(
            "<tr>"
            f'<td class="name-cell">{_esc(d["title"] or "Untitled deal")}</td>'
            f'<td class="muted-cell">{_esc(cust_name)}</td>'
            f'<td class="money-cell">{_fmt_money(d["amount"])}</td>'
            f'<td>{_badge(d["stage"], STAGE_TONE.get(d["stage"], "default"))}</td>'
            "<td>"
            f'<form class="stage-form" method="post" action="/deals/{_esc(d["id"])}/stage">'
            f'<select name="stage" onchange="this.form.submit()">{opts}</select>'
            "</form>"
            "</td>"
            "</tr>"
        )
    return f"<table><thead><tr><th>Deal</th><th>Customer</th><th>Amount</th><th>Stage</th><th>Move stage</th></tr></thead><tbody>{''.join(trs)}</tbody></table>"


@app.route("/deals", methods=["GET", "POST"])
@login_required
def deals_page():
    user = get_user(session["username"])
    error = None
    customers = sorted(crm.list_customers(), key=lambda c: c["name"])
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        customer_id = request.form.get("customer_id") or ""
        if not customer_id:
            error = "Pick a customer for this deal (add one on the Customers page first if needed)."
        elif not title:
            error = "A deal needs a title."
        else:
            crm.add_deal(
                customer_id=customer_id,
                title=title,
                amount=request.form.get("amount", "0"),
                stage=request.form.get("stage", "Lead"),
                expected_close_date=request.form.get("expected_close_date", ""),
                notes=request.form.get("notes", ""),
            )
            return redirect(url_for("deals_page"))

    deals = sorted(crm.list_deals(), key=lambda d: d["created_at"], reverse=True)
    customers_by_id = {c["id"]: c for c in customers}
    rows_html = _deal_rows(deals, customers_by_id)
    stage_opts = "".join(f'<option value="{s}">{s}</option>' for s in crm.DEAL_STAGES)

    body = f"""
    <div class="panel">
      <div class="panel-head"><h2>Sales pipeline</h2><span class="count">{len(deals)}</span></div>
      {rows_html}
    </div>
    <div class="panel">
      <div class="panel-head"><h2>Add a deal</h2></div>
      <form method="post" action="/deals">
        <div class="form-grid">
          <div><label>Customer</label><select name="customer_id">{_customer_options(customers)}</select></div>
          <div><label>Deal title</label><input type="text" name="title" placeholder="e.g. Pilant Studio — annual plan" required></div>
          <div><label>Amount ($)</label><input type="number" name="amount" step="0.01" min="0" required></div>
          <div><label>Stage</label><select name="stage">{stage_opts}</select></div>
          <div><label>Expected close date</label><input type="date" name="expected_close_date"></div>
          <div class="span2"><label>Notes</label><textarea name="notes" rows="2"></textarea></div>
        </div>
        <div class="form-actions"><button type="submit">Add deal</button></div>
      </form>
    </div>
    """
    return _shell(user, "deals", "Pipeline", f"{len(deals)} deals on record", body, error=error)


@app.route("/deals/<deal_id>/stage", methods=["POST"])
@login_required
def update_deal_stage_route(deal_id):
    stage = request.form.get("stage", "")
    if stage in crm.DEAL_STAGES:
        crm.update_deal_stage(deal_id, stage)
    return redirect(url_for("deals_page"))


@app.route("/revenue", methods=["GET", "POST"])
@login_required
def revenue_page():
    user = get_user(session["username"])
    error = None
    if request.method == "POST":
        amount = request.form.get("amount", "0")
        try:
            if float(amount) <= 0:
                error = "Enter an amount greater than zero."
        except ValueError:
            error = "That doesn't look like a number."
        if not error:
            crm.add_revenue(
                amount=amount,
                date=request.form.get("date", ""),
                source=request.form.get("source", ""),
                note=request.form.get("note", ""),
            )
            return redirect(url_for("revenue_page"))

    entries = sorted(crm.list_revenue(), key=lambda r: r["date"], reverse=True)
    if not entries:
        rows_html = '<div class="empty-state">No revenue logged yet.</div>'
    else:
        trs = "".join(
            "<tr>"
            f'<td class="muted-cell">{_esc(r["date"])}</td>'
            f'<td class="name-cell">{_esc(r["source"] or "—")}</td>'
            f'<td class="money-cell">{_fmt_money(r["amount"])}</td>'
            f'<td class="muted-cell">{_esc(("from pipeline" if r.get("deal_id") else r.get("note") or "—"))}</td>'
            "</tr>"
            for r in entries
        )
        rows_html = f"<table><thead><tr><th>Date</th><th>Source</th><th>Amount</th><th>Note</th></tr></thead><tbody>{trs}</tbody></table>"

    total = sum(r["amount"] for r in entries)
    body = f"""
    <div class="stat-grid"><div class="stat-card tone-good"><div class="v">{_fmt_money(total)}</div><div class="l">Total revenue logged</div></div></div>
    <div class="panel">
      <div class="panel-head"><h2>Revenue</h2><span class="count">{len(entries)} entries</span></div>
      {rows_html}
    </div>
    <div class="panel">
      <div class="panel-head"><h2>Log revenue</h2></div>
      <form method="post" action="/revenue">
        <div class="form-grid">
          <div><label>Amount ($)</label><input type="number" name="amount" step="0.01" min="0" required></div>
          <div><label>Date</label><input type="date" name="date"></div>
          <div class="span2"><label>Source</label><input type="text" name="source" placeholder="e.g. Acme Corp — renewal"></div>
          <div class="span2"><label>Note</label><textarea name="note" rows="2"></textarea></div>
        </div>
        <div class="form-actions"><button type="submit">Log revenue</button></div>
      </form>
      <p style="padding:0 20px 18px;font-size:.78rem;color:var(--text-muted);">Moving a pipeline deal to "Won" logs its revenue here automatically — use this form for revenue that isn't tied to a tracked deal.</p>
    </div>
    """
    return _shell(user, "revenue", "Revenue", f"{_fmt_money(total)} logged all time", body, error=error)


@app.route("/expenses", methods=["GET", "POST"])
@login_required
def expenses_page():
    user = get_user(session["username"])
    error = None
    if request.method == "POST":
        amount = request.form.get("amount", "0")
        try:
            if float(amount) <= 0:
                error = "Enter an amount greater than zero."
        except ValueError:
            error = "That doesn't look like a number."
        if not error:
            crm.add_expense(
                amount=amount,
                date=request.form.get("date", ""),
                category=request.form.get("category", "Other"),
                note=request.form.get("note", ""),
            )
            return redirect(url_for("expenses_page"))

    entries = sorted(crm.list_expenses(), key=lambda e: e["date"], reverse=True)
    if not entries:
        rows_html = '<div class="empty-state">No expenses logged yet.</div>'
    else:
        trs = "".join(
            "<tr>"
            f'<td class="muted-cell">{_esc(e["date"])}</td>'
            f'<td class="name-cell">{_esc(e["category"])}</td>'
            f'<td class="money-cell">{_fmt_money(e["amount"])}</td>'
            f'<td class="muted-cell">{_esc(e.get("note") or "—")}</td>'
            "</tr>"
            for e in entries
        )
        rows_html = f"<table><thead><tr><th>Date</th><th>Category</th><th>Amount</th><th>Note</th></tr></thead><tbody>{trs}</tbody></table>"

    total = sum(e["amount"] for e in entries)
    category_opts = "".join(f'<option value="{c}">{c}</option>' for c in crm.EXPENSE_CATEGORIES)
    body = f"""
    <div class="stat-grid"><div class="stat-card"><div class="v">{_fmt_money(total)}</div><div class="l">Total expenses logged</div></div></div>
    <div class="panel">
      <div class="panel-head"><h2>Expenses</h2><span class="count">{len(entries)} entries</span></div>
      {rows_html}
    </div>
    <div class="panel">
      <div class="panel-head"><h2>Log an expense</h2></div>
      <form method="post" action="/expenses">
        <div class="form-grid">
          <div><label>Amount ($)</label><input type="number" name="amount" step="0.01" min="0" required></div>
          <div><label>Date</label><input type="date" name="date"></div>
          <div><label>Category</label><select name="category">{category_opts}</select></div>
          <div class="span2"><label>Note</label><textarea name="note" rows="2"></textarea></div>
        </div>
        <div class="form-actions"><button type="submit">Log expense</button></div>
      </form>
    </div>
    """
    return _shell(user, "expenses", "Expenses", f"{_fmt_money(total)} logged all time", body, error=error)


# Session cookie is signed but browser-side (no server-side store), so it's
# capped around 4KB and also carries the logged-in username — keep the chat
# history short so a long conversation can never break login (same as
# healthcare_dashboard.py's MAX_COPILOT_TRANSCRIPT).
MAX_COPILOT_TRANSCRIPT = 6

# agent_pilant_crm's system prompt forces a real data request through
# get_crm_data + render_view, which is right for real questions but leaves
# something like "hi" with nothing to fetch and nothing genuinely ambiguous
# to ask about. Catch greetings/small talk here, before the model is ever
# called, and answer them directly instead (same pattern as
# healthcare_dashboard.py's _looks_like_smalltalk).
_SMALLTALK_WORDS = {
    "hi", "hello", "hey", "yo", "hiya", "howdy", "sup", "thanks", "thank",
    "ok", "okay", "cool", "nice", "test", "hmm", "bye", "goodbye",
}


def _looks_like_smalltalk(text):
    t = text.strip().lower().rstrip("!.?")
    if not t:
        return True
    words = [w.strip(",.!?") for w in t.split()]
    return len(words) <= 3 and all(w in _SMALLTALK_WORDS for w in words if w)


SMALLTALK_REPLY = (
    "Hi! Ask me about customers, the pipeline, revenue, or expenses and I'll pull the real "
    "numbers on record and build a screen for it — try something like "
    "“what's our open pipeline worth?” or “which customers are active?”"
)


@app.route("/copilot/toggle")
@login_required
def copilot_toggle():
    session["copilot_open"] = not session.get("copilot_open", False)
    return redirect(request.args.get("return_to") or url_for("dashboard"))


@app.route("/copilot/clear")
@login_required
def copilot_clear():
    session.pop("copilot_transcript", None)
    session.pop("copilot_active_conversation", None)
    return redirect(request.args.get("return_to") or url_for("dashboard"))


@app.route("/copilot/send", methods=["POST"])
@login_required
def copilot_send():
    """A message from the docked Copilot panel. Runs agent_pilant_crm.run_agent
    and appends to a persistent, growing chat transcript, redirecting back to
    whatever page you were on (return_to) so the panel stays docked wherever
    you're working — same shape as healthcare_dashboard.py's copilot_send()."""
    text = (request.form.get("message") or "").strip()
    return_to = request.form.get("return_to") or url_for("dashboard")
    # "/copilot/expand/latest" is the sentinel _generated_view_followup_html()'s
    # form uses — only a real fallback target for a fresh render. A
    # clarifying question, plain-text reply, or error has nothing full-screen
    # to show, so those fall back to the docked panel on the dashboard.
    wants_expand_on_render = return_to == "/copilot/expand/latest"
    if wants_expand_on_render:
        return_to = url_for("dashboard")
    if not text:
        return redirect(return_to)

    session["copilot_open"] = True
    transcript = session.get("copilot_transcript", [])
    convo = session.get("copilot_active_conversation")

    if not convo and _looks_like_smalltalk(text):
        transcript.append({"role": "user", "text": text})
        transcript.append({"role": "assistant", "text": SMALLTALK_REPLY})
        session["copilot_transcript"] = transcript[-MAX_COPILOT_TRANSCRIPT:]
        return redirect(return_to)

    try:
        from agent_pilant_crm import run_agent
    except Exception as e:
        transcript.append({"role": "user", "text": text})
        transcript.append({
            "role": "assistant",
            "text": f"Copilot isn't available right now — the AI backend isn't configured ({e}).",
        })
        session["copilot_transcript"] = transcript[-MAX_COPILOT_TRANSCRIPT:]
        return redirect(return_to)

    transcript.append({"role": "user", "text": text})

    if convo:
        messages = convo["messages"] + [{"role": "user", "content": text}]
        try:
            result = run_agent(verbose=True, messages=messages, fetched_data=convo.get("fetched_data", False))
        except Exception as e:
            result = {"error": str(e)}
        original_query = convo["query"]
    else:
        try:
            result = run_agent(text, verbose=True)
        except Exception as e:
            result = {"error": str(e)}
        original_query = text

    if "clarify" in result:
        transcript.append({"role": "assistant", "text": result["clarify"]})
        session["copilot_active_conversation"] = {
            "messages": result["messages"],
            "fetched_data": result.get("fetched_data", False),
            "query": original_query,
        }
    else:
        session.pop("copilot_active_conversation", None)
        if "error" in result:
            transcript.append({
                "role": "assistant",
                "text": f"Couldn't generate a screen for that ({result['error']}). Try rephrasing.",
            })
        elif "text" in result:
            transcript.append({"role": "assistant", "text": result["text"]})
        else:
            transcript.append({"kind": "render", "query": original_query, "view": result["render"]})
            if wants_expand_on_render:
                return_to = url_for("copilot_expand_latest")

    session["copilot_transcript"] = transcript[-MAX_COPILOT_TRANSCRIPT:]
    return redirect(return_to)


@app.route("/copilot/expand/<int:idx>")
@login_required
def copilot_expand(idx):
    user = get_user(session["username"])
    transcript = session.get("copilot_transcript", [])
    if idx < 0 or idx >= len(transcript) or transcript[idx].get("kind") != "render":
        return redirect(url_for("dashboard"))
    entry = transcript[idx]
    return _render_generated_view_page(user, entry["view"], entry.get("query", ""))


@app.route("/copilot/expand/latest")
@login_required
def copilot_expand_latest():
    """Same rendering as copilot_expand(<int:idx>), but always shows the most
    recent render-kind transcript entry — the redirect target
    _generated_view_followup_html()'s form posts to."""
    user = get_user(session["username"])
    transcript = session.get("copilot_transcript", [])
    render_indices = [i for i, e in enumerate(transcript) if e.get("kind") == "render"]
    if not render_indices:
        return redirect(url_for("dashboard"))
    entry = transcript[render_indices[-1]]
    return _render_generated_view_page(user, entry["view"], entry.get("query", ""))


def _badge_from(b):
    """Same shape as the STAGE_TONE/STATUS_TONE-driven _badge() above, but
    for a component-level badge dict ({"text": ..., "tone": ...}) from a
    render_view component — the model already chose the tone (see
    agent_pilant_crm.py's SYSTEM prompt), so this just renders it."""
    if not b:
        return ""
    tone = b.get("tone", "default")
    text = b.get("text", "")
    return f'<span class="badge tone-{_esc(tone)}">{_esc(text)}</span>'


def _dashboard_component_html(c):
    """Converts one render_view component (schema.py's generic shape) into
    HTML styled with THIS app's own CSS (SHARED_CSS above) instead of
    renderer.py's generic floating "device mockup" card — same reasoning
    and approach as healthcare_dashboard.py's _dashboard_component_html(),
    built in from the start this time rather than retrofitted.

    A row's fields (name/note/badge) are generic — a Copilot answer might
    be about customers, deals, revenue, or expenses, each shaped
    differently — so list rows render as a generic two-column Name/Detail
    table with a status badge, using this file's own .name-cell/.muted-cell
    classes (already defined above for the Customers/Pipeline pages)."""
    ctype = c.get("type")

    if ctype == "stat_grid":
        cards = "".join(
            f'<div class="stat-card tone-{_esc(s.get("tone", "default"))}">'
            f'<div class="v">{_esc(s.get("value"))}</div><div class="l">{_esc(s.get("label"))}</div></div>'
            for s in (c.get("stats") or [])
        )
        return f'<div class="stat-grid">{cards}</div>'

    if ctype == "list":
        rows = c.get("rows") or []
        title = c.get("title") or c.get("subtitle")
        head = (
            f'<div class="panel-head"><h2>{_esc(title)}</h2><span class="count">{len(rows)}</span></div>'
            if title else ""
        )
        if not rows:
            body = '<div class="empty-state">Nothing matched — try a broader request.</div>'
        else:
            trs = "".join(
                "<tr>"
                f'<td class="name-cell">{_esc(r.get("name"))}</td>'
                f'<td class="muted-cell">{_esc(r.get("note") or "")}</td>'
                f'<td>{_badge_from(r.get("badge"))}</td>'
                "</tr>"
                for r in rows
            )
            body = f"<table><thead><tr><th>Name</th><th>Detail</th><th>Status</th></tr></thead><tbody>{trs}</tbody></table>"
        return f'<div class="panel">{head}{body}</div>'

    if ctype == "panel":
        fields_html = "".join(
            '<div class="info-row">'
            f'<span class="k">{_esc(f.get("label"))}</span><span>{_esc(f.get("value"))}</span>'
            "</div>"
            for f in (c.get("fields") or [])
        )
        title = c.get("title")
        subtitle = c.get("subtitle")
        sub_html = f'<span class="count">{_esc(subtitle)}</span>' if subtitle else ""
        head = f'<div class="panel-head"><h2>{_esc(title)}</h2>{sub_html}</div>' if title else ""
        badge_html = _badge_from(c.get("badge"))
        badge_row = f'<div style="padding:0 20px 16px;">{badge_html}</div>' if badge_html else ""
        return f'<div class="panel">{head}<div class="card-list" style="padding:16px 20px;gap:8px;">{fields_html}</div>{badge_row}</div>'

    if ctype == "suggestions":
        items = [s for s in (c.get("suggestions") or []) if isinstance(s, str) and s.strip()]
        if not items:
            return ""
        title = c.get("title") or "Suggested actions"
        lis = "".join(f"<li>{_esc(s)}</li>" for s in items)
        return (
            f'<div class="panel"><div class="panel-head"><h2>{_esc(title)}'
            '<span class="sample-tag">AI suggestion</span></h2></div>'
            f'<div style="padding:16px 20px;"><ul style="margin:0;padding-left:18px;font-size:.86rem;">{lis}</ul></div></div>'
        )

    return (
        '<div class="panel"><div style="padding:16px 20px;color:var(--text-muted);">'
        f'Unrecognized component: {_esc(ctype)}</div></div>'
    )


def _generated_view_followup_html():
    """The docked Copilot panel's own input box is deliberately NOT shown on
    the generated-view page (see _shell()'s show_copilot docstring) — this
    is its full-screen-page equivalent, styled with .followup-row. Posts to
    /copilot/send with return_to="/copilot/expand/latest"."""
    return (
        '<form method="post" action="/copilot/send" class="followup-row" style="max-width:560px;margin-bottom:22px;">'
        '<input type="hidden" name="return_to" value="/copilot/expand/latest">'
        '<input type="text" name="message" placeholder="Ask a follow-up — e.g. \'now show me the churned customers\'" autofocus>'
        '<button type="submit">Ask</button>'
        "</form>"
    )


def _render_generated_view_page(user, view, query):
    """Renders a Copilot-generated screen as a genuine page inside Pilant
    CRM's own dashboard shell — sidebar, topbar, real nav — instead of
    renderer.py's generic floating "device mockup" card. Used by
    copilot_expand() / copilot_expand_latest() (the "open full screen" flow)."""
    components_html = "".join(_dashboard_component_html(c) for c in (view.get("components") or []))
    heading = view.get("heading") or query or "Generated view"
    subtitle = f'Copilot answer to: "{query}"' if query else "Copilot-generated view"
    body = _generated_view_followup_html() + components_html
    return _shell(user, None, heading, subtitle, body, show_copilot=False)


if __name__ == "__main__":
    print("Pilant internal CRM running at http://localhost:5007", file=sys.stderr)
    app.run(host="127.0.0.1", port=5007, debug=False)
