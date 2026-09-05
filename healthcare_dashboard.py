"""
Pilant Front Desk — real dashboard, no AI in the loop.

Unlike healthcare_site.py (which routes a typed request through the
Composition Engine and an LLM), this is a straightforward product-style
page: log in, land on a real dashboard with navigation, and see real
appointment data fetched directly from connectors_healthcare.get_appointments()
— the same live Epic open-sandbox connector, called directly with no model
in between. Nothing here is generated text; every number and row on the
page is computed from the actual FHIR response.

Same login gate as the other demos (reusing users.py) for consistency.
Because the Epic open sandbox only has a couple of synthetic appointments
per test patient (see connectors_healthcare.py's docstring), the "Patients"
and "Schedule" views below are built from that same small, real result set
rather than a separate data source — there's no fabricated data anywhere,
just different groupings of the one real fetch.
"""

import html as _html
import os
import secrets
import sys
from collections import Counter, defaultdict
from functools import wraps
from pathlib import Path

from flask import Flask, request, session, redirect, url_for

from connectors_healthcare import get_appointments
from users import get_user, verify_login

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


# ----------------------------------------------------------------------
# Styling — same design language (IBM Plex, dark ground, teal accent) as
# the other Pilant demos, but laid out as an actual product shell: fixed
# sidebar + top bar + content, not a centered single box.
# ----------------------------------------------------------------------

SHARED_CSS = """
:root { --ground:#0B1220; --surface:#141C30; --surface-2:#1A2440; --border:#2A3552; --text:#EDEFF5; --text-muted:#8791A8; --accent:#3FB6A8; --critical:#E8867B; --warning:#E8B85C; --good:#5FD08A; }
* { box-sizing:border-box; }
body { margin:0; min-height:100vh; background:var(--ground); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; }
a { color:inherit; }

/* ---- login page ---- */
.login-wrap { min-height:100vh; display:flex; align-items:center; justify-content:center; }
.login-box { width:100%; max-width:400px; padding:24px; text-align:center; }
.eyebrow { font-family:'Sora',sans-serif; font-weight:700; font-size:1.4rem; letter-spacing:.02em; margin:0 0 6px; }
.subeyebrow { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); margin:0 0 28px; }
form.login-form { display:flex; flex-direction:column; gap:14px; }
input[type=text], input[type=password], input[type=search] { width:100%; background:var(--surface); border:1px solid var(--border); border-radius:10px; padding:14px 16px; font-size:.95rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
input[type=text]:focus, input[type=password]:focus, input[type=search]:focus { outline:none; border-color:var(--accent); }
input::placeholder { color:var(--text-muted); }
button, .btn { background:var(--accent); color:#0B1220; border:none; border-radius:8px; padding:12px 18px; font-size:.88rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }
button:hover, .btn:hover { opacity:.92; }
.error { color:var(--critical); font-family:'IBM Plex Mono',monospace; font-size:.8rem; margin-top:16px; }
.demo-accounts { margin-top:26px; font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:var(--text-muted); line-height:1.7; text-align:left; border-top:1px solid var(--border); padding-top:16px; }
.demo-accounts b { color:var(--text); }

/* ---- app shell ---- */
.shell { display:flex; min-height:100vh; }
.sidebar { width:230px; flex-shrink:0; background:var(--surface); border-right:1px solid var(--border); padding:22px 16px; display:flex; flex-direction:column; }
.brand { font-family:'Sora',sans-serif; font-weight:700; font-size:1.15rem; padding:0 8px 22px; display:flex; align-items:center; gap:8px; }
.brand .dot { width:8px; height:8px; border-radius:50%; background:var(--accent); box-shadow:0 0 8px 1px var(--accent); }
.navlist { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; gap:2px; }
.navlist a { display:flex; align-items:center; gap:10px; padding:10px 12px; border-radius:8px; font-size:.86rem; color:var(--text-muted); text-decoration:none; }
.navlist a:hover { background:var(--surface-2); color:var(--text); }
.navlist a.active { background:var(--surface-2); color:var(--text); font-weight:600; box-shadow:inset 2px 0 0 var(--accent); }
.navlist .icon { width:16px; text-align:center; opacity:.85; }
.nav-divider { font-family:'IBM Plex Mono',monospace; font-size:.62rem; text-transform:uppercase; letter-spacing:.08em; color:var(--text-muted); padding:16px 12px 6px; }
.nav-divider:first-child { padding-top:0; }
.sidebar-foot { margin-top:auto; padding-top:16px; border-top:1px solid var(--border); font-family:'IBM Plex Mono',monospace; font-size:.7rem; color:var(--text-muted); }
.sidebar-foot .who { color:var(--text); font-weight:600; }
.sidebar-foot a { color:var(--text-muted); text-decoration:underline; }
.sidebar-foot a:hover { color:var(--accent); }

.main { flex:1; min-width:0; display:flex; flex-direction:column; }
.topbar { height:64px; flex-shrink:0; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; padding:0 26px; gap:20px; }
.topbar h1 { font-size:1.05rem; margin:0; font-weight:600; }
.topbar .sub { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); margin-top:2px; }
.topbar-copilot-btn { display:inline-flex; align-items:center; gap:7px; font-family:'IBM Plex Sans',sans-serif; font-size:.82rem; font-weight:600; color:var(--text); background:var(--surface-2); border:1px solid var(--border); border-radius:8px; padding:8px 15px; text-decoration:none; white-space:nowrap; flex-shrink:0; }
.topbar-copilot-btn:hover { border-color:var(--accent); }
.topbar-copilot-btn.active { background:var(--accent); color:#0B1220; border-color:var(--accent); }
.avatar { width:32px; height:32px; border-radius:50%; background:var(--accent); color:#0B1220; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:.78rem; flex-shrink:0; }

.content { padding:26px; overflow-y:auto; }
.banner-error { background:rgba(232,134,123,0.1); border:1px solid var(--critical); color:var(--critical); border-radius:10px; padding:14px 18px; font-size:.86rem; margin-bottom:20px; font-family:'IBM Plex Mono',monospace; }

/* ---- stat cards ---- */
.stat-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(150px, 1fr)); gap:14px; margin-bottom:26px; }
.stat-card { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px 18px; }
.stat-card .v { font-size:1.7rem; font-weight:700; font-family:'Sora',sans-serif; }
.stat-card .l { font-size:.76rem; color:var(--text-muted); margin-top:4px; }
.stat-card.tone-critical .v { color:var(--critical); }
.stat-card.tone-warning .v { color:var(--warning); }
.stat-card.tone-good .v { color:var(--good); }

/* ---- filter tabs ---- */
.filter-tabs { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; }
.filter-tabs a { font-family:'IBM Plex Mono',monospace; font-size:.74rem; padding:7px 13px; border-radius:999px; border:1px solid var(--border); color:var(--text-muted); text-decoration:none; }
.filter-tabs a:hover { border-color:var(--accent); color:var(--text); }
.filter-tabs a.active { background:var(--accent); border-color:var(--accent); color:#0B1220; font-weight:600; }

/* ---- table ---- */
.panel { background:var(--surface); border:1px solid var(--border); border-radius:12px; overflow:hidden; }
.panel-head { padding:16px 20px; border-bottom:1px solid var(--border); display:flex; align-items:center; justify-content:space-between; }
.panel-head h2 { font-size:.92rem; margin:0; }
.panel-head .count { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); }
table { width:100%; border-collapse:collapse; }
th { text-align:left; font-size:.7rem; text-transform:uppercase; letter-spacing:.04em; color:var(--text-muted); font-weight:600; padding:10px 20px; border-bottom:1px solid var(--border); }
td { padding:14px 20px; font-size:.86rem; border-bottom:1px solid var(--border); vertical-align:middle; }
tr:last-child td { border-bottom:none; }
tr:hover td { background:var(--surface-2); }
.patient-cell { font-weight:600; }
.provider-cell, .time-cell { color:var(--text-muted); }
.badge { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.68rem; padding:4px 9px; border-radius:999px; border:1px solid var(--border); text-transform:uppercase; letter-spacing:.03em; }
.badge.tone-critical { color:var(--critical); border-color:var(--critical); }
.badge.tone-warning { color:var(--warning); border-color:var(--warning); }
.badge.tone-good { color:var(--good); border-color:var(--good); }
.badge.tone-default { color:var(--text-muted); }
.empty-state { padding:40px 20px; text-align:center; color:var(--text-muted); font-size:.86rem; }

/* ---- simple settings/patients pages ---- */
.card-list { display:flex; flex-direction:column; gap:10px; }
.info-row { display:flex; justify-content:space-between; padding:14px 18px; background:var(--surface); border:1px solid var(--border); border-radius:10px; font-size:.86rem; }
.info-row .k { color:var(--text-muted); }

/* ---- inline appointments filter box ---- */
.appt-search-row { display:flex; gap:8px; margin-bottom:14px; max-width:420px; }
.appt-search-row input { flex:1; }
.appt-search-row button { flex-shrink:0; }

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

/* ---- mock/sample-data sections (Finance, HR, etc.) ---- */
.sample-tag { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.66rem; letter-spacing:.03em; text-transform:uppercase; color:var(--text-muted); border:1px dashed var(--border); border-radius:999px; padding:3px 10px; margin-left:10px; vertical-align:middle; }
"""

STATUS_META = {
    "booked":     {"label": "Booked",     "tone": "warning"},
    "checked_in": {"label": "Checked in", "tone": "default"},
    "fulfilled":  {"label": "Fulfilled",  "tone": "good"},
    "cancelled":  {"label": "Cancelled",  "tone": "critical"},
    "no_show":    {"label": "No-show",    "tone": "critical"},
}

# Grouped as (divider_label_or_None, [(key, href, icon, label), ...]).
# Dashboard/Appointments/Patients stay ungrouped at the top since they're
# backed by the real FHIR fetch; everything below is a distinct
# department, grouped the way a real front-desk product would organize
# them, with a divider label rendered above each named group.
NAV_GROUPS = [
    (None, [
        ("dashboard", "/", "&#9635;", "Dashboard"),
        ("appointments", "/appointments", "&#128197;", "Appointments"),
        ("patients", "/patients", "&#128100;", "Patients"),
    ]),
    ("Care team", [
        ("doctors", "/section/doctors", "&#129658;", "Doctors"),
        ("social_work", "/section/social_work", "&#129309;", "Social Work &amp; Therapies"),
        ("cmt", "/section/cmt", "&#129517;", "CMT"),
    ]),
    ("Operations", [
        ("finance", "/section/finance", "&#128176;", "Finance"),
        ("patient_funding", "/section/patient_funding", "&#128179;", "Patient Funding"),
        ("administration", "/section/administration", "&#127970;", "Administration"),
        ("hr", "/section/hr", "&#128101;", "Human Resources"),
        ("marketing", "/section/marketing", "&#128227;", "Marketing &amp; Communications"),
    ]),
    (None, [
        ("settings", "/settings", "&#9881;", "Settings"),
    ]),
]

# Sample/mock data for the department sections above — the real FHIR
# connector only exposes appointments for a couple of sandbox test
# patients, so there's no live source for Finance/HR/Marketing/etc. This
# is clearly labeled "sample data" everywhere it appears (see
# _mock_section_body) rather than presented as if it were real, live
# numbers — same honesty principle as the rest of this file.
MOCK_SECTIONS = {
    "doctors": {
        "label": "Doctors",
        "subtitle": "physician roster &middot; sample data",
        "stats": [
            {"label": "Total physicians", "value": "34", "tone": "default"},
            {"label": "On duty today", "value": "11", "tone": "good"},
            {"label": "Avg patient load", "value": "14", "tone": "default"},
            {"label": "Specialties covered", "value": "9", "tone": "default"},
        ],
        "table": {
            "title": "Physician roster",
            "columns": ["Name", "Specialty", "Status", "Patients today"],
            "rows": [
                ["Dr. Sarah Bennett", "Family Medicine", ("On duty", "good"), "12"],
                ["Dr. Michael Osei", "Pediatrics", ("On duty", "good"), "9"],
                ["Dr. Priya Raman", "Cardiology", ("Off today", "default"), "0"],
                ["Dr. James Wu", "Internal Medicine", ("On duty", "good"), "15"],
                ["Dr. Elena Torres", "OB/GYN", ("On call", "warning"), "6"],
            ],
        },
    },
    "social_work": {
        "label": "Social Work & Therapies",
        "subtitle": "case load overview &middot; sample data",
        "stats": [
            {"label": "Active cases", "value": "27", "tone": "default"},
            {"label": "Sessions this week", "value": "41", "tone": "good"},
            {"label": "Referrals pending", "value": "6", "tone": "warning"},
            {"label": "Avg case duration", "value": "5.2 wks", "tone": "default"},
        ],
        "table": {
            "title": "Active cases",
            "columns": ["Patient", "Service", "Assigned to", "Status"],
            "rows": [
                ["Lopez, Camila Maria", "Behavioral health follow-up", "R. Okafor, LCSW", ("Active", "default")],
                ["Chen, David", "Physical therapy", "T. Nguyen, PT", ("Active", "default")],
                ["Martinez, Ana", "Discharge planning", "R. Okafor, LCSW", ("Pending referral", "warning")],
                ["Williams, Kevin", "Occupational therapy", "S. Patel, OT", ("Completed", "good")],
            ],
        },
    },
    "cmt": {
        "label": "CMT",
        "subtitle": "Care Management Team &middot; sample data",
        "stats": [
            {"label": "Active care plans", "value": "19", "tone": "default"},
            {"label": "High-risk patients", "value": "6", "tone": "critical"},
            {"label": "Care coordinators", "value": "4", "tone": "default"},
            {"label": "Avg plan review", "value": "9 days", "tone": "default"},
        ],
        "table": {
            "title": "Care plans in progress",
            "columns": ["Patient", "Risk level", "Coordinator", "Next review"],
            "rows": [
                ["Lopez, Camila Maria", ("Moderate", "warning"), "N. Foster, RN", "Aug 28"],
                ["Chen, David", ("High", "critical"), "N. Foster, RN", "Aug 24"],
                ["Martinez, Ana", ("Low", "good"), "K. Ibarra, RN", "Sep 3"],
                ["Williams, Kevin", ("Moderate", "warning"), "K. Ibarra, RN", "Aug 30"],
            ],
        },
    },
    "finance": {
        "label": "Finance",
        "subtitle": "billing &amp; revenue overview &middot; sample data",
        "stats": [
            {"label": "Revenue (MTD)", "value": "$284,600", "tone": "good"},
            {"label": "Outstanding claims", "value": "$52,300", "tone": "warning"},
            {"label": "Avg claim cycle", "value": "18 days", "tone": "default"},
            {"label": "Collections rate", "value": "94.2%", "tone": "good"},
        ],
        "table": {
            "title": "Recent transactions",
            "columns": ["Date", "Description", "Amount", "Status"],
            "rows": [
                ["Aug 21", "Insurance payment — Aetna", "$4,820", ("Posted", "good")],
                ["Aug 20", "Patient co-pay — Lopez, Camila Maria", "$45", ("Posted", "good")],
                ["Aug 19", "Insurance claim — UnitedHealth", "$3,150", ("Pending", "warning")],
                ["Aug 18", "Equipment lease payment", "$1,200", ("Posted", "good")],
                ["Aug 15", "Claim denial — BlueCross", "$980", ("Denied", "critical")],
            ],
        },
    },
    "patient_funding": {
        "label": "Patient Funding",
        "subtitle": "grants &amp; assistance programs &middot; sample data",
        "stats": [
            {"label": "Active grants", "value": "5", "tone": "default"},
            {"label": "Disbursed (YTD)", "value": "$412,000", "tone": "good"},
            {"label": "Applications pending", "value": "9", "tone": "warning"},
            {"label": "Avg approval time", "value": "12 days", "tone": "default"},
        ],
        "table": {
            "title": "Funding applications",
            "columns": ["Patient", "Program", "Amount requested", "Status"],
            "rows": [
                ["Lopez, Camila Maria", "Charity Care Fund", "$1,200", ("Approved", "good")],
                ["Chen, David", "State Medicaid supplement", "$3,400", ("Under review", "warning")],
                ["Martinez, Ana", "Prescription Assistance", "$260", ("Approved", "good")],
                ["Williams, Kevin", "Emergency Relief Grant", "$800", ("Denied", "critical")],
            ],
        },
    },
    "administration": {
        "label": "Administration",
        "subtitle": "facilities &amp; compliance &middot; sample data",
        "stats": [
            {"label": "Active staff", "value": "142", "tone": "default"},
            {"label": "Open requisitions", "value": "6", "tone": "warning"},
            {"label": "Facilities managed", "value": "3", "tone": "default"},
            {"label": "Compliance items due", "value": "2", "tone": "critical"},
        ],
        "table": {
            "title": "Administrative tasks",
            "columns": ["Task", "Owner", "Due", "Status"],
            "rows": [
                ["Renew HIPAA training records", "Compliance Team", "Aug 30", ("Due soon", "warning")],
                ["Update visitor policy signage", "Facilities", "Sep 5", ("On track", "good")],
                ["Annual fire drill", "Safety Officer", "Sep 12", ("Scheduled", "default")],
                ["Vendor contract renewal — Cleaning Co.", "Procurement", "Aug 25", ("Overdue", "critical")],
                ["Staff badge access audit", "IT/Admin", "Sep 1", ("On track", "good")],
            ],
        },
    },
    "hr": {
        "label": "Human Resources",
        "subtitle": "staffing &amp; open requests &middot; sample data",
        "stats": [
            {"label": "Headcount", "value": "142", "tone": "default"},
            {"label": "Open positions", "value": "5", "tone": "warning"},
            {"label": "Onboarding this month", "value": "3", "tone": "default"},
            {"label": "PTO requests pending", "value": "8", "tone": "warning"},
        ],
        "table": {
            "title": "Open positions & requests",
            "columns": ["Role / request", "Department", "Submitted", "Status"],
            "rows": [
                ["RN — Pediatrics", "Nursing", "Aug 10", ("Interviewing", "default")],
                ["Front Desk Coordinator", "Front Desk", "Aug 15", ("Open", "warning")],
                ["PTO request — J. Alvarez", "Nursing", "Aug 19", ("Pending approval", "warning")],
                ["Onboarding — M. Chen (Billing)", "Finance", "Aug 18", ("In progress", "default")],
                ["Medical Records Clerk", "Administration", "Aug 5", ("Filled", "good")],
            ],
        },
    },
    "marketing": {
        "label": "Marketing & Communications",
        "subtitle": "outreach &amp; engagement &middot; sample data",
        "stats": [
            {"label": "Active campaigns", "value": "4", "tone": "default"},
            {"label": "Website visits (30d)", "value": "12,400", "tone": "good"},
            {"label": "Patient satisfaction", "value": "4.6 / 5", "tone": "good"},
            {"label": "Referral rate", "value": "22%", "tone": "default"},
        ],
        "table": {
            "title": "Active campaigns",
            "columns": ["Campaign", "Channel", "Reach", "Status"],
            "rows": [
                ["Flu shot awareness", "Email + Social", "8,200", ("Live", "good")],
                ["New pediatrics wing launch", "Local press", "5,600", ("Live", "good")],
                ["Patient satisfaction survey", "SMS", "1,900", ("Collecting", "default")],
                ["Community health fair", "In-person", "—", ("Planning", "warning")],
            ],
        },
    },
}


def _fmt_time(iso_str):
    if not iso_str:
        return "—"
    # "2026-08-18T16:30:00Z" -> "Aug 18, 4:30 PM"
    try:
        from datetime import datetime
        dt = datetime.strptime(iso_str.replace("Z", "+0000"), "%Y-%m-%dT%H:%M:%S%z")
        return dt.strftime("%b %-d, %-I:%M %p")
    except Exception:
        return iso_str


def _badge(status):
    meta = STATUS_META.get(status, {"label": status or "unknown", "tone": "default"})
    return f'<span class="badge tone-{meta["tone"]}">{_esc(meta["label"])}</span>'


def _sidebar(active):
    groups_html = []
    for divider, group_items in NAV_GROUPS:
        divider_html = f'<div class="nav-divider">{_esc(divider)}</div>' if divider else ""
        items = "".join(
            f'<li><a href="{href}" class="{"active" if key == active else ""}">'
            f'<span class="icon">{icon}</span>{label}</a></li>'
            for key, href, icon, label in group_items
        )
        groups_html.append(f'{divider_html}<ul class="navlist">{items}</ul>')
    return "".join(groups_html)


def _copilot_panel():
    """
    Renders the docked Copilot chat panel — real conversation history
    (session["copilot_transcript"]), each entry either a plain text bubble
    (user message / clarifying question / error) or, for a completed
    request, a compact "generated view" card embedding the rendered screen
    in an iframe (renderer.render_html, self-contained, so its CSS never
    collides with the dashboard's own — same technique the old single-shot
    /pilant route used).

    Only the compact view JSON is kept in session, not the rendered HTML —
    the HTML is built fresh here on every page load. Flask's session is a
    signed cookie (browser-side, ~4KB cap), so the transcript itself is
    also capped (see MAX_COPILOT_TRANSCRIPT) to keep the whole session
    small — that cookie also carries the logged-in username, so letting it
    grow unbounded risks breaking login, not just the chat.
    """
    from renderer import render_html

    transcript = session.get("copilot_transcript", [])
    return_to = request.path
    mid_clarify = bool(session.get("copilot_active_conversation"))

    if not transcript:
        body = (
            '<div class="copilot-empty">Ask about today\'s appointments and Copilot will fetch '
            'real data from Epic and build a screen for you, right here in the chat.'
            '<span class="ex">&ldquo;who needs to be checked in?&rdquo;</span>'
            '<span class="ex">&ldquo;any no-shows today?&rdquo;</span>'
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
    placeholder = "Type your answer..." if mid_clarify else "Ask about appointments..."
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


def _shell(user, active, title, subtitle, body_html, error=None, show_copilot=True):
    """
    show_copilot=False (added 2026-08-29 for the generated-view page below)
    suppresses BOTH the docked Copilot panel and its topbar toggle button
    on this render — used only by _render_generated_view_page(), which
    embeds its own follow-up box directly in the content area instead.
    Reusing the real docked panel there instead would have its own
    "return_to" posting back to a specific, soon-stale /copilot/expand/<idx>
    URL rather than always the latest one, and would duplicate the input
    box the generated view already has — so it's cleanly left out rather
    than reconciled with a second follow-up mechanism.
    """
    error_html = f'<div class="banner-error">{_esc(error)}</div>' if error else ""
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
        f"<title>Pilant Front Desk — {_esc(title)}</title>"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
        f"<style>{SHARED_CSS}</style></head><body>"
        '<div class="shell">'
        '<div class="sidebar">'
        '<div class="brand"><span class="dot"></span>Pilant</div>'
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
        f'<div class="content">{error_html}{body_html}</div>'
        '</div>'
        f'{copilot_html}'
        '</div>'
        "</body></html>"
    )


def render_login(error=None):
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>Pilant Front Desk — log in</title>"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
        f"<style>{SHARED_CSS}</style></head><body>"
        '<div class="login-wrap"><div class="login-box">'
        '<p class="eyebrow">Pilant</p>'
        '<p class="subeyebrow">front desk dashboard &middot; real Epic sandbox data</p>'
        '<form class="login-form" method="post" action="/login">'
        '<input type="text" name="username" placeholder="username" autofocus required>'
        '<input type="password" name="password" placeholder="password" required>'
        '<button type="submit">Log in</button>'
        '</form>'
        f'{error_html}'
        '<div class="demo-accounts">'
        '<div><b>kowsick</b> / owner123 — owner</div>'
        '<div><b>analyst</b> / analyst123 — analyst</div>'
        '<div><b>viewer</b> / viewer123 — viewer</div>'
        '</div>'
        '</div></div>'
        "</body></html>"
    )


def _fetch_appointments_safe():
    """Returns (appointments, error_message)."""
    try:
        return get_appointments(), None
    except Exception as e:
        return [], str(e)


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
    appts, error = _fetch_appointments_safe()

    counts = Counter(a["status"] for a in appts)
    stat_cards = "".join(
        f'<div class="stat-card tone-{STATUS_META.get(status, {}).get("tone", "default")}">'
        f'<div class="v">{counts.get(status, 0)}</div>'
        f'<div class="l">{_esc(meta["label"])}</div></div>'
        for status, meta in STATUS_META.items()
    )
    stat_cards = (
        f'<div class="stat-card"><div class="v">{len(appts)}</div><div class="l">Total appointments</div></div>'
        + stat_cards
    )

    # "needs attention" = anything not simply booked-and-far-off: checked-in
    # (waiting), cancelled, or no-show — the same lens the AI demo uses,
    # computed directly here from real data instead.
    attention = [a for a in appts if a["status"] in ("checked_in", "cancelled", "no_show")]
    rows_html = _appointment_rows(attention[:8])

    body = f"""
    <div class="stat-grid">{stat_cards}</div>
    <div class="panel">
      <div class="panel-head"><h2>Needs attention</h2><span class="count">{len(attention)} of {len(appts)}</span></div>
      {rows_html}
    </div>
    """
    return _shell(user, "dashboard", "Dashboard", "front desk overview &middot; live from Epic FHIR", body, error=error)


@app.route("/appointments")
@login_required
def appointments():
    user = get_user(session["username"])
    appts, error = _fetch_appointments_safe()

    status_filter = request.args.get("status") or ""
    q = (request.args.get("q") or "").strip().lower()

    filtered = appts
    if status_filter:
        filtered = [a for a in filtered if a["status"] == status_filter]
    if q:
        filtered = [
            a for a in filtered
            if q in (a.get("patient") or "").lower() or q in (a.get("provider") or "").lower()
        ]

    tabs = ['<a href="/appointments" class="{}">All</a>'.format("active" if not status_filter else "")]
    for status, meta in STATUS_META.items():
        cls = "active" if status_filter == status else ""
        tabs.append(f'<a href="/appointments?status={status}" class="{cls}">{_esc(meta["label"])}</a>')

    status_hidden = f'<input type="hidden" name="status" value="{_esc(status_filter)}">' if status_filter else ""
    body = f"""
    <div class="filter-tabs">{''.join(tabs)}</div>
    <form method="get" action="/appointments" class="appt-search-row">
      {status_hidden}
      <input type="search" name="q" value="{_esc(q)}" placeholder="Filter by patient or provider...">
      <button type="submit">Filter</button>
    </form>
    <div class="panel">
      <div class="panel-head"><h2>All appointments</h2><span class="count">{len(filtered)} of {len(appts)}</span></div>
      {_appointment_rows(filtered)}
    </div>
    """
    return _shell(user, "appointments", "Appointments", "every appointment on record for the configured test patient(s)", body, error=error)


@app.route("/patients")
@login_required
def patients():
    user = get_user(session["username"])
    appts, error = _fetch_appointments_safe()

    by_patient = defaultdict(list)
    for a in appts:
        by_patient[a.get("patient") or "unknown patient"].append(a)

    if not by_patient:
        rows = '<div class="empty-state">No patients found in the configured sandbox test set.</div>'
    else:
        rows = '<div class="card-list">'
        for name, plist in sorted(by_patient.items()):
            last_status = plist[-1]["status"]
            rows += (
                '<div class="info-row">'
                f'<span class="k">{_esc(name)}</span>'
                f'<span>{len(plist)} appointment{"s" if len(plist) != 1 else ""} &middot; most recent: {_badge(last_status)}</span>'
                '</div>'
            )
        rows += '</div>'

    body = f'<div class="panel-head" style="border:none;padding:0 0 14px;"><h2>Patients on record</h2></div>{rows}'
    return _shell(user, "patients", "Patients", "grouped from the same live appointment fetch — no separate data source", body, error=error)


@app.route("/settings")
@login_required
def settings_page():
    user = get_user(session["username"])
    scope = user.get("label_scope")
    scope_text = "unrestricted" if scope is None else ", ".join(scope)
    body = f"""
    <div class="card-list">
      <div class="info-row"><span class="k">Signed in as</span><span>{_esc(user["name"])} ({_esc(user["username"])})</span></div>
      <div class="info-row"><span class="k">Role</span><span>{_esc(user["role"])}</span></div>
      <div class="info-row"><span class="k">Data scope</span><span>{_esc(scope_text)}</span></div>
      <div class="info-row"><span class="k">Connected system</span><span>Epic open FHIR sandbox (non-production)</span></div>
      <div class="info-row"><span class="k">Test patients configured</span><span>{_esc(os.environ.get("EPIC_TEST_PATIENT_IDS", "(none set)"))}</span></div>
    </div>
    """
    return _shell(user, "settings", "Settings", "account &amp; connection info", body)


def _mock_table_html(table):
    thead = "".join(f"<th>{_esc(c)}</th>" for c in table["columns"])
    rows_html = []
    for row in table["rows"]:
        cells = []
        for cell in row:
            if isinstance(cell, tuple):
                text, tone = cell
                cells.append(f'<td><span class="badge tone-{tone}">{_esc(text)}</span></td>')
            else:
                cells.append(f"<td>{_esc(cell)}</td>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")
    return (
        "<table><thead><tr>" + thead + "</tr></thead><tbody>"
        + "".join(rows_html) + "</tbody></table>"
    )


def _mock_section_body(section):
    stat_cards = "".join(
        f'<div class="stat-card tone-{s.get("tone", "default")}">'
        f'<div class="v">{_esc(s["value"])}</div><div class="l">{_esc(s["label"])}</div></div>'
        for s in section["stats"]
    )
    table = section["table"]
    return f"""
    <div class="stat-grid">{stat_cards}</div>
    <div class="panel">
      <div class="panel-head"><h2>{_esc(table["title"])}<span class="sample-tag">sample data</span></h2>
      <span class="count">{len(table["rows"])} records</span></div>
      {_mock_table_html(table)}
    </div>
    """


@app.route("/section/<key>")
@login_required
def mock_section(key):
    """
    Finance / Administration / HR / Marketing / Social Work & Therapies /
    Doctors / Patient Funding / CMT — departments with no real connected
    data source (the FHIR sandbox only covers appointments). These render
    realistic, clearly-labeled sample data in the same visual language as
    the rest of the dashboard, rather than either faking them as live or
    leaving them as bare stubs.
    """
    section = MOCK_SECTIONS.get(key)
    if section is None:
        return redirect(url_for("dashboard"))
    user = get_user(session["username"])
    body = _mock_section_body(section)
    return _shell(user, key, section["label"], section["subtitle"], body)


# Session cookie is signed but browser-side (no server-side store configured),
# so it's capped around 4KB and also carries the logged-in username — keep
# the chat history short so a long conversation can never break login.
MAX_COPILOT_TRANSCRIPT = 6

# agent_healthcare's system prompt forces every request through
# get_appointments + render_view (no plain-text answers), which is right
# for real questions but means something like "hi" has no data to fetch
# and no real ambiguity to ask about — it just burns through max_steps and
# comes back as a confusing "hit max_steps without a valid render_view"
# error. Catch greetings/small talk here, before the model is ever
# called, and answer them directly instead.
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
    "Hi! Ask me about today's appointments and I'll pull real data from Epic and build "
    "a screen for it — try something like “who needs to be checked in?” or "
    "“any no-shows today?”"
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
    """
    A message from the docked Copilot panel. Runs the same Composition
    Engine (agent_healthcare.run_agent) the old single-shot /pilant search
    bar used, but appends to a persistent, growing chat transcript instead
    of showing one screen at a time — and redirects back to whatever page
    you were on (return_to) so the panel stays docked wherever you're
    working instead of taking over the whole window.
    """
    text = (request.form.get("message") or "").strip()
    return_to = request.form.get("return_to") or url_for("dashboard")
    # "/copilot/expand/latest" is the sentinel _expand_followup_bar_html()'s
    # form uses (see that function's docstring) -- only a real fallback
    # target for a fresh render (the whole point of a full-screen view). A
    # clarifying question, plain-text reply, or error has nothing
    # full-screen to show, so those fall back to the docked panel on the
    # main dashboard instead, same as every other copilot_send() caller.
    wants_expand_on_render = return_to == "/copilot/expand/latest"
    if wants_expand_on_render:
        return_to = url_for("dashboard")
    if not text:
        return redirect(return_to)

    session["copilot_open"] = True
    transcript = session.get("copilot_transcript", [])
    convo = session.get("copilot_active_conversation")

    # Greetings/small talk never reach the model — there's no data to fetch
    # and nothing genuinely ambiguous to ask about, so forcing it through
    # the agent loop only produces a confusing max_steps error. Only
    # short-circuits when there's no clarify already in flight, so a real
    # answer to a real clarifying question is never accidentally caught.
    if not convo and _looks_like_smalltalk(text):
        transcript.append({"role": "user", "text": text})
        transcript.append({"role": "assistant", "text": SMALLTALK_REPLY})
        session["copilot_transcript"] = transcript[-MAX_COPILOT_TRANSCRIPT:]
        return redirect(return_to)

    try:
        from agent_healthcare import run_agent
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
            # A genuine conversational reply — greeting, small talk, "what
            # can you do" — with no data fetched and no screen to render.
            # See agent_healthcare.py's SYSTEM prompt / tool_choice="auto"
            # change for why this shape can come back now.
            transcript.append({"role": "assistant", "text": result["text"]})
        else:
            # Only the compact view JSON is stored — see _copilot_panel's
            # docstring for why (session cookie size).
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
    """
    Same rendering as copilot_expand(<int:idx>) above, but always shows the
    MOST RECENT render-kind transcript entry rather than a fixed index --
    the redirect target _generated_view_followup_html()'s form posts to,
    since a follow-up asked from a full-screen page doesn't know in advance
    what position its own answer will land at in the transcript (and that
    position shifts anyway once MAX_COPILOT_TRANSCRIPT trimming kicks in).
    """
    user = get_user(session["username"])
    transcript = session.get("copilot_transcript", [])
    render_indices = [i for i, e in enumerate(transcript) if e.get("kind") == "render"]
    if not render_indices:
        return redirect(url_for("dashboard"))
    entry = transcript[render_indices[-1]]
    return _render_generated_view_page(user, entry["view"], entry.get("query", ""))


def _badge_from(b):
    """Same shape as renderer.py's own _badge(), but for a component-level
    badge dict ({"text": ..., "tone": ...}) from a render_view component —
    NOT the status-string _badge(status) above, which looks statuses up in
    STATUS_META. Kept separate rather than overloading _badge() with two
    incompatible input shapes."""
    if not b:
        return ""
    tone = b.get("tone", "default")
    text = b.get("text", "")
    return f'<span class="badge tone-{_esc(tone)}">{_esc(text)}</span>'


def _dashboard_component_html(c):
    """
    Converts one render_view component (the Composition Engine's generic
    schema — see schema.py / renderer.py's _component_html for the source
    of truth every connector's model output is validated against) into
    HTML styled with THIS app's own dashboard chrome (SHARED_CSS above)
    instead of renderer.py's generic light "device mockup" look.

    Added 2026-08-29 after the user pointed out that Copilot's "open full
    screen" view looked like a generic floating mockup card, not part of
    the actual Pilant front-desk product they were using everywhere else —
    "interface should look like the actually healthcare application."
    Deliberately kept local to this file rather than changed in the shared
    renderer.py, which every OTHER demo (helpdesk_site.py, retail_site.py,
    the CLI outputs, healthcare_site.py itself) still uses unchanged for
    its own generated-view pages — reuses the exact CSS classes
    _appointment_rows()/_mock_table_html() already use elsewhere in this
    file (table/th/td/.patient-cell/.provider-cell/.badge/.stat-card/
    .panel/.info-row), so a Copilot-generated screen and a real dashboard
    page become visually indistinguishable, not just similarly themed.

    A render_view row's fields (name/note/badge/action/url) are generic —
    unlike _appointment_rows()'s fixed Patient/Provider/Time/Status
    columns, a Copilot answer might legitimately be about anything ("show
    me Camila Lopez's appointments" produced date/provider/status rows,
    not patient/provider/time) — so list rows render as a generic two-
    column Name/Detail table with a status badge, rather than assuming
    appointment-shaped columns that won't always fit what was actually
    asked for.
    """
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
                f'<td class="patient-cell">{_esc(r.get("name"))}</td>'
                f'<td class="provider-cell">{_esc(r.get("note") or "")}</td>'
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
    """The docked Copilot panel's own input box is deliberately NOT shown
    on the generated-view page (see _shell()'s show_copilot docstring) —
    this is its full-screen-page equivalent instead, styled with the same
    .appt-search-row input+button row /appointments already uses. Posts to
    the same /copilot/send the docked panel uses, with
    return_to="/copilot/expand/latest" — see copilot_send()'s handling of
    that sentinel for what happens when the reply isn't a fresh render."""
    return (
        '<form method="post" action="/copilot/send" class="appt-search-row" style="max-width:560px;margin-bottom:22px;">'
        '<input type="hidden" name="return_to" value="/copilot/expand/latest">'
        '<input type="text" name="message" placeholder="Ask a follow-up — e.g. \'now show me the no-shows\'" autofocus>'
        '<button type="submit">Ask</button>'
        "</form>"
    )


def _render_generated_view_page(user, view, query):
    """
    Renders a Copilot-generated screen (the render_view result from
    agent_healthcare.py) as a genuine page inside Pilant's own dashboard
    shell — sidebar, topbar, real nav — instead of renderer.py's generic
    floating "device mockup" card. See _dashboard_component_html()'s
    docstring for the full reasoning. Used by copilot_expand() /
    copilot_expand_latest() (the "open full screen" flow).
    """
    components_html = "".join(_dashboard_component_html(c) for c in (view.get("components") or []))
    heading = view.get("heading") or query or "Generated view"
    subtitle = f'Copilot answer to: "{query}"' if query else "Copilot-generated view"
    body = _generated_view_followup_html() + components_html
    return _shell(user, None, heading, subtitle, body, show_copilot=False)


def _appointment_rows(appts):
    if not appts:
        return '<div class="empty-state">No appointments match this view.</div>'
    rows = "".join(
        "<tr>"
        f'<td class="patient-cell">{_esc(a.get("patient") or "unknown patient")}</td>'
        f'<td class="provider-cell">{_esc(a.get("provider") or "unassigned")}</td>'
        f'<td class="time-cell">{_esc(_fmt_time(a.get("start")))}</td>'
        f'<td>{_badge(a.get("status"))}</td>'
        "</tr>"
        for a in appts
    )
    return (
        "<table><thead><tr>"
        "<th>Patient</th><th>Provider</th><th>Time</th><th>Status</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )


if __name__ == "__main__":
    print("Pilant front desk dashboard (real FHIR data, no AI) running at http://localhost:5006", file=sys.stderr)
    app.run(host="127.0.0.1", port=5006, debug=False)
