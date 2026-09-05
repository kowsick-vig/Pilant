"""
A dummy internal admin tool for a fictional clothing retailer ("Harriet &
Co Ops") with Pilant embedded inside it as a widget — not as the whole
page, the way server.py's hosted portal is. This is the "embedded widget"
integration model from the architecture doc: Pilant lives as one card on
someone else's real page, answering a typed request inline without a full
navigation, backed by the same real agent loop (agent_retail.run_agent)
and a new mock connector (connectors_retail.py) standing in for Harriet &
Co's actual order/inventory system.

Everything around the widget — the sidebar, the topbar, the decorative
stat cards — is static dummy chrome with no real data behind it. Only the
"Ask Ops" card is real: it POSTs to /widget/generate, which runs the
actual Composition Engine and returns a rendered HTML fragment that gets
injected into the page without a reload, the same way a real embedded
widget script would behave inside a real customer's app.
"""

import html as _html
import sys

from flask import Flask, request

from agent_retail import run_agent
from renderer import render_fragment

app = Flask(__name__)


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


# Just the component-rendering CSS from renderer.py (the .app-window scope
# and everything under it) — deliberately NOT renderer.py's :root/body/page
# rules, since those would repaint this whole host page in Pilant's own
# dark theme instead of Harriet & Co's. Only the widget result box should
# look like Pilant; the page around it should look like someone else's app.
PILANT_WIDGET_CSS = """
.app-window { --app-bg:#F4F6FB; --app-card:#FFFFFF; --app-border:#E3E7F1; --app-text:#171B2E; --app-muted:#6B7385; --app-accent:#5B6EF5; --app-critical:#C6392F; --app-critical-bg:#FBEAE8; --app-warning:#A56A0E; --app-warning-bg:#FBF1DE; --app-good:#227A55; --app-good-bg:#E6F5EE; background:var(--app-bg); color:var(--app-text); padding:22px; min-height:60px; border-radius:10px; margin-top:14px; }
.app-window:empty { padding:0; min-height:0; margin-top:0; }
.app-h1 { font-family:'Sora',sans-serif; font-weight:600; font-size:1.15rem; margin:0 0 4px; }
.app-meta { font-size:.83rem; color:var(--app-muted); margin:0 0 16px; }
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin-bottom:16px; }
.stat-card { background:var(--app-card); border:1px solid var(--app-border); border-left:3px solid var(--app-border); border-radius:6px; padding:13px 14px; }
.stat-card.tone-critical { border-left-color:var(--app-critical); }
.stat-card.tone-warning { border-left-color:var(--app-warning); }
.stat-card.tone-good { border-left-color:var(--app-good); }
.stat-label { font-size:.68rem; letter-spacing:.05em; text-transform:uppercase; color:var(--app-muted); display:block; margin-bottom:6px; font-family:'IBM Plex Mono',monospace; }
.stat-value { font-family:'Sora',sans-serif; font-weight:700; font-size:1.3rem; }
.panel { background:var(--app-card); border:1px solid var(--app-border); border-radius:8px; padding:16px 18px; margin-bottom:12px; }
.panel-head { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; margin-bottom:10px; }
.panel-title { font-family:'Sora',sans-serif; font-weight:600; font-size:.96rem; margin:0 0 3px; }
.panel-sub { font-size:.8rem; color:var(--app-muted); margin:0; }
.badge { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.66rem; font-weight:500; letter-spacing:.03em; text-transform:uppercase; padding:3px 9px; border-radius:4px; flex:none; }
.badge.critical { background:var(--app-critical-bg); color:var(--app-critical); }
.badge.warning { background:var(--app-warning-bg); color:var(--app-warning); }
.badge.good { background:var(--app-good-bg); color:var(--app-good); }
.badge.default { background:var(--app-border); color:var(--app-muted); }
.kv-grid { display:grid; grid-template-columns:1fr 1fr; gap:10px 24px; margin:14px 0; }
.kv-grid dt { font-size:.66rem; text-transform:uppercase; letter-spacing:.04em; color:var(--app-muted); font-family:'IBM Plex Mono',monospace; margin-bottom:2px; }
.kv-grid dd { margin:0; font-size:.84rem; }
.app-btn { font-family:'IBM Plex Sans',sans-serif; font-weight:500; font-size:.8rem; background:var(--app-accent); color:#fff; border:none; border-radius:6px; padding:8px 15px; cursor:pointer; }
.list-row { display:flex; justify-content:space-between; align-items:center; gap:14px; padding:11px 0; border-bottom:1px solid var(--app-border); }
.list-row:last-child { border-bottom:none; }
.list-name { font-weight:600; font-size:.86rem; }
.list-note { font-size:.76rem; color:var(--app-muted); margin-top:2px; }
.list-action { font-size:.74rem; color:var(--app-accent); font-weight:500; white-space:nowrap; }
"""

SITE_CSS = """
:root { --hc-bg:#FAF7F2; --hc-surface:#FFFFFF; --hc-border:#E8E1D6; --hc-text:#2B2620; --hc-muted:#8A8172; --hc-accent:#B5652B; --hc-sidebar:#231F1A; --hc-sidebar-text:#EFE8DC; --hc-sidebar-muted:#9C9284; }
* { box-sizing:border-box; }
body { margin:0; background:var(--hc-bg); color:var(--hc-text); font-family:'IBM Plex Sans',system-ui,sans-serif; }
.shell { display:flex; min-height:100vh; }
.sidebar { width:220px; flex:none; background:var(--hc-sidebar); color:var(--hc-sidebar-text); padding:26px 20px; }
.brand { font-family:'Sora',sans-serif; font-weight:700; font-size:1.05rem; margin:0 0 2px; }
.brand-sub { font-size:.72rem; color:var(--hc-sidebar-muted); margin:0 0 30px; font-family:'IBM Plex Mono',monospace; letter-spacing:.03em; }
.nav a { display:block; padding:9px 10px; border-radius:6px; color:var(--hc-sidebar-muted); text-decoration:none; font-size:.86rem; margin-bottom:2px; }
.nav a.active { background:rgba(255,255,255,.08); color:var(--hc-sidebar-text); font-weight:500; }
.main { flex:1; padding:32px 40px; max-width:1120px; }
.topbar { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
.page-title { font-family:'Sora',sans-serif; font-weight:600; font-size:1.4rem; margin:0; }
.user-chip { font-size:.8rem; color:var(--hc-muted); }
.alert-banner { display:flex; align-items:center; gap:10px; background:#FBF1DE; border:1px solid #E9D9AE; color:#8A5A0E; border-radius:8px; padding:10px 16px; font-size:.8rem; margin-bottom:20px; font-family:'IBM Plex Mono',monospace; }
.stat-row { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:14px; margin-bottom:26px; }
.dumb-stat { background:var(--hc-surface); border:1px solid var(--hc-border); border-radius:10px; padding:16px 18px; }
.dumb-stat-label { font-size:.7rem; text-transform:uppercase; letter-spacing:.05em; color:var(--hc-muted); font-family:'IBM Plex Mono',monospace; margin-bottom:8px; display:block; }
.dumb-stat-value { font-family:'Sora',sans-serif; font-weight:700; font-size:1.45rem; }
.widget-card { background:var(--hc-surface); border:1px solid var(--hc-border); border-radius:12px; padding:22px 24px; margin-bottom:22px; }
.dash-grid { display:grid; grid-template-columns:1.6fr 1fr; gap:20px; }
.side-col { display:flex; flex-direction:column; gap:20px; }
.panel-box { background:var(--hc-surface); border:1px solid var(--hc-border); border-radius:12px; padding:20px 22px; }
.panel-box-title { font-family:'Sora',sans-serif; font-weight:600; font-size:.92rem; margin:0 0 14px; }
.activity-row { display:flex; justify-content:space-between; gap:14px; padding:10px 0; border-bottom:1px solid var(--hc-border); font-size:.84rem; }
.activity-row:last-child { border-bottom:none; }
.activity-time { color:var(--hc-muted); font-size:.72rem; font-family:'IBM Plex Mono',monospace; white-space:nowrap; }
.mini-row { display:flex; justify-content:space-between; padding:8px 0; border-bottom:1px solid var(--hc-border); font-size:.82rem; }
.mini-row:last-child { border-bottom:none; }
.mini-row-count { color:var(--hc-muted); font-size:.78rem; font-family:'IBM Plex Mono',monospace; }
.task-row { padding:8px 0; border-bottom:1px solid var(--hc-border); font-size:.82rem; display:flex; align-items:center; gap:8px; }
.task-row:last-child { border-bottom:none; }
.task-dot { width:6px; height:6px; border-radius:50%; background:var(--hc-accent); flex:none; }
.widget-card { position:relative; overflow:hidden; }
.widget-card::before { content:""; position:absolute; top:0; left:0; right:0; height:3px; background:linear-gradient(90deg, var(--hc-accent), #E8A15C); }
.widget-head { display:flex; align-items:center; gap:10px; margin-bottom:6px; }
.widget-badge { display:inline-flex; align-items:center; gap:5px; font-family:'IBM Plex Mono',monospace; font-size:.66rem; font-weight:600; letter-spacing:.05em; text-transform:uppercase; background:#1A2340; color:#8E9CFF; padding:4px 10px 4px 8px; border-radius:999px; }
.widget-badge svg { width:11px; height:11px; flex:none; }
.widget-title { font-family:'Sora',sans-serif; font-weight:600; font-size:1rem; }
.widget-sub { font-size:.82rem; color:var(--hc-muted); margin:0 0 18px; }
#pilant-form { display:flex; gap:10px; background:var(--hc-bg); border:1px solid var(--hc-border); border-radius:12px; padding:6px; transition:border-color .15s ease, box-shadow .15s ease; }
#pilant-form:focus-within { border-color:var(--hc-accent); box-shadow:0 0 0 3px rgba(181,101,43,.12); }
#pilant-query { flex:1; border:none; background:transparent; border-radius:8px; padding:10px 12px; font-size:.92rem; font-family:'IBM Plex Sans',sans-serif; color:var(--hc-text); }
#pilant-query:focus { outline:none; }
#pilant-query::placeholder { color:var(--hc-muted); }
#pilant-form button { display:inline-flex; align-items:center; justify-content:center; gap:7px; min-width:64px; background:var(--hc-accent); color:#fff; border:none; border-radius:8px; padding:10px 18px; font-size:.86rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; transition:opacity .15s ease, transform .1s ease; }
#pilant-form button:hover:not(:disabled) { opacity:.92; }
#pilant-form button:active:not(:disabled) { transform:scale(.97); }
#pilant-form button:disabled { opacity:.55; cursor:default; }
.spinner { width:13px; height:13px; border-radius:50%; border:2px solid rgba(255,255,255,.4); border-top-color:#fff; display:none; animation:pilant-spin .7s linear infinite; }
#pilant-form button.loading .spinner { display:inline-block; }
#pilant-form button.loading .btn-label { display:none; }
@keyframes pilant-spin { to { transform:rotate(360deg); } }
.widget-chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
.widget-chip { font-family:'IBM Plex Mono',monospace; font-size:.71rem; color:var(--hc-muted); border:1px solid var(--hc-border); border-radius:999px; padding:6px 13px; background:var(--hc-surface); cursor:pointer; transition:border-color .15s ease, color .15s ease, transform .1s ease; }
.widget-chip:hover { border-color:var(--hc-accent); color:var(--hc-text); transform:translateY(-1px); }
.widget-status { display:flex; align-items:center; gap:7px; font-size:.78rem; color:var(--hc-muted); margin-top:12px; font-family:'IBM Plex Mono',monospace; min-height:1.1em; }
.widget-status.is-error { color:#C6392F; }
.app-window { animation:pilant-fadein .25s ease; }
.app-window:empty { animation:none; }
@keyframes pilant-fadein { from { opacity:0; transform:translateY(4px); } to { opacity:1; transform:translateY(0); } }
"""

NAV_ITEMS = ["Dashboard", "Orders", "Inventory", "Customers", "Reports"]

EXAMPLE_PROMPTS = [
    "what orders need attention right now?",
    "what's low on stock?",
    "show me anything that's out of stock",
]

# All of the below is static decorative chrome — no connector, no agent call.
# It exists purely so the page reads like a busy real internal tool instead
# of a bare demo, matching the "make it look real" ask. Only the Ask Ops
# card above is ever backed by real (mock) data through Pilant.
ACTIVITY_FEED = [
    ("Order HC-10432 flagged as delayed by carrier", "12m ago"),
    ("New order placed — HC-10433, R. Alvarez", "38m ago"),
    ("Return requested for HC-10431 (wrong size)", "2h ago"),
    ("Merino Crew Knit — Oat marked out of stock", "3h ago"),
    ("Order HC-10428 moved to processing", "4h ago"),
    ("Linen Wrap Dress — Sage flagged low stock (2 left)", "6h ago"),
    ("Order HC-10425 shipped to A. Petrova", "2d ago"),
]

TOP_PRODUCTS = [
    ("Linen Wrap Dress — Black", "41 sold"),
    ("Tailored Wide Trouser — Navy", "29 sold"),
    ("Merino Crew Knit — Oat", "24 sold"),
]

STAFF_TASKS = [
    "Reorder Merino Crew Knit — Oat",
    "Follow up with carrier on HC-10432",
    "Approve return for HC-10431",
    "Restock Linen Wrap Dress — Sage",
]

PAGE = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Harriet & Co — Ops</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
    "<style>{{SITE_CSS}}{{PILANT_CSS}}</style></head><body>"
    "<div class=\"shell\">"
    "<aside class=\"sidebar\">"
    "<p class=\"brand\">Harriet &amp; Co</p>"
    "<p class=\"brand-sub\">STORE OPS</p>"
    "<nav class=\"nav\">{{NAV}}</nav>"
    "</aside>"
    "<main class=\"main\">"
    "<div class=\"topbar\"><h1 class=\"page-title\">Dashboard</h1>"
    "<div class=\"user-chip\">Priya Shah &middot; Store Ops</div></div>"
    "<div class=\"alert-banner\">3 orders need attention &middot; 2 items low or out of stock</div>"
    "<div class=\"stat-row\">"
    "<div class=\"dumb-stat\"><span class=\"dumb-stat-label\">Orders today</span><span class=\"dumb-stat-value\">128</span></div>"
    "<div class=\"dumb-stat\"><span class=\"dumb-stat-label\">Revenue</span><span class=\"dumb-stat-value\">$4,320</span></div>"
    "<div class=\"dumb-stat\"><span class=\"dumb-stat-label\">Open tickets</span><span class=\"dumb-stat-value\">4</span></div>"
    "<div class=\"dumb-stat\"><span class=\"dumb-stat-label\">Avg order value</span><span class=\"dumb-stat-value\">$89.40</span></div>"
    "<div class=\"dumb-stat\"><span class=\"dumb-stat-label\">Low stock SKUs</span><span class=\"dumb-stat-value\">3</span></div>"
    "</div>"
    "<div class=\"widget-card\">"
    "<div class=\"widget-head\"><span class=\"widget-badge\">"
    "<svg viewBox=\"0 0 16 16\" fill=\"currentColor\"><path d=\"M8 0l1.6 5.4L15 7l-5.4 1.6L8 14l-1.6-5.4L1 7l5.4-1.6z\"/></svg>"
    "Pilant</span><span class=\"widget-title\">Ask Ops</span></div>"
    "<p class=\"widget-sub\">Type what you need &mdash; Pilant builds the screen live from real order and inventory data.</p>"
    "<form id=\"pilant-form\">"
    "<input type=\"text\" id=\"pilant-query\" placeholder=\"Ask about orders or inventory...\" autocomplete=\"off\">"
    "<button type=\"submit\"><span class=\"spinner\"></span><span class=\"btn-label\">Ask</span></button>"
    "</form>"
    "<div class=\"widget-chips\">{{CHIPS}}</div>"
    "<div class=\"widget-status\" id=\"pilant-status\"></div>"
    "<div class=\"app-window\" id=\"pilant-result\"></div>"
    "</div>"
    "<div class=\"dash-grid\">"
    "<div class=\"panel-box\"><p class=\"panel-box-title\">Recent activity</p>{{ACTIVITY}}</div>"
    "<div class=\"side-col\">"
    "<div class=\"panel-box\"><p class=\"panel-box-title\">Top products this week</p>{{TOP_PRODUCTS}}</div>"
    "<div class=\"panel-box\"><p class=\"panel-box-title\">Staff tasks</p>{{TASKS}}</div>"
    "</div>"
    "</div>"
    "</main>"
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
    "  button.classList.add('loading');"
    "  status.classList.remove('is-error');"
    "  status.textContent = 'Building your screen...';"
    "  result.innerHTML = '';"
    "  fetch('/widget/generate', {"
    "    method: 'POST',"
    "    headers: {'Content-Type': 'application/x-www-form-urlencoded'},"
    "    body: 'query=' + encodeURIComponent(q)"
    "  }).then(function(r) { return r.text(); }).then(function(html) {"
    "    result.innerHTML = html;"
    "    status.textContent = '';"
    "    button.disabled = false;"
    "    button.classList.remove('loading');"
    "  }).catch(function(err) {"
    "    status.classList.add('is-error');"
    "    status.textContent = 'Something went wrong: ' + err;"
    "    button.disabled = false;"
    "    button.classList.remove('loading');"
    "  });"
    "}"
    "form.addEventListener('submit', function(e) {"
    "  e.preventDefault();"
    "  if (input.value.trim()) ask(input.value.trim());"
    "});"
    "document.querySelectorAll('.widget-chip').forEach(function(el) {"
    "  el.addEventListener('click', function() { ask(el.dataset.q); });"
    "});"
    "</script>"
    "</body></html>"
)


def render_page():
    nav_parts = []
    for item in NAV_ITEMS:
        cls = ' class="active"' if item == "Dashboard" else ""
        nav_parts.append(f'<a href="#"{cls}>{item}</a>')
    nav_html = "".join(nav_parts)
    chips_html = "".join(
        f'<button type="button" class="widget-chip" data-q="{_esc(c)}">{_esc(c)}</button>'
        for c in EXAMPLE_PROMPTS
    )
    activity_html = "".join(
        f'<div class="activity-row"><span>{_esc(text)}</span>'
        f'<span class="activity-time">{_esc(when)}</span></div>'
        for text, when in ACTIVITY_FEED
    )
    top_products_html = "".join(
        f'<div class="mini-row"><span>{_esc(name)}</span>'
        f'<span class="mini-row-count">{_esc(count)}</span></div>'
        for name, count in TOP_PRODUCTS
    )
    tasks_html = "".join(
        f'<div class="task-row"><span class="task-dot"></span><span>{_esc(t)}</span></div>'
        for t in STAFF_TASKS
    )
    out = PAGE
    out = out.replace("{{SITE_CSS}}", SITE_CSS)
    out = out.replace("{{PILANT_CSS}}", PILANT_WIDGET_CSS)
    out = out.replace("{{NAV}}", nav_html)
    out = out.replace("{{CHIPS}}", chips_html)
    out = out.replace("{{ACTIVITY}}", activity_html)
    out = out.replace("{{TOP_PRODUCTS}}", top_products_html)
    out = out.replace("{{TASKS}}", tasks_html)
    return out


@app.route("/")
def index():
    return render_page()


@app.route("/widget/generate", methods=["POST"])
def widget_generate():
    query = (request.form.get("query") or "").strip()
    if not query:
        return '<p class="panel-sub">Type something to ask for.</p>'

    print(f"[widget] {query}", file=sys.stderr)
    result = run_agent(query, verbose=True)

    if "error" in result:
        return f'<p class="panel-sub">Couldn\'t generate a screen for that request ({_esc(result["error"])}). Try rephrasing.</p>'

    return render_fragment(result)


if __name__ == "__main__":
    print("Harriet & Co Ops (with embedded Pilant) running at http://localhost:5002", file=sys.stderr)
    app.run(host="127.0.0.1", port=5002, debug=False)
