"""
A real, interactive Gmail interface — not just "type a request, get a
rendered screen" like the render_view-based Studio workflows, but an
actual inbox you can click into, reply from, compose in, delete from, star,
and forward from. Added 2026-08-24 after proving out agent_gmail.py's
read-only rendering: reading real mail was the first milestone, this is the
second — genuine write actions (send_reply, send_email, trash_message,
star_message, forward via send_email — all in connectors_gmail.py) with a
person in the loop before anything sends or deletes.

Deliberately NOT built on the render_view/tool-calling engine studio.py's
chat-generated workflows use. Two reasons:

  1. Reliability. 2026-08-24's whole debugging session (see agent_gmail.py's
     docstring and MODEL comment) was one long fight against a small model
     producing malformed JSON, wrong counts, and fabricated placeholder
     values. Browsing your own inbox and clicking Reply doesn't need an LLM
     deciding what to fetch or how to lay it out — it needs correct code.
     get_gmail_messages()/get_message_full() are called directly here, so
     what you see is exactly what Gmail's API actually returned, every
     time, not "usually, once the model self-corrects."
  2. Safety. render_view's schema has no field for a real message ID or
     URL — there was never a safe way to let the model decide which real
     email a "Reply"/"Delete"/"Forward" action should target without
     risking it pointing somewhere wrong. Code decides navigation and
     identity here; the model (agent_gmail.draft_reply_text) is used ONLY
     to suggest reply text, shown in an editable textarea, never sent
     without a person clicking Send. That split — deterministic code owns
     "what real thing does this button do," the model only ever suggests
     text a person reviews — is the actual safety property this file is
     built around, not a detail. This is also why Studio's chat connector
     (agent_gmail.py's TOOLS) was deliberately never given compose/delete
     tools of its own — a chat request "delete my spam" would have no
     click-to-confirm step the way every write action below does.

MERGED INTO STUDIO 2026-08-26, at the user's explicit request ("merge into
Studio, one app, port 5008") after they tried this interface's
compose/delete/star/forward from Studio's own chat page and correctly
found it wasn't there — this file used to be its own standalone Flask app
on its own port (5007), which meant genuinely two separate servers to run
and two browser tabs to keep straight. It's now a Blueprint
(`gmail_bp`, mounted with NO url_prefix so every route keeps its original
path — /inbox, /compose, /message/<id>, etc. — unchanged) that studio.py
imports and registers directly onto its own Flask app/port (see studio.py's
`from gmail_site import gmail_bp` / `app.register_blueprint(gmail_bp)`).
Concretely this means:
  - No more standalone `app = Flask(__name__)`, secret key, or .env
    loading here — studio.py already owns all three for the one process
    both files now run inside.
  - No more /login, /logout, or / routes here, and no LOGIN_PAGE/
    render_login — studio.py's own login already establishes
    session["username"] using the same users.py this file's
    login_required checks, so a person logged into Studio is
    automatically "logged in" here too, same cookie, same session.
  - Every internal redirect that used to say url_for("inbox") now says
    url_for(".inbox") (the leading dot is Flask's "this blueprint"
    shorthand) — url_for("login")/url_for("logout") stay bare, since
    those two are genuinely Studio's own top-level routes, not this
    blueprint's.
  - This file is no longer runnable standalone (`python3 gmail_site.py`
    does nothing useful now) — `python3 studio.py` is the one command
    that starts everything, including this.

Same login (users.py) as studio.py, same non-scoping reasoning as
agent_gmail.py's docstring: whoever logs in sees the one real inbox this
refresh token is authorized for, not a personalized slice of it.

send_reply()/send_email()/forward (via send_email) need GMAIL_REFRESH_TOKEN
re-authorized with gmail.send (in addition to gmail.readonly).
trash_message()/star_message()/mark_read() need a SEPARATE
re-authorization with gmail.modify. See connectors_gmail.py's module
docstring for the exact OAuth Playground steps for both. Until those are
done, browsing/reading/searching works fine here regardless; only the
specific write action needing that scope will fail, with a clear message
pointing at the fix (_scope_err below) rather than a raw 403.
"""

import html as _html
import re
import urllib.parse
from functools import wraps

from flask import Blueprint, Response, request, session, redirect, url_for

from agent_gmail import draft_reply_text, translate_to_search_query
from connectors_gmail import (
    FOLDERS,
    get_attachment,
    get_gmail_messages,
    get_gmail_threads,
    get_message_full,
    get_thread_full,
    mark_read,
    send_email,
    send_reply,
    star_message,
    star_thread,
    trash_message,
    trash_thread,
)
from users import get_user

gmail_bp = Blueprint("mail", __name__)


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _nl2br(s):
    """Escape first, THEN insert <br> — escaping after would turn the tags
    themselves back into visible text. Used for plain contexts (e.g. the
    forward-quote textarea) that should stay exactly as fetched, never
    linkified — see _linkify_and_nl2br below for the read-only message
    view, which is a different, display-only case."""
    return _esc(s).replace("\n", "<br>")


# Marketing/notification email plain-text bodies routinely spell the link
# out inline right after its own label — "What's new
# (https://click.vendor.com/f/a/very-long-opaque-tracking-token...)" — which
# connectors_gmail.py's _extract_body_text deliberately preserves verbatim
# (see that module's docstring: real text/plain, trusted as-is, not a real
# HTML renderer). Showing that raw token string as visible text is both
# unreadable and, without care, can overflow the message pane's width
# outright (a single un-spaced 200+ character token has nowhere to wrap) —
# exactly what a real Base44 marketing email showed live 2026-08-27. Fix
# is at the RENDERING layer only, not the fetch layer: the link stays the
# real, exact URL nothing invented — only the visible LABEL is shortened to
# the domain, and .msg-body below gets overflow-wrap as a hard backstop for
# any token this regex doesn't happen to catch.
_URL_RE = re.compile(r'https?://[^\s<>"\')\]]+')


def _linkify_and_nl2br(text):
    """Same escape-then-format contract as _nl2br, plus: every raw URL in
    the text becomes a real <a href> (target=_blank, rel=noopener) labeled
    by its domain instead of the full raw string, and a blank line becomes
    real paragraph spacing instead of just another <br> — the plain-text
    body this receives already collapses 3+ blank lines to one
    (connectors_gmail._clean_body_text), so \\n\\n reliably means "the
    sender's own paragraph break", not incidental whitespace."""
    if not text:
        return text

    def linkify_line(line):
        out = []
        last = 0
        for m in _URL_RE.finditer(line):
            out.append(_esc(line[last:m.start()]))
            url = m.group(0)
            trail = ""
            while url and url[-1] in ".,;:!?)]}'\"":
                trail = url[-1] + trail
                url = url[:-1]
            domain = urllib.parse.urlparse(url).netloc or "link"
            out.append(
                f'<a href="{_esc(url)}" target="_blank" rel="noopener noreferrer" '
                f'class="msg-link">{_esc(domain)}</a>'
            )
            out.append(_esc(trail))
            last = m.end()
        out.append(_esc(line[last:]))
        return "".join(out)

    paragraphs = re.split(r"\n\s*\n", text.strip())
    return "".join(
        '<p class="msg-para">' + "<br>".join(linkify_line(line) for line in para.split("\n")) + "</p>"
        for para in paragraphs
        if para.strip()
    )


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("username"):
            return redirect(url_for("login"))  # studio.py's top-level login, not this blueprint's
        return view(*args, **kwargs)
    return wrapped


SHARED_CSS = """
:root {
  --ground:#0B1220; --surface:#141C30; --card:#182240; --border:#2A3552; --text:#EDEFF5;
  --text-muted:#8791A8; --accent:#6C7CFF; --danger:#E8A5A0; --danger-strong:#D9695F;
  --star:#F0B429; --good:#5FCE9A;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--ground); color:var(--text); font-family:'IBM Plex Sans',system-ui,sans-serif; }
input[type=text], input[type=password], input[type=email], textarea {
  width:100%; background:var(--surface); border:1px solid var(--border); border-radius:10px;
  padding:14px 16px; font-size:1rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif;
}
input[type=text]:focus, input[type=password]:focus, input[type=email]:focus, textarea:focus { outline:none; border-color:var(--accent); }
input[type=text]::placeholder, input[type=password]::placeholder, input[type=email]::placeholder, textarea::placeholder { color:var(--text-muted); }
button, .btn { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:11px 18px; font-size:.85rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif; }
button:hover, .btn:hover { opacity:.92; }
button.secondary { background:transparent; border:1px solid var(--border); color:var(--text); }
button.danger { background:var(--danger-strong); }
button:disabled { opacity:.5; cursor:default; }
.error { color:var(--danger); font-family:'IBM Plex Mono',monospace; font-size:.8rem; margin-top:16px; }

/* ---- authenticated app shell: fixed left sidebar + scrolling main pane ---- */
.mail-shell { display:flex; min-height:100vh; align-items:stretch; }
.mail-sidebar {
  width:230px; flex:none; background:var(--surface); border-right:1px solid var(--border);
  padding:22px 14px; display:flex; flex-direction:column; gap:20px; position:sticky; top:0;
  height:100vh; overflow-y:auto;
}
.mail-sidebar-brand { display:flex; align-items:center; gap:10px; padding:0 8px; }
.mail-sidebar .eyebrow { font-size:1.05rem; font-family:'Sora',sans-serif; font-weight:700; letter-spacing:.02em; color:var(--text); margin:0; }
/* Matches studio.py's identical .view-toggle-btn exactly — same top-left
   icon-button look on both "normal" (Studio chat) and "full" (this real
   inbox) view, so switching between them is one click from the same
   corner regardless of which one you're on. */
.view-toggle-btn {
  display:inline-flex; align-items:center; justify-content:center; width:32px; height:32px;
  border-radius:8px; background:transparent; border:1px solid var(--border); color:var(--text-muted);
  text-decoration:none; font-size:1rem; flex:none;
}
.view-toggle-btn:hover { border-color:var(--accent); color:var(--text); background:rgba(108,124,255,.1); }
.compose-btn {
  display:flex; align-items:center; justify-content:center; gap:8px; width:100%;
  background:var(--accent); color:#fff; border:none; border-radius:24px; padding:13px 16px;
  font-size:.86rem; font-weight:600; cursor:pointer; font-family:'IBM Plex Sans',sans-serif;
  text-decoration:none;
}
.compose-btn:hover { opacity:.92; }
.sidebar-nav { display:flex; flex-direction:column; gap:2px; }
.sidebar-folder {
  display:flex; justify-content:space-between; align-items:center; padding:9px 12px;
  border-radius:8px; color:var(--text-muted); text-decoration:none; font-size:.85rem;
  font-family:'IBM Plex Sans',sans-serif;
}
.sidebar-folder:hover { background:rgba(108,124,255,.08); color:var(--text); }
.sidebar-folder.active { background:var(--accent); color:#fff; }
.sidebar-footer { margin-top:auto; font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); padding:0 8px; }
.sidebar-footer a { color:var(--text-muted); }
.sidebar-footer a:hover { color:var(--accent); }
.mail-main { flex:1; min-width:0; padding:28px 32px 80px; max-width:820px; }

.flash { font-family:'IBM Plex Mono',monospace; font-size:.8rem; padding:10px 14px; border-radius:8px; margin-bottom:18px; }
.flash-ok { background:rgba(34,122,85,.15); color:#5FCE9A; border:1px solid rgba(34,122,85,.4); }
.flash-err { background:rgba(198,57,47,.12); color:#E8A5A0; border:1px solid rgba(198,57,47,.35); }

.search-row { display:flex; gap:8px; margin-bottom:8px; }
.search-row input[type=text] { flex:1; }

.back-link { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); text-decoration:none; margin:0 0 14px; }
.back-link:hover { color:var(--accent); }

.panel { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:6px 20px; margin-bottom:12px; }
.list-row { display:flex; justify-content:space-between; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--border); }
.list-row:last-child { border-bottom:none; }
.list-row-main { flex:1; min-width:0; display:block; text-decoration:none; color:inherit; padding:4px 0; }
.list-name { font-weight:600; font-size:.88rem; }
.list-note { font-size:.78rem; color:var(--text-muted); margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.inline-form { display:inline-flex; margin:0; }
.badge { display:inline-block; font-family:'IBM Plex Mono',monospace; font-size:.68rem; font-weight:500; letter-spacing:.03em; text-transform:uppercase; padding:3px 9px; border-radius:4px; flex:none; }
.badge.warning { background:rgba(240,180,41,.15); color:var(--star); }
.thread-count { font-weight:400; color:var(--text-muted); font-size:.82rem; }

.row-actions { display:flex; align-items:center; gap:2px; flex:none; }
.icon-btn {
  background:transparent; border:none; color:var(--text-muted); cursor:pointer; font-size:1rem;
  line-height:1; padding:6px 7px; border-radius:6px;
}
.icon-btn:hover { background:rgba(108,124,255,.12); color:var(--text); }
.icon-btn.star.active { color:var(--star); }
.icon-btn.trash:hover { color:var(--danger-strong); background:rgba(217,105,95,.12); }

.msg-detail { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:20px 22px; margin-bottom:14px; max-width:100%; overflow:hidden; }
.msg-subject { font-family:'Sora',sans-serif; font-weight:600; font-size:1.1rem; margin:0 0 6px; }
.msg-meta { font-size:.8rem; color:var(--text-muted); margin:0 0 16px; }
/* overflow-wrap is the real fix for a marketing email's raw, un-spaced
   tracking-link token (see _linkify_and_nl2br's comment) — _linkify_and_nl2br
   already shortens most of these to a short domain label, but this stays as
   a hard backstop for anything that regex doesn't catch (or any other long
   unbroken token — a hash, an order id), so the message pane's width can
   never blow out. */
.msg-body { font-size:.9rem; line-height:1.65; border-top:1px solid var(--border); padding-top:14px; overflow-wrap:anywhere; }
.msg-para { margin:0 0 14px; }
.msg-para:last-child { margin-bottom:0; }
.msg-link { color:var(--accent); text-decoration:none; border-bottom:1px solid rgba(108,124,255,.4); }
.msg-link:hover { border-bottom-color:var(--accent); }

/* Added 2026-08-27 alongside get_attachment() — a real download chip per
   attachment, not a decorative icon; the count label mirrors real
   Gmail's own "N Attachments" wording above its thumbnail strip. */
.msg-attachments { border-top:1px solid var(--border); margin-top:16px; padding-top:14px; }
.attachments-label { font-family:'IBM Plex Mono',monospace; font-size:.68rem; letter-spacing:.05em; text-transform:uppercase; color:var(--text-muted); margin:0 0 10px; }
.attachments-row { display:flex; flex-wrap:wrap; gap:8px; }
.attachment-chip {
  display:inline-flex; align-items:center; gap:8px; background:var(--surface); border:1px solid var(--border);
  border-radius:8px; padding:8px 12px; font-size:.82rem; color:var(--text); text-decoration:none; max-width:100%;
}
.attachment-chip:hover { border-color:var(--accent); color:var(--accent); }
.attachment-size { color:var(--text-muted); font-size:.72rem; white-space:nowrap; }

/* Added 2026-08-27 for render_thread()'s <details>/<summary> per-message
   accordion — collapsed rows read as a compact sender/date line; the
   expanded (open) message gets the same body/attachments treatment as
   the single-message view above. */
.thread-msg { border-top:1px solid var(--border); }
.thread-msg:first-child { border-top:none; }
.thread-msg-summary {
  display:flex; justify-content:space-between; align-items:center; gap:12px;
  padding:14px 0; cursor:pointer; list-style:none;
}
.thread-msg-summary::-webkit-details-marker { display:none; }
.thread-msg-from { font-weight:600; font-size:.88rem; }
.thread-msg-date { font-size:.76rem; color:var(--text-muted); flex:none; }
.thread-msg[open] .thread-msg-summary { padding-bottom:6px; }
.thread-msg-body { padding-bottom:16px; }

.msg-toolbar { display:flex; gap:8px; margin-bottom:14px; flex-wrap:wrap; }
.toolbar-btn {
  display:inline-flex; align-items:center; gap:6px; background:var(--card); border:1px solid var(--border);
  color:var(--text); border-radius:8px; padding:8px 14px; font-size:.8rem; font-weight:500; cursor:pointer;
  font-family:'IBM Plex Sans',sans-serif; text-decoration:none;
}
.toolbar-btn:hover { border-color:var(--accent); }
.toolbar-btn.active { color:var(--star); border-color:var(--star); }
.toolbar-btn.danger:hover { border-color:var(--danger-strong); color:var(--danger-strong); }

.reply-box { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:20px 22px; }
.reply-box h3 { font-family:'Sora',sans-serif; font-weight:600; font-size:.92rem; margin:0 0 12px; color:var(--text); }
.reply-box textarea { width:100%; min-height:160px; resize:vertical; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:12px 14px; font-family:'IBM Plex Sans',sans-serif; font-size:.88rem; color:var(--text); }
.draft-row { display:flex; gap:8px; margin-bottom:12px; }
.draft-row input[type=text] { flex:1; background:var(--surface); border:1px solid var(--border); border-radius:8px; padding:9px 12px; font-size:.82rem; color:var(--text); font-family:'IBM Plex Sans',sans-serif; }
.draft-row button, .send-row button { font-family:'IBM Plex Sans',sans-serif; font-weight:600; font-size:.82rem; border:none; border-radius:6px; padding:9px 16px; cursor:pointer; }
.draft-row button { background:var(--border); color:var(--text); }
.send-row { margin-top:12px; display:flex; justify-content:flex-end; }
.send-row button { background:var(--accent); color:#fff; }
.hint { font-size:.72rem; color:var(--text-muted); margin-top:8px; }

.compose-form { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:20px 22px; display:flex; flex-direction:column; gap:12px; }
.compose-form label { font-family:'IBM Plex Mono',monospace; font-size:.68rem; letter-spacing:.05em; text-transform:uppercase; color:var(--text-muted); display:block; margin-bottom:5px; }
.compose-form .field { display:flex; flex-direction:column; }
.compose-form input[type=text], .compose-form input[type=email] { background:var(--surface); }
.compose-form textarea { min-height:280px; background:var(--surface); }
.compose-row { display:flex; gap:12px; }
.compose-row .field { flex:1; }

.searchbox { border:1px solid var(--border); border-radius:10px; padding:16px 18px; margin-bottom:20px; background:var(--surface); }
.searchbox-label { font-family:'IBM Plex Mono',monospace; font-size:.68rem; letter-spacing:.06em; text-transform:uppercase; color:var(--accent); margin:0 0 10px; }
.searchbox .search-row { margin-bottom:0; }
.query-hint { font-family:'IBM Plex Mono',monospace; font-size:.72rem; color:var(--text-muted); margin:10px 0 0; }
.query-hint strong { color:var(--text); font-weight:500; }
"""


def _sidebar_nav_html(active_folder):
    """Real Gmail's left-nav folder list, plus the Compose button above it."""
    items = []
    for key, label in _FOLDER_TABS:
        cls = "sidebar-folder active" if key == active_folder else "sidebar-folder"
        href = "/inbox" if key is None else f"/inbox?folder={_esc(key)}"
        items.append(f'<a class="{cls}" href="{href}">{_esc(label)}</a>')
    return (
        '<a class="compose-btn" href="/compose">&#9998; Compose</a>'
        f'<nav class="sidebar-nav">{"".join(items)}</nav>'
    )


def _app_shell(user, title, active_folder, body_html):
    """The real, full Gmail-style interface: a fixed left sidebar (Compose +
    every real folder) and a scrolling main pane on the right. As of
    2026-08-26's merge into Studio, this shell is intentionally its own
    look (not Studio's chat+sidebar shell) — this IS the "full view", a
    real inbox page, reached via a link from within Studio rather than a
    panel embedded inside the chat UI. "log out" here ends the same
    Studio session (shared cookie), so it's a real, working link, not a
    dead end back to a page that no longer exists."""
    out = (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>{{TITLE}}</title>"
        "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
        "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Sora:wght@600;700&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap\">"
        "<style>{{CSS}}</style></head><body>"
        "<div class=\"mail-shell\">"
        "<div class=\"mail-sidebar\">"
        "<div class=\"mail-sidebar-brand\">"
        "<a class=\"view-toggle-btn\" href=\"/studio\" title=\"Back to Studio chat\">&#128172;</a>"
        "<p class=\"eyebrow\">Pilant Mail</p>"
        "</div>"
        "{{SIDEBAR_NAV}}"
        "<div class=\"sidebar-footer\">{{USER}}<br><a href=\"/logout\">log out</a></div>"
        "</div>"
        "<div class=\"mail-main\">{{BODY}}</div>"
        "</div></body></html>"
    )
    out = out.replace("{{TITLE}}", _esc(title))
    out = out.replace("{{CSS}}", SHARED_CSS)
    out = out.replace("{{SIDEBAR_NAV}}", _sidebar_nav_html(active_folder))
    out = out.replace("{{USER}}", _esc(user["name"]))
    out = out.replace("{{BODY}}", body_html)
    return out


def _flash_html():
    ok = session.pop("flash_ok", None)
    err = session.pop("flash_err", None)
    out = ""
    if ok:
        out += f'<div class="flash flash-ok">{_esc(ok)}</div>'
    if err:
        out += f'<div class="flash flash-err">{_esc(err)}</div>'
    return out


def _scope_err(e, needed_scope):
    """Every write action needing gmail.modify (delete/star/mark-unread) or
    gmail.send (reply/compose/forward) gets the same 'clear message
    pointing at the scope issue instead of a raw 403' treatment — every
    write route below reuses this instead of repeating the same lines."""
    err = str(e)
    if "403" in err:
        err += (
            f" — this usually means GMAIL_REFRESH_TOKEN wasn't authorized with {needed_scope}. "
            "See connectors_gmail.py's module docstring for the OAuth Playground steps to "
            "re-authorize with it added."
        )
    return err


# Which folders should show who a message was sent TO instead of who it's
# FROM — in both, "from" is just the connected account's own address, so
# showing it is never useful; the recipient is the real information.
_TO_FACING_FOLDERS = {"sent", "drafts"}


def _thread_row_html(thread, folder=None, next_url="/inbox"):
    """The list row for the (now thread-grouped, as of 2026-08-27) inbox
    view — a row is not a single giant <a> (which couldn't contain real
    buttons — an <a> can't legally nest a <form>), so the subject/snippet
    area is its own link and the star/delete icon buttons sit beside it
    as tiny same-row forms, same structure the old flat per-message rows
    used before this date, just pointed at /thread/<id> and the
    thread-level star_thread()/trash_thread() actions: real Gmail's own
    inbox behavior collapses several messages in one conversation into
    ONE row with a count ("Revolut (3)"), and starring/deleting from that
    row acts on the whole conversation, not just one message in it.
    `next_url` is where each action's POST route redirects back to (the
    exact inbox view — folder + search — the row was showing)."""
    badge = '<span class="badge warning">Unread</span>' if thread.get("unread") else ""
    if folder in _TO_FACING_FOLDERS:
        who = f'To: {_esc(thread.get("to") or "(no recipient)")}'
    else:
        who = _esc(thread.get("from") or "")
    count = thread.get("count") or 1
    count_html = f' <span class="thread-count">({count})</span>' if count > 1 else ""
    href = f'/thread/{_esc(thread["id"])}' + (f'?folder={_esc(folder)}' if folder else '')
    tid = _esc(thread["id"])
    nxt = _esc(next_url)
    starred = bool(thread.get("starred"))
    star_icon = "&#9733;" if starred else "&#9734;"
    actions = (
        '<div class="row-actions">'
        f'<form class="inline-form" method="post" action="/thread/{tid}/star">'
        f'<input type="hidden" name="starred" value="{"1" if starred else "0"}">'
        f'<input type="hidden" name="next" value="{nxt}">'
        f'<button type="submit" class="icon-btn star{" active" if starred else ""}" '
        f'title="{"Unstar" if starred else "Star"}">{star_icon}</button></form>'
        f'<form class="inline-form" method="post" action="/thread/{tid}/delete">'
        f'<input type="hidden" name="next" value="{nxt}">'
        f'<button type="submit" class="icon-btn trash" title="Delete">&#128465;</button></form>'
        '</div>'
    )
    return (
        f'<div class="list-row">'
        f'<a class="list-row-main" href="{href}">'
        f'<div class="list-name">{who}{count_html}</div>'
        f'<div class="list-note">{_esc(thread.get("subject") or "(no subject)")} &middot; '
        f'{_esc(thread.get("snippet") or "")}</div>'
        f'</a>{actions}{badge}</div>'
    )


def _embedded_row_html(msg, next_url):
    """A row for gmail_bp's real inbox, styled with renderer.py's GMAIL_CSS
    classes (.gmail-row and friends) instead of this file's own .list-row
    — added 2026-08-26 so gmail_bp's render_inbox_panel() (below) looks
    identical to the chat's old read-only preview it replaces, but every
    element here is real: opening the message is a real link, star and
    delete are real <form> posts to this blueprint's own routes. `next_url`
    is where star/delete redirect back to — /studio when this is embedded
    on Studio's chat page, so the action lands back there instead of
    bouncing to /inbox."""
    mid = _esc(msg["id"])
    nxt = _esc(next_url)
    starred = bool(msg.get("starred"))
    star_icon = "&#9733;" if starred else "&#9734;"
    is_unread = bool(msg.get("unread"))
    row_cls = "gmail-row gmail-row-unread" if is_unread else "gmail-row"
    sender = _esc(msg.get("from") or "")
    subject = _esc(msg.get("subject") or "(no subject)")
    snippet = _esc(msg.get("snippet") or "")
    snippet_html = f' <span class="gmail-snippet">&mdash; {snippet}</span>' if snippet else ""
    status_html = '<span class="gmail-row-status gmail-row-status-warning">Unread</span>' if is_unread else ""
    # Added 2026-08-29 at the user's direct request, after screenshots
    # showed clicking a row here navigating away to the separate,
    # differently-branded "Pilant Mail" full page (render_message below):
    # "if we click the mail it's navigating to next page want to do
    # everything in the studio front page." next_url is always "/studio"
    # for every real caller of render_inbox_panel (see its own docstring),
    # so this is a real link BACK to Studio's own page with ?panel_message=
    # set, not a click-handler — the /studio route reads that and swaps the
    # panel to render_inbox_panel_message() (below) instead of the list,
    # entirely without leaving /studio.
    href = f"{next_url}?panel_message={mid}"
    star_form = (
        f'<form class="inline-form" method="post" action="/message/{mid}/star">'
        f'<input type="hidden" name="starred" value="{"1" if starred else "0"}">'
        f'<input type="hidden" name="next" value="{nxt}">'
        f'<button type="submit" class="gmail-row-star gmail-row-star-btn{" active" if starred else ""}" '
        f'title="{"Unstar" if starred else "Star"}">{star_icon}</button></form>'
    )
    trash_form = (
        f'<form class="inline-form" method="post" action="/message/{mid}/delete">'
        f'<input type="hidden" name="next" value="{nxt}">'
        f'<button type="submit" class="gmail-row-delete" title="Delete">&#128465;</button></form>'
    )
    return (
        f'<div class="{row_cls}">'
        f'{star_form}'
        f'<a class="gmail-row-sender" href="{href}">{sender}</a>'
        f'<a class="gmail-row-subject" href="{href}">{subject}{snippet_html}</a>'
        f'{status_html}'
        f'{trash_form}'
        '</div>'
    )


def render_inbox_panel(next_url="/studio", limit=25, folder="inbox", query=None):
    """The real, working inbox — same deterministic get_gmail_messages()
    call and the exact same real star/delete/mark-read actions as /inbox,
    just laid out to drop directly into Studio's chat live-preview pane
    instead of needing its own page. Added 2026-08-26 at the user's
    explicit request ("still can't control from this page ... just want
    to extend the interface not a separate full view mode") — the chat's
    OLD preview (renderer.render_gmail_fragment) was deliberately
    read-only because render_view's schema has no real message ID field
    (see this module's top docstring on why the LLM is never trusted to
    pick which real email a delete/star action targets); this sidesteps
    that entirely by not routing through the LLM's rendering at all — it's
    the same deterministic code path /inbox already uses, just rendered
    where the user is actually looking. Reading a specific message still
    opens its own page (a full message deserves more room than an inline
    panel), but star/delete/mark-read work right here.

    `folder` — added 2026-08-29 alongside render_inbox_panel_tabs() below,
    for the panel's own folder strip ("have all the options like spam,
    sent, everything in this page"). Defaults to "inbox", same as before
    this parameter existed. None is a DELIBERATE, legitimate value here —
    it's what the "All Mail" tab passes (see resolve_panel_folder) and
    matches get_gmail_messages' own "no folder given" meaning — so it is
    NOT touched by the fallback below; only a truly unrecognized non-None
    value (a typo'd or tampered string) falls back to "inbox", same "never
    silently widen to unscoped All Mail" rule the 2026-08-27 fix
    established, now enforced per-call instead of at one hardcoded site.

    `query` — added 2026-08-29 so the panel can be scoped to the exact
    real Gmail search a Studio chat request just fetched with (studio.py
    passes wf["gmail_panel_query"], built from the render's real
    fetch_args — see agent_gmail.py's dispatch() and studio.py's
    _handle_studio_message), not a second, separately-guessed
    interpretation of the chat text. None (the default) means no
    filtering beyond `folder`, same as before this parameter existed.
    """
    if folder is not None and folder not in FOLDERS:
        # 2026-08-27 note, still true: get_gmail_messages(folder=None)
        # defaults to unscoped "All Mail" (every label except Spam/Trash),
        # not what a person means by "my inbox" — an invalid/tampered
        # folder value should fall back to the real Inbox scope, not
        # silently widen to that. A genuine None (see docstring above)
        # is left exactly as-is, since that IS the All Mail scope, chosen
        # deliberately rather than defaulted into.
        folder = "inbox"
    try:
        messages = get_gmail_messages(unread_only=False, limit=limit, folder=folder, query=query)
    except RuntimeError as e:
        return f'<div class="gmail-empty">{_esc(str(e))}</div>'
    if not messages:
        label = dict(_FOLDER_TABS).get(folder, "Inbox")
        scope = f'{label} matching "{query}"' if query else label
        return f'<div class="gmail-empty">No messages in {_esc(scope)}.</div>'
    return "".join(_embedded_row_html(m, next_url) for m in messages)


def render_inbox_panel_message(message_id, next_url="/studio"):
    """Compact, panel-embedded rendering of ONE real message — added
    2026-08-29 at the user's direct request, after screenshots showed
    opening a message from the Studio panel navigating away to the
    separate, differently-branded "Pilant Mail" full page (render_message
    below): "want to do everything in the studio front page." Reuses the
    exact same real data and actions render_message's page does
    (get_message_full, mark_read-on-open, star/delete/reply/draft) —
    only the shell is new: no sidebar, no separate page title, styled
    with renderer.py's gmail-panel-* classes instead of this file's own
    .msg-detail/.msg-toolbar/.reply-box (those belong to the full /inbox
    page's own SHARED_CSS, which Studio's page shell never loads — see
    studio.py's docstrings on the two files keeping separate stylesheets).

    Forward still opens its own page (/message/<id>/forward) rather than
    being inlined here too — it needs a real recipient-picking compose
    flow the reply box has no concept of (same reason render_message's
    own toolbar links out to it instead of inlining it), so a dedicated
    screen is the right amount of navigation for THAT one action, same as
    real Gmail's own Forward always opening its own compose window.

    Returns an error fragment (not a redirect — the panel itself IS the
    destination here, there is no separate page to bounce to) if the
    message can't be loaded, e.g. a stale/deleted message ID."""
    try:
        msg = get_message_full(message_id)
    except RuntimeError as e:
        return f'<div class="gmail-empty">Couldn&#39;t open that message: {_esc(str(e))}</div>'

    # Same best-effort mark-read-on-open as view_message() below.
    if msg.get("unread"):
        try:
            mark_read(message_id, read=True)
            msg["unread"] = False
        except RuntimeError:
            pass

    # draft_message()/reply_message() below stash a just-generated draft
    # and/or a reply outcome in the session and redirect back HERE (this
    # exact GET) rather than rendering inline — same "the next real page
    # load picks it up" pattern view_message()'s own draft_text already
    # uses, just reusing the SAME session["flash_ok"]/["flash_err"] keys
    # every other action on this blueprint already sets, instead of
    # inventing a second, panel-only notice mechanism.
    draft_text = session.pop(f"draft:{message_id}", "")
    flash_ok = session.pop("flash_ok", None)
    flash_err = session.pop("flash_err", None)

    mid = _esc(msg["id"])
    panel_url = f"{next_url}?panel_message={mid}"
    starred = bool(msg.get("starred"))
    star_icon = "&#9733;" if starred else "&#9734;"
    to_line = f' &middot; To {_esc(msg["to"])}' if msg.get("to") else ""
    forward_href = f'/message/{mid}/forward'

    toolbar = (
        '<div class="gmail-panel-msg-toolbar">'
        f'<a class="panel-ctrl-btn" href="{_esc(next_url)}" title="Back to list">&larr;</a>'
        f'<form class="inline-form" method="post" action="/message/{mid}/delete">'
        f'<input type="hidden" name="next" value="{_esc(next_url)}">'
        '<button type="submit" class="gmail-panel-msg-btn gmail-panel-msg-btn-danger">&#128465; Delete</button></form>'
        f'<form class="inline-form" method="post" action="/message/{mid}/star">'
        f'<input type="hidden" name="starred" value="{"1" if starred else "0"}">'
        f'<input type="hidden" name="next" value="{_esc(panel_url)}">'
        f'<button type="submit" class="gmail-panel-msg-btn{" active" if starred else ""}">'
        f'{star_icon} {"Unstar" if starred else "Star"}</button></form>'
        f'<form class="inline-form" method="post" action="/message/{mid}/read">'
        f'<input type="hidden" name="next" value="{_esc(next_url)}">'
        '<button type="submit" class="gmail-panel-msg-btn">&#9993; Mark unread</button></form>'
        f'<a class="gmail-panel-msg-btn" href="{_esc(forward_href)}">&#8618; Forward</a>'
        '</div>'
    )

    notice_html = ""
    if flash_ok:
        notice_html += f'<p class="gmail-panel-notice gmail-panel-notice-ok">{_esc(flash_ok)}</p>'
    if flash_err:
        notice_html += f'<p class="gmail-panel-notice gmail-panel-notice-err">{_esc(flash_err)}</p>'

    detail = (
        f'<div class="gmail-panel-message">'
        f'<p class="gmail-panel-msg-subject">{_esc(msg.get("subject") or "(no subject)")}</p>'
        f'<p class="gmail-panel-msg-meta">From {_esc(msg.get("from") or "")}{to_line} &middot; {_esc(msg.get("date") or "")}</p>'
        f'<div class="gmail-panel-msg-body">{_linkify_and_nl2br(msg.get("body") or "(empty message body)")}</div>'
        f'{_attachments_html(msg)}'
        f'</div>'
    )

    reply_box = (
        '<div class="gmail-panel-reply">'
        '<p class="gmail-panel-reply-label">Reply</p>'
        f'<form method="post" action="/message/{mid}/draft" class="gmail-panel-draft-row">'
        f'<input type="hidden" name="next" value="{_esc(panel_url)}">'
        '<input type="text" name="instruction" placeholder="Optional: tell the AI what to say (e.g. \'politely decline\')...">'
        '<button type="submit" class="gmail-panel-msg-btn">Generate draft</button>'
        '</form>'
        f'<form method="post" action="/message/{mid}/reply">'
        f'<input type="hidden" name="next" value="{_esc(panel_url)}">'
        f'<textarea name="body" class="gmail-panel-reply-textarea" placeholder="Write your reply...">{_esc(draft_text)}</textarea>'
        '<div class="gmail-panel-reply-send"><button type="submit" class="gmail-panel-msg-btn gmail-panel-msg-btn-primary">Send reply</button></div>'
        '</form>'
        '<p class="gmail-panel-reply-hint">Nothing sends until you click Send reply &mdash; an AI-generated '
        'draft is only ever a suggestion you can edit or delete first.</p>'
        '</div>'
    )

    return f'{toolbar}{notice_html}{detail}{reply_box}'


def render_inbox_panel_tabs(active_folder, next_url="/studio"):
    """A compact horizontal folder strip for the embedded Studio panel —
    the exact same real Gmail folders _FOLDER_TABS already lists for the
    full /inbox sidebar (Inbox/Sent/Drafts/Starred/Important/Spam/Trash,
    plus All Mail), added 2026-08-29 at the user's request ("have all the
    options like spam, sent, everything in this page") so switching
    folders no longer means leaving Studio for the full /inbox view.

    Each tab is a real link back to `next_url` with ?panel_folder=<key>
    ("all" stands in for the unscoped All Mail folder's real key, None,
    since a URL can't carry a bare None) — studio.py's /studio route reads
    that via resolve_panel_folder() below and re-renders this same panel
    scoped to the chosen folder. A real page navigation, same as every
    other folder switch in this app already is, not a client-side fetch."""
    items = []
    for key, label in _FOLDER_TABS:
        param = "all" if key is None else key
        is_active = (key or "all") == (active_folder or "all")
        cls = "gmail-panel-tab active" if is_active else "gmail-panel-tab"
        href = f"{next_url}?panel_folder={param}"
        items.append(f'<a class="{cls}" href="{_esc(href)}">{_esc(label)}</a>')
    return f'<div class="gmail-panel-tabs">{"".join(items)}</div>'


PANEL_CHIPS = (
    ("unread", "Unread", "is:unread"),
    ("starred", "Starred", "is:starred"),
    ("important", "Important", "is:important"),
)


def render_inbox_panel_chips(active_query, next_url="/studio"):
    """"Improvement idea #1" from the interface-comparison discussion, built
    2026-08-29 right after idea #2 (the panel's own search box, just above):
    a row of one-tap filter chips — Unread / Starred / Important — sitting
    alongside the folder tabs and search box rather than replacing either.
    The whole point is "no typing needed" for the three filters people reach
    for constantly, where the search box (and chat) stay there for anything
    more specific.

    Each chip is a plain GET link back to `next_url` with ?panel_chip=<key>
    — a real page navigation, same as the folder tabs, not a client-side
    toggle — and is marked active when the panel's current real query is
    EXACTLY that chip's search term (studio.py's /studio route does the
    actual toggle-on/toggle-off; this function only renders what's
    currently true). Clicking an already-active chip's link re-sends the
    same key, which the route reads as "turn it back off.\""""
    active_query = (active_query or "").strip()
    items = []
    for key, label, term in PANEL_CHIPS:
        cls = "gmail-panel-chip active" if active_query == term else "gmail-panel-chip"
        href = f"{next_url}?panel_chip={key}"
        items.append(f'<a class="{cls}" href="{_esc(href)}">{_esc(label)}</a>')
    return f'<div class="gmail-panel-chips">{"".join(items)}</div>'


def resolve_panel_chip(raw_key):
    """Turns a raw ?panel_chip= value (one of render_inbox_panel_chips' own
    links) into the real Gmail search term it stands for, or None if it
    isn't a recognized chip key (unset/typo'd/tampered) — same defensive
    "unrecognized input doesn't silently do something surprising" pattern
    resolve_panel_folder above already follows."""
    for key, _label, term in PANEL_CHIPS:
        if key == raw_key:
            return term
    return None


def resolve_panel_folder(raw):
    """Turns a raw ?panel_folder= query value (from render_inbox_panel_tabs'
    own links, or absent on the panel's first load) into a real folder key
    for render_inbox_panel/render_inbox_panel_tabs — keeps studio.py from
    needing to know connectors_gmail.FOLDERS' keys or this "all" URL
    encoding itself. "all" maps to None (the real All Mail scope); any
    other unrecognized value (unset, typo'd, tampered) falls back to
    "inbox" — same "never silently widen to unscoped All Mail" rule
    render_inbox_panel itself enforces for defense in depth."""
    if raw == "all":
        return None
    if raw in FOLDERS:
        return raw
    return "inbox"


def resolve_panel_query(raw_text):
    """Turns free text typed into the embedded Studio panel's OWN search
    box (added 2026-08-29, POST /studio/panel_search) into a real Gmail
    search query — the exact same one-step translation /inbox's search box
    already uses below (_looks_like_gmail_search + translate_to_search_query),
    factored out here so the panel doesn't grow a second, separately
    -maintained copy of "type real syntax or plain English, either works."

    Returns the real query to actually search with — real syntax used
    verbatim, natural language translated via one model call, or (if that
    translation fails or comes back empty) the raw text used as-is, same
    three-way fallback /inbox's own route documents. Returns None for
    blank/whitespace-only input (an empty search box submission clears
    the panel's filter rather than searching for nothing)."""
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return None
    if _looks_like_gmail_search(raw_text):
        return raw_text
    return translate_to_search_query(raw_text) or raw_text


_GMAIL_OPERATOR_RE = re.compile(
    r"\b(from|to|cc|bcc|subject|is|has|label|newer_than|older_than|after|before|"
    r"filename|in|category|list|deliveredto|rfc822msgid|size|larger|smaller):\S"
)


def _looks_like_gmail_search(text):
    """True if `text` already contains real Gmail search operator syntax
    (from:, is:unread, newer_than:7d, ...) — in which case it should be
    sent to get_gmail_messages verbatim, untouched by the LLM. Only text
    that does NOT match this gets routed through translate_to_search_query,
    so anyone who already knows Gmail search syntax gets exact, immediate,
    deterministic behavior with no model call in the loop at all."""
    return bool(_GMAIL_OPERATOR_RE.search(text or ""))


# Ordered for display — "All Mail" (folder=None, the original/default
# behavior, unchanged) first, then Gmail's real built-in views in roughly
# the order Gmail's own left nav shows them. Used both for the sidebar nav
# (_sidebar_nav_html, above) and for page titles/back-link labels below.
_FOLDER_TABS = [
    (None, "All Mail"),
    ("inbox", "Inbox"),
    ("sent", "Sent"),
    ("drafts", "Drafts"),
    ("starred", "Starred"),
    ("important", "Important"),
    ("spam", "Spam"),
    ("trash", "Trash"),
]

# A sentinel for "highlight nothing in the sidebar" — Compose and Forward
# aren't folders, so passing None (which means "All Mail") to _app_shell
# would incorrectly light up the All Mail tab. No real folder key or the
# None used for All Mail can ever equal this string.
_NO_FOLDER_ACTIVE = "__none__"


def _url_with_params(path, **params):
    """Builds path?k=v&... from only the truthy params — used everywhere a
    route needs to redirect back to 'wherever the user actually was'
    (current folder + search query) rather than always bouncing to a
    fixed page."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v})
    return f"{path}?{qs}" if qs else path


def render_inbox(user, threads, search_q, folder=None, interpreted_q=None, error=None):
    """Renders the list view as real Gmail conversations (thread-grouped,
    via get_gmail_threads()) rather than individual messages — changed
    2026-08-27, see _thread_row_html's docstring. `threads` carries the
    shape get_gmail_threads() returns (from/to/subject/snippet/count/
    unread/starred/id), not the flat get_gmail_messages() shape this
    function used before that date. _thread_row_html switches to showing
    "to" instead of "from" for Sent/Drafts, same _TO_FACING_FOLDERS logic
    the old flat per-message rows used."""
    next_url = _url_with_params("/inbox", folder=folder, q=search_q)
    rows_html = "".join(_thread_row_html(t, folder=folder, next_url=next_url) for t in threads) or (
        '<p class="hint">No messages match.</p>'
    )
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    hint_html = ""
    if interpreted_q and interpreted_q != search_q:
        hint_html = f'<p class="query-hint">interpreted as: <strong>{_esc(interpreted_q)}</strong></p>'
    folder_field = f'<input type="hidden" name="folder" value="{_esc(folder)}">' if folder else ""
    body = (
        f'{_flash_html()}'
        f'<div class="searchbox">'
        f'<p class="searchbox-label">Search {_esc(dict(_FOLDER_TABS).get(folder, "All Mail"))}</p>'
        f'<form class="search-row" method="get" action="/inbox">'
        f'{folder_field}'
        f'<input type="text" name="q" value="{_esc(search_q)}" '
        f'placeholder="Type a real search (from:, subject:, is:unread...) or just ask — '
        f'&quot;unread from paypal this week&quot;..." autocomplete="off">'
        f'<button type="submit">Search</button></form>'
        f'{hint_html}'
        f'</div>'
        f'{error_html}'
        f'<div class="panel">{rows_html}</div>'
    )
    title = f"Pilant Mail — {dict(_FOLDER_TABS).get(folder, 'All Mail')}"
    return _app_shell(user, title, folder, body)


def _format_size(num_bytes):
    """Human-readable file size for the attachment list ('148 KB',
    '1.2 MB') — matches what a person expects, not a raw byte count."""
    try:
        num_bytes = float(num_bytes or 0)
    except (TypeError, ValueError):
        return ""
    if num_bytes < 1024:
        return f"{num_bytes:.0f} B"
    for unit in ("KB", "MB", "GB"):
        num_bytes /= 1024
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.1f} {unit}"
    return ""


def _attachments_html(msg, folder=None):
    """Real attachment list — added 2026-08-27 alongside
    connectors_gmail.get_attachment(), at the user's direct request after
    comparing a message with 4 real PDF attachments against Pilant's
    message view, which had nothing to show for them (the metadata simply
    wasn't being read before now — see get_message_full()'s docstring).
    Each chip links to a real download of that exact attachment via the
    /message/<id>/attachment/<attachment_id> route below — the actual
    bytes are only ever fetched from Gmail on click, not speculatively."""
    attachments = msg.get("attachments") or []
    if not attachments:
        return ""
    mid = _esc(msg["id"])
    folder_qs = f'?folder={_esc(folder)}' if folder else ""
    rows = "".join(
        f'<a class="attachment-chip" href="/message/{mid}/attachment/{_esc(a.get("attachment_id") or "")}{folder_qs}">'
        f'&#128206; {_esc(a.get("filename") or "attachment")}'
        f'<span class="attachment-size">{_esc(_format_size(a.get("size")))}</span>'
        f'</a>'
        for a in attachments
        if a.get("attachment_id")
    )
    if not rows:
        return ""
    count = len(attachments)
    label = f'{count} Attachment' + ("s" if count != 1 else "")
    return (
        f'<div class="msg-attachments">'
        f'<p class="attachments-label">{_esc(label)}</p>'
        f'<div class="attachments-row">{rows}</div>'
        f'</div>'
    )


def render_message(user, msg, draft_text="", error=None, sent_ok=None, folder=None):
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    sent_html = f'<div class="flash flash-ok">{_esc(sent_ok)}</div>' if sent_ok else ""
    back_href = "/inbox" + (f"?folder={_esc(folder)}" if folder else "")
    back_label = f"&larr; {_esc(dict(_FOLDER_TABS).get(folder, 'Inbox'))}"
    to_line = f' &middot; To {_esc(msg["to"])}' if msg.get("to") else ""
    mid = _esc(msg["id"])
    current_url = f'/message/{mid}' + (f'?folder={_esc(folder)}' if folder else '')
    starred = bool(msg.get("starred"))
    star_icon = "&#9733;" if starred else "&#9734;"
    forward_href = f'/message/{mid}/forward' + (f'?folder={_esc(folder)}' if folder else '')
    toolbar = (
        '<div class="msg-toolbar">'
        f'<form class="inline-form" method="post" action="/message/{mid}/delete">'
        f'<input type="hidden" name="next" value="{_esc(back_href)}">'
        '<button type="submit" class="toolbar-btn danger">&#128465; Delete</button></form>'
        f'<form class="inline-form" method="post" action="/message/{mid}/star">'
        f'<input type="hidden" name="starred" value="{"1" if starred else "0"}">'
        f'<input type="hidden" name="next" value="{_esc(current_url)}">'
        f'<button type="submit" class="toolbar-btn{" active" if starred else ""}">'
        f'{star_icon} {"Unstar" if starred else "Star"}</button></form>'
        f'<form class="inline-form" method="post" action="/message/{mid}/read">'
        f'<input type="hidden" name="next" value="{_esc(back_href)}">'
        '<button type="submit" class="toolbar-btn">&#9993; Mark unread</button></form>'
        f'<a class="toolbar-btn" href="{forward_href}">&#8618; Forward</a>'
        '</div>'
    )
    body = (
        f'{_flash_html()}'
        f'<a class="back-link" href="{back_href}">{back_label}</a>'
        f'{toolbar}'
        f'<div class="msg-detail">'
        f'<p class="msg-subject">{_esc(msg.get("subject") or "(no subject)")}</p>'
        f'<p class="msg-meta">From {_esc(msg.get("from") or "")}{to_line} &middot; {_esc(msg.get("date") or "")}</p>'
        f'<div class="msg-body">{_linkify_and_nl2br(msg.get("body") or "(empty message body)")}</div>'
        f'{_attachments_html(msg, folder=folder)}'
        f'</div>'
        f'{sent_html}{error_html}'
        f'<div class="reply-box">'
        f'<h3>Reply</h3>'
        f'<form method="post" action="/message/{mid}/draft" class="draft-row">'
        f'<input type="hidden" name="folder" value="{_esc(folder or "")}">'
        f'<input type="text" name="instruction" placeholder="Optional: tell the AI what to say (e.g. \'politely decline\')...">'
        f'<button type="submit" class="secondary">Generate draft</button>'
        f'</form>'
        f'<form method="post" action="/message/{mid}/reply">'
        f'<input type="hidden" name="folder" value="{_esc(folder or "")}">'
        f'<textarea name="body" placeholder="Write your reply...">{_esc(draft_text)}</textarea>'
        f'<div class="send-row"><button type="submit">Send reply</button></div>'
        f'</form>'
        f'<p class="hint">Nothing sends until you click Send reply — an AI-generated draft is only ever a suggestion you can edit or delete first.</p>'
        f'</div>'
    )
    return _app_shell(user, "Pilant Mail — message", folder, body)


def render_thread(user, thread_id, messages, folder=None, error=None, sent_ok=None, draft_text=""):
    """The conversation view real Gmail opens into when you click a
    multi-message row — added 2026-08-27 alongside get_gmail_threads()/
    get_thread_full(). Every message renders inside <details>/<summary>
    (no JS needed for expand/collapse, and it degrades to "just readable"
    even without CSS) — the LAST (most recent) message opens expanded by
    default, the rest collapsed to a one-line sender/date summary,
    matching real Gmail's own behavior of auto-expanding only the newest
    message. Reply/draft are scoped to the last message (real Gmail's own
    default: replying to a thread replies to its most recent message),
    but carry a hidden `thread` field so a successful send or draft
    redirects back to THIS page instead of bouncing to the single-message
    view — see reply_message()/draft_message()'s thread-aware redirects.
    Delete/Star act on the whole conversation via trash_thread()/
    star_thread(), matching real Gmail: those apply to every message in
    the thread, not just the one currently expanded."""
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    sent_html = f'<div class="flash flash-ok">{_esc(sent_ok)}</div>' if sent_ok else ""
    back_href = "/inbox" + (f"?folder={_esc(folder)}" if folder else "")
    back_label = f"&larr; {_esc(dict(_FOLDER_TABS).get(folder, 'Inbox'))}"
    tid = _esc(thread_id)
    current_url = f'/thread/{tid}' + (f'?folder={_esc(folder)}' if folder else '')
    subject = (messages[-1].get("subject") if messages else None) or "(no subject)"
    any_starred = any(m.get("starred") for m in messages)
    star_icon = "&#9733;" if any_starred else "&#9734;"
    toolbar = (
        '<div class="msg-toolbar">'
        f'<form class="inline-form" method="post" action="/thread/{tid}/delete">'
        f'<input type="hidden" name="next" value="{_esc(back_href)}">'
        '<button type="submit" class="toolbar-btn danger">&#128465; Delete</button></form>'
        f'<form class="inline-form" method="post" action="/thread/{tid}/star">'
        f'<input type="hidden" name="starred" value="{"1" if any_starred else "0"}">'
        f'<input type="hidden" name="next" value="{_esc(current_url)}">'
        f'<button type="submit" class="toolbar-btn{" active" if any_starred else ""}">'
        f'{star_icon} {"Unstar" if any_starred else "Star"} all</button></form>'
        '</div>'
    )

    last_index = len(messages) - 1
    blocks = []
    for i, m in enumerate(messages):
        is_last = i == last_index
        to_line = f' &middot; To {_esc(m["to"])}' if m.get("to") else ""
        summary_html = (
            '<summary class="thread-msg-summary">'
            f'<span class="thread-msg-from">{_esc(m.get("from") or "")}</span>'
            f'<span class="thread-msg-date">{_esc(m.get("date") or "")}</span>'
            '</summary>'
        )
        detail_html = (
            f'<div class="thread-msg-body">'
            f'<p class="msg-meta">From {_esc(m.get("from") or "")}{to_line} &middot; {_esc(m.get("date") or "")}</p>'
            f'<div class="msg-body">{_linkify_and_nl2br(m.get("body") or "(empty message body)")}</div>'
            f'{_attachments_html(m, folder=folder)}'
            f'</div>'
        )
        blocks.append(f'<details class="thread-msg"{" open" if is_last else ""}>{summary_html}{detail_html}</details>')

    last = messages[-1] if messages else {}
    last_id = _esc(last.get("id") or "")
    body = (
        f'{_flash_html()}'
        f'<a class="back-link" href="{back_href}">{back_label}</a>'
        f'{toolbar}'
        f'<div class="msg-detail">'
        f'<p class="msg-subject">{_esc(subject)}</p>'
        f'{"".join(blocks)}'
        f'</div>'
        f'{sent_html}{error_html}'
        f'<div class="reply-box">'
        f'<h3>Reply</h3>'
        f'<form method="post" action="/message/{last_id}/draft" class="draft-row">'
        f'<input type="hidden" name="folder" value="{_esc(folder or "")}">'
        f'<input type="hidden" name="thread" value="{tid}">'
        f'<input type="text" name="instruction" placeholder="Optional: tell the AI what to say (e.g. \'politely decline\')...">'
        f'<button type="submit" class="secondary">Generate draft</button>'
        f'</form>'
        f'<form method="post" action="/message/{last_id}/reply">'
        f'<input type="hidden" name="folder" value="{_esc(folder or "")}">'
        f'<input type="hidden" name="thread" value="{tid}">'
        f'<textarea name="body" placeholder="Write your reply...">{_esc(draft_text)}</textarea>'
        f'<div class="send-row"><button type="submit">Send reply</button></div>'
        f'</form>'
        f'<p class="hint">Replies into the most recent message in this conversation, same as real Gmail. '
        f'Nothing sends until you click Send reply.</p>'
        f'</div>'
    )
    return _app_shell(user, "Pilant Mail — conversation", folder, body)


def render_compose(user, to="", cc="", subject="", body="", error=None, next_url=None):
    """A real 'write a new email' page — separate from the reply box, which
    is scoped to one existing message. Sends via connectors_gmail.send_email
    when the form posts; nothing goes out until that real Send click,
    same review-before-send principle as the reply box. `next_url`, added
    2026-08-26 for the Compose button embedded on Studio's chat page,
    carries through the form as a hidden field and back into the back-link
    so composing from Studio returns to Studio after sending, instead of
    always landing on /inbox."""
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    back_href = next_url or "/inbox"
    back_label = "&larr; Back to Studio" if next_url == "/studio" else "&larr; All Mail"
    next_field = f'<input type="hidden" name="next" value="{_esc(next_url)}">' if next_url else ""
    body_html = (
        f'{_flash_html()}'
        f'<a class="back-link" href="{_esc(back_href)}">{back_label}</a>'
        f'<p class="msg-subject">New message</p>'
        f'{error_html}'
        f'<form method="post" action="/compose" class="compose-form">'
        f'{next_field}'
        f'<div class="compose-row">'
        f'<div class="field"><label>To</label>'
        f'<input type="text" name="to" value="{_esc(to)}" placeholder="recipient@example.com" autofocus required></div>'
        f'<div class="field"><label>Cc (optional)</label>'
        f'<input type="text" name="cc" value="{_esc(cc)}" placeholder="cc@example.com"></div>'
        f'</div>'
        f'<div class="field"><label>Subject</label>'
        f'<input type="text" name="subject" value="{_esc(subject)}" placeholder="Subject"></div>'
        f'<div class="field"><label>Message</label>'
        f'<textarea name="body" placeholder="Write your email...">{_esc(body)}</textarea></div>'
        f'<div class="send-row"><button type="submit">Send</button></div>'
        f'</form>'
    )
    return _app_shell(user, "Pilant Mail — Compose", _NO_FOLDER_ACTIVE, body_html)


def render_forward(user, msg, to="", body="", error=None, folder=None):
    """Forward's own page, not a mode of the reply box — a forward goes to
    a DIFFERENT recipient than the original thread, so it gets its own To
    field, and (like Reply) the full quoted content sits in an editable
    textarea a person reviews before the real Forward click sends it."""
    error_html = f'<p class="error">{_esc(error)}</p>' if error else ""
    mid = _esc(msg["id"])
    back_href = f'/message/{mid}' + (f'?folder={_esc(folder)}' if folder else '')
    body_html = (
        f'{_flash_html()}'
        f'<a class="back-link" href="{back_href}">&larr; Back to message</a>'
        f'<p class="msg-subject">Forward: {_esc(msg.get("subject") or "(no subject)")}</p>'
        f'{error_html}'
        f'<form method="post" action="/message/{mid}/forward" class="compose-form">'
        f'<input type="hidden" name="folder" value="{_esc(folder or "")}">'
        f'<div class="field"><label>To</label>'
        f'<input type="text" name="to" value="{_esc(to)}" placeholder="recipient@example.com" autofocus required></div>'
        f'<div class="field"><label>Message</label>'
        f'<textarea name="body">{_esc(body)}</textarea></div>'
        f'<div class="send-row"><button type="submit">Forward</button></div>'
        f'</form>'
    )
    return _app_shell(user, "Pilant Mail — Forward", folder or _NO_FOLDER_ACTIVE, body_html)


@gmail_bp.route("/inbox")
@login_required
def inbox():
    """
    One merged search box — whatever's typed into `q`:

      1. If it already looks like real Gmail search syntax
         (_looks_like_gmail_search), it's used exactly as typed — no model
         call at all, fully deterministic. This covers "is:unread" too, so
         unread-only browsing still works, just typed instead of tabbed.
      2. Otherwise it's handed to translate_to_search_query(), one single
         plain completion (no tools, no JSON schema, no multi-step loop —
         see that function's docstring) that turns natural language into
         real Gmail search syntax.
      3. If that translation fails or comes back empty, the raw typed text
         is used as-is — Gmail's search already treats a bare phrase as a
         keyword search, so this never blocks the box from doing
         something reasonable.

    With no query at all, this is just the plain recent-messages inbox.

    `folder` is a SEPARATE dimension from the search box above, not a
    replacement for it — the sidebar's real-Gmail-folder links
    (_sidebar_nav_html) set it; picking one scopes every search (or the
    plain unfiltered list, with no `q` typed) to that folder via
    connectors_gmail.FOLDERS, same as typing "in:spam" would. The two
    compose exactly like typing both operators into Gmail's own search
    bar would.
    """
    user = get_user(session["username"])
    search_q = (request.args.get("q") or "").strip()
    folder = (request.args.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None  # an unrecognized/tampered ?folder= falls back to All Mail, not an error page

    interpreted_q = None
    effective_q = search_q or None
    if search_q and not _looks_like_gmail_search(search_q):
        translated = translate_to_search_query(search_q)
        if translated:
            interpreted_q = translated
            effective_q = translated

    try:
        threads = get_gmail_threads(query=effective_q, folder=folder, limit=25)
        error = None
    except RuntimeError as e:
        threads = []
        error = str(e)

    return render_inbox(user, threads, search_q, folder=folder, interpreted_q=interpreted_q, error=error)


@gmail_bp.route("/message/<message_id>")
@login_required
def view_message(message_id):
    user = get_user(session["username"])
    folder = (request.args.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None  # an unrecognized/tampered ?folder= falls back to Inbox, not an error page
    draft_text = session.pop(f"draft:{message_id}", "")
    try:
        msg = get_message_full(message_id)
    except RuntimeError as e:
        session["flash_err"] = f"Couldn't open that message: {e}"
        return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))

    # Real Gmail marks a message read the moment you open it — replicated
    # here, best-effort: if GMAIL_REFRESH_TOKEN doesn't have gmail.modify
    # yet, this silently no-ops rather than breaking the ability to just
    # read the message, which never needed that scope. "Mark unread" in
    # the toolbar below reverses it, same as real Gmail.
    if msg.get("unread"):
        try:
            mark_read(message_id, read=True)
            msg["unread"] = False
        except RuntimeError:
            pass

    return render_message(user, msg, draft_text=draft_text, folder=folder)


@gmail_bp.route("/thread/<thread_id>")
@login_required
def view_thread(thread_id):
    """The conversation view — added 2026-08-27 alongside
    get_gmail_threads()/get_thread_full(), reached by clicking a
    thread-grouped row in /inbox. See render_thread()'s docstring for the
    expand/collapse and reply-to-latest behavior."""
    user = get_user(session["username"])
    folder = (request.args.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None
    draft_text = session.pop(f"draft:{thread_id}", "")
    try:
        thread = get_thread_full(thread_id)
    except RuntimeError as e:
        session["flash_err"] = f"Couldn't open that conversation: {e}"
        return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))

    messages = thread.get("messages") or []
    # Same best-effort mark-read as view_message() — only the LAST
    # (auto-expanded) message, matching real Gmail: opening a
    # conversation marks its newest message read, not every message in
    # the history.
    if messages and messages[-1].get("unread"):
        try:
            mark_read(messages[-1]["id"], read=True)
            messages[-1]["unread"] = False
        except RuntimeError:
            pass

    return render_thread(user, thread_id, messages, folder=folder, draft_text=draft_text)


@gmail_bp.route("/thread/<thread_id>/star", methods=["POST"])
@login_required
def star_thread_route(thread_id):
    starred = request.form.get("starred") == "1"
    next_url = _safe_next(request.form.get("next")) or url_for(".inbox")
    try:
        star_thread(thread_id, starred=not starred)
    except RuntimeError as e:
        session["flash_err"] = _scope_err(e, "gmail.modify")
    return redirect(next_url)


@gmail_bp.route("/thread/<thread_id>/delete", methods=["POST"])
@login_required
def delete_thread_route(thread_id):
    next_url = _safe_next(request.form.get("next")) or url_for(".inbox")
    try:
        trash_thread(thread_id)
        session["flash_ok"] = "Conversation moved to Trash."
    except RuntimeError as e:
        session["flash_err"] = _scope_err(e, "gmail.modify")
    return redirect(next_url)


def _safe_content_disposition(filename):
    """Strip characters that could break or inject into the raw
    Content-Disposition header — an attachment's filename comes from
    whoever sent the email, so it's untrusted data reflected into an HTTP
    header, same caution as any other value that ends up there."""
    cleaned = (filename or "attachment").replace("\r", "").replace("\n", "").replace('"', "'")
    return f'attachment; filename="{cleaned}"'


@gmail_bp.route("/message/<message_id>/attachment/<attachment_id>")
@login_required
def download_attachment(message_id, attachment_id):
    """Real download of one real attachment — added 2026-08-27 alongside
    connectors_gmail.get_attachment(), at the user's direct request after
    the Revolut-message screenshot comparison showed 4 real PDF
    attachments Pilant had nothing to show for. Fetches the actual bytes
    from Gmail only on this click, never speculatively (get_message_full's
    attachments list only ever carries metadata — see its docstring).

    CORRECTED 2026-08-27, same day: the first version re-fetched
    get_message_full() and required attachment_id to exactly match an
    entry in its freshly-walked attachments list BEFORE calling
    get_attachment() at all — meant as an extra "can't be spoofed by
    hand-editing the URL" check. Live testing immediately broke on a real
    attachment that plainly existed (confirmed against real Gmail's own
    PDF viewer) — that pre-check was actually redundant, unnecessary
    duplicate authorization logic: Gmail's own messages.attachments.get
    call is ALREADY scoped to the exact (message_id, attachment_id) pair
    by Google's backend — if they don't genuinely belong together,
    Gmail's own API rejects it, which get_attachment()/_get() already
    surfaces as a real, honest RuntimeError. My own second derived
    comparison was just a second chance to be wrong, and it was. Fetching
    the real bytes now happens first, directly, deferring to Gmail as the
    actual authority; the metadata re-fetch below is kept only for a
    human-readable filename/mime type on the download, and is
    deliberately best-effort — it can never block a download Gmail
    itself was willing to serve."""
    folder = (request.args.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None
    try:
        data = get_attachment(message_id, attachment_id)
    except RuntimeError as e:
        session["flash_err"] = f"Couldn't download that attachment: {e}"
        return redirect(url_for(".view_message", message_id=message_id, folder=folder))
    if not data:
        session["flash_err"] = "Couldn't download that attachment: Gmail returned no content for it."
        return redirect(url_for(".view_message", message_id=message_id, folder=folder))

    filename, mime_type = "attachment", "application/octet-stream"
    try:
        msg = get_message_full(message_id)
        meta = next(
            (a for a in (msg.get("attachments") or []) if a.get("attachment_id") == attachment_id),
            None,
        )
        if meta:
            filename = meta.get("filename") or filename
            mime_type = meta.get("mime_type") or mime_type
    except RuntimeError:
        pass  # filename/mime type are cosmetic — never block a real download over this

    resp = Response(data, mimetype=mime_type)
    resp.headers["Content-Disposition"] = _safe_content_disposition(filename)
    return resp


@gmail_bp.route("/message/<message_id>/draft", methods=["POST"])
@login_required
def draft_message(message_id):
    """
    Generates a SUGGESTED reply and stashes it in the session for the next
    GET of this same message — never sends anything, never even shows the
    person anything until the redirect below re-renders the page with the
    draft sitting in the (still fully editable) textarea.

    `thread` form field added 2026-08-27 alongside render_thread(): when
    this draft form is submitted from the conversation view (rather than
    the single-message view), the redirect and the session key the draft
    is stashed under both need to point at the THREAD, not the message —
    otherwise generating a draft from a conversation would silently bounce
    the person to the single-message page and lose the thread context
    they were reading in.

    `next` form field added 2026-08-29 for render_inbox_panel_message()'s
    OWN draft form: when this is submitted from the Studio panel (next
    starts with "/studio"), redirect straight back there instead of
    computing a .view_message/.view_thread URL — same session-stash-then-
    redirect-back-to-the-SAME-GET pattern, just a different destination.
    Checked before the thread_id/folder branches below since panel mode
    only ever applies to a single message, never a thread.
    """
    user = get_user(session["username"])
    instruction = (request.form.get("instruction") or "").strip()
    folder = (request.form.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None
    thread_id = (request.form.get("thread") or "").strip() or None
    next_url = (request.form.get("next") or "").strip()
    panel_mode = next_url.startswith("/studio")
    try:
        msg = get_message_full(message_id)
    except RuntimeError as e:
        session["flash_err"] = f"Couldn't open that message: {e}"
        if panel_mode:
            return redirect(_safe_next(next_url))
        return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))

    draft = draft_reply_text(msg, user["name"], instruction=instruction or None)
    session[f"draft:{thread_id or message_id}"] = draft or ""
    if not draft:
        session["flash_err"] = "Couldn't generate a draft right now — write your own reply below, or try again."
    if panel_mode:
        return redirect(_safe_next(next_url))
    if thread_id:
        return redirect(
            url_for(".view_thread", thread_id=thread_id, folder=folder)
            if folder else url_for(".view_thread", thread_id=thread_id)
        )
    return redirect(
        url_for(".view_message", message_id=message_id, folder=folder)
        if folder else url_for(".view_message", message_id=message_id)
    )


@gmail_bp.route("/message/<message_id>/reply", methods=["POST"])
@login_required
def reply_message(message_id):
    """
    The one route in this file that actually sends anything. `body` is
    whatever is currently in the textarea at submit time — AI-drafted,
    hand-edited, or entirely hand-written; this route has no way to tell
    the difference, and doesn't need to: either way, a person just
    submitted this exact text via a real click, which is the actual
    confirmation step this whole interface is built around.

    `thread` form field added 2026-08-27 alongside render_thread(): when
    replying from the conversation view, an error needs to re-render the
    FULL thread (not just the one message being replied to) so the rest
    of the conversation doesn't disappear out from under the person, and
    a successful send should land back on the conversation — which now
    includes the reply that was just sent — rather than always bouncing
    to the plain inbox list.

    `next` form field added 2026-08-29 for render_inbox_panel_message()'s
    OWN reply form: when submitted from the Studio panel (next starts
    with "/studio"), every outcome — empty body, a real send failure, or
    success — stashes its result in session["flash_ok"/"flash_err"] (the
    SAME keys every other action here already uses) and redirects back to
    that exact panel URL, rather than rendering a full separate
    render_message()/render_thread() page the way the non-panel path
    does. Deliberately stays on the SAME message either way (unlike the
    non-panel success case, which bounces to the inbox list) — an inline
    panel is a worse place to yank someone away from right after they
    just sent something; the confirmation shows in place, and the list is
    one click away via the toolbar's own back arrow.
    """
    user = get_user(session["username"])
    body = request.form.get("body") or ""
    folder = (request.form.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None
    thread_id = (request.form.get("thread") or "").strip() or None
    next_url = (request.form.get("next") or "").strip()
    panel_mode = next_url.startswith("/studio")

    def _thread_error_page(error_text):
        try:
            thread = get_thread_full(thread_id)
        except RuntimeError:
            return None
        return render_thread(user, thread_id, thread.get("messages") or [], folder=folder,
                              draft_text=body, error=error_text)

    if not body.strip():
        if panel_mode:
            session["flash_err"] = "Write something before sending."
            session[f"draft:{message_id}"] = body
            return redirect(_safe_next(next_url))
        if thread_id:
            page = _thread_error_page("Write something before sending.")
            if page is not None:
                return page
        try:
            msg = get_message_full(message_id)
        except RuntimeError as e:
            session["flash_err"] = f"Couldn't open that message: {e}"
            return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))
        return render_message(user, msg, draft_text=body, error="Write something before sending.", folder=folder)

    try:
        result = send_reply(message_id, body)
    except RuntimeError as e:
        err = _scope_err(e, "gmail.send")
        if panel_mode:
            session["flash_err"] = err
            session[f"draft:{message_id}"] = body
            return redirect(_safe_next(next_url))
        if thread_id:
            page = _thread_error_page(err)
            if page is not None:
                return page
        try:
            msg = get_message_full(message_id)
        except RuntimeError:
            session["flash_err"] = err
            return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))
        return render_message(user, msg, draft_text=body, error=err, folder=folder)

    session["flash_ok"] = f"Reply sent to {result['to']}."
    if panel_mode:
        return redirect(_safe_next(next_url))
    if thread_id:
        return redirect(
            url_for(".view_thread", thread_id=thread_id, folder=folder)
            if folder else url_for(".view_thread", thread_id=thread_id)
        )
    return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))


def _safe_next(next_url):
    """`next` comes from a form field a browser fills in from a page this
    server itself rendered — but treat it as untrusted input anyway (a
    crafted POST could send anything) and only ever redirect to a
    same-site relative path, never an absolute/off-site URL, before
    falling back to the plain inbox."""
    next_url = (next_url or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return url_for(".inbox")


@gmail_bp.route("/message/<message_id>/star", methods=["POST"])
@login_required
def star_message_route(message_id):
    """Toggles the star from wherever it was clicked — a list row or the
    message-detail toolbar — and redirects right back there (`next`),
    same one-real-click-does-the-real-thing model as delete/reply."""
    currently_starred = request.form.get("starred") == "1"
    next_url = _safe_next(request.form.get("next"))
    try:
        star_message(message_id, starred=not currently_starred)
    except RuntimeError as e:
        session["flash_err"] = _scope_err(e, "gmail.modify")
    return redirect(next_url)


@gmail_bp.route("/message/<message_id>/delete", methods=["POST"])
@login_required
def delete_message_route(message_id):
    """Moves a message to Trash — real Gmail's actual Delete behavior
    (recoverable, not permanent — see connectors_gmail.trash_message's
    docstring). No confirmation dialog: this mirrors real Gmail, which
    also deletes on a single click and relies on Trash being recoverable
    rather than an "are you sure?" prompt."""
    next_url = _safe_next(request.form.get("next"))
    try:
        trash_message(message_id)
        session["flash_ok"] = "Message moved to Trash."
    except RuntimeError as e:
        session["flash_err"] = _scope_err(e, "gmail.modify")
    return redirect(next_url)


@gmail_bp.route("/message/<message_id>/read", methods=["POST"])
@login_required
def mark_unread_route(message_id):
    """The message-detail toolbar's 'Mark unread' button — the reverse of
    view_message()'s auto-mark-read-on-open. Redirects to the inbox list
    (not back to the message), same as clicking it in real Gmail: marking
    something unread is something you do on your way OUT of a message,
    not a reason to stay on it.

    `next` form field added 2026-08-29 for render_inbox_panel_message()'s
    OWN toolbar — "the inbox list" means /studio's own panel when this
    was clicked from there, not the separate /inbox page. Falls back to
    the original .inbox redirect when `next` isn't sent (the full
    /message/<id> page's own toolbar doesn't send it), so that page's
    behavior is completely unchanged."""
    folder = (request.form.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None
    next_url = (request.form.get("next") or "").strip()
    try:
        mark_read(message_id, read=False)
        session["flash_ok"] = "Marked as unread."
    except RuntimeError as e:
        session["flash_err"] = _scope_err(e, "gmail.modify")
    if next_url.startswith("/studio"):
        return redirect(_safe_next(next_url))
    return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))


@gmail_bp.route("/compose", methods=["GET", "POST"])
@login_required
def compose():
    """The real 'write a new email' flow — separate from Reply/Forward,
    which are both scoped to an existing message. Uses gmail.send, same
    scope reply_message() already needs; no further re-authorization if
    Reply already works. `next`, added 2026-08-26 for the Compose button
    embedded in Studio's chat live-preview (render_inbox_panel), is read
    from either the query string (GET, so the link itself carries it into
    the form) or the POST body (the hidden field render_compose renders
    when next_url was set) — same _safe_next guard as star/delete use, so
    a crafted value can't redirect off-site."""
    user = get_user(session["username"])
    next_url = _safe_next(request.values.get("next")) if request.values.get("next") else None
    if request.method == "GET":
        return render_compose(user, next_url=next_url)

    to = (request.form.get("to") or "").strip()
    cc = (request.form.get("cc") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    body = request.form.get("body") or ""

    if not to:
        return render_compose(user, to=to, cc=cc, subject=subject, body=body,
                               error="Add at least one recipient before sending.", next_url=next_url)

    try:
        result = send_email(to, subject, body, cc=cc or None)
    except RuntimeError as e:
        return render_compose(user, to=to, cc=cc, subject=subject, body=body,
                               error=_scope_err(e, "gmail.send"), next_url=next_url)

    session["flash_ok"] = f"Email sent to {result['to']}."
    return redirect(next_url or url_for(".inbox", folder="sent"))


@gmail_bp.route("/message/<message_id>/forward", methods=["GET", "POST"])
@login_required
def forward_route(message_id):
    """
    Forward gets its own page (render_forward) rather than reusing the
    reply box, because it needs a real To field the reply box has no
    concept of. Deliberately does NOT call connectors_gmail.forward_message()
    on submit — that function re-fetches the original and builds its OWN
    quoted block, which would double the quote on top of the one already
    sitting in the (edited-by-a-person) textarea below. Instead this sends
    exactly what's in the textarea at submit time via send_email(), same
    "whatever's in the box when you click the real button is what goes
    out" principle reply_message() already uses — forward_message() stays
    available in connectors_gmail.py for any future non-interactive caller
    that wants the auto-quoting behavior directly.
    """
    user = get_user(session["username"])
    folder = (request.args.get("folder") or request.form.get("folder") or "").strip().lower() or None
    if folder is not None and folder not in FOLDERS:
        folder = None

    try:
        msg = get_message_full(message_id)
    except RuntimeError as e:
        session["flash_err"] = f"Couldn't open that message: {e}"
        return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))

    if request.method == "GET":
        quoted = (
            "---------- Forwarded message ---------\n"
            f"From: {msg.get('from') or ''}\n"
            f"Date: {msg.get('date') or ''}\n"
            f"Subject: {msg.get('subject') or ''}\n"
            f"To: {msg.get('to') or ''}\n\n"
            f"{msg.get('body') or ''}"
        )
        return render_forward(user, msg, body=quoted, folder=folder)

    to = (request.form.get("to") or "").strip()
    body = request.form.get("body") or ""
    if not to:
        return render_forward(user, msg, to=to, body=body, folder=folder,
                               error="Add a recipient before forwarding.")

    subject = msg.get("subject") or "(no subject)"
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"

    try:
        result = send_email(to, subject, body)
    except RuntimeError as e:
        return render_forward(user, msg, to=to, body=body, folder=folder,
                               error=_scope_err(e, "gmail.send"))

    session["flash_ok"] = f"Forwarded to {result['to']}."
    return redirect(url_for(".inbox", folder=folder) if folder else url_for(".inbox"))
