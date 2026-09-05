"""
STRYDE — a brand-new, standalone PUBLIC storefront (own Flask app, own
port, own visual identity), built at the user's request for "a proper gym
[shark-style] website for public users ... crowded ... discounts, offers
... want all in the things in gym shark."

Not Gymshark. Gymshark is a real company; using its name/logo/branding
would be impersonating a real business, which is refused regardless of
framing ("it's just a demo" included) — confirmed directly with the user
via clarifying questions before writing a line of this file. STRYDE is a
fictional brand invented for this project: same bold, promo-heavy,
athletic-wear energy (dark palette, a loud accent color, sitewide discount
banners, sale badges), but its own name and identity, not modeled on any
one real company beyond the general "DTC athletic-apparel storefront"
genre real customers see across the category.

Scope, confirmed with the user: a full public storefront — homepage,
shoppable catalog with categories, product detail pages, a working session
cart with a real promo-code discount, a checkout flow, and shopper
accounts with order history. Explicitly NO real payment processing (the
user agreed this needs a real payment provider, which can't be fabricated
here) — checkout collects a shipping address and shows an honest "demo
store, nothing is charged" notice rather than rendering card-number/CVV
fields that would look like real payment collection with nothing behind
them (that's a bright line this project doesn't cross even for a demo —
see the top-level safety rules on forms that collect payment details under
false pretenses).

Deliberately its own standalone Flask app (own port, own session cookie
via its own SECRET_KEY, own shopper login separate from Pilant Studio's
staff login in users.py) rather than folded into studio.py — the user
confirmed this is "just a storefront, no live Pilant connector yet," so
there's nothing here that needs Studio's chat/agent machinery. If a real
Pilant-powered connector (e.g. a support chat widget backed by real order
data) gets built later, THIS is the app it would attach to.

Same "plain string-built HTML, no template engine" convention every other
standalone site in this project uses (retail_site.py, gmail_site.py,
healthcare_dashboard.py, ...) — no new dependency, same style throughout.
"""

import html as _html
import os
import secrets
import uuid
from datetime import datetime

from flask import Flask, request, session, redirect, url_for
from werkzeug.security import generate_password_hash, check_password_hash

from storefront_data import (
    BRAND_NAME, BRAND_TAGLINE, CATEGORIES, PRODUCTS, DISCOUNT_CODES,
    FREE_SHIPPING_THRESHOLD, get_product, get_products_by_category,
    get_bestsellers, get_new_arrivals, get_sale_items,
)

app = Flask(__name__)
app.secret_key = os.environ.get("STOREFRONT_SECRET_KEY") or secrets.token_hex(32)
PORT = 5009


def _esc(s):
    return _html.escape(str(s)) if s is not None else ""


def _money(n):
    return f"${n:,.2f}" if isinstance(n, float) else f"${n:,}"


# ---- Shopper accounts (in-memory — a demo store, same "plain in-memory
# dict, no real database" pattern users.py uses for Studio's own staff
# logins) -----------------------------------------------------------------
SHOPPERS = {}   # email -> {"name": str, "password_hash": str}
ORDERS = {}     # email -> [order dict, ...], newest first


def _current_shopper():
    email = session.get("shopper_email")
    return SHOPPERS.get(email) if email else None


def _require_shopper():
    """Returns the logged-in shopper's email, or None — callers redirect to
    /login themselves rather than this raising, since a Flask route needs
    to return a real redirect Response, not have one thrown from a helper."""
    return session.get("shopper_email") if session.get("shopper_email") in SHOPPERS else None


# ---- Cart (Flask session — {product_id: {"qty": int, "size": str, "color": str}}) ----

def _cart_dict():
    return session.setdefault("cart", {})


def _cart_items():
    """Resolves the session's raw cart dict into full line items (real
    product data + qty + size/color + computed line total), skipping any
    id that no longer matches a real product (e.g. the catalog changed) —
    never fabricates a product to fill a gap."""
    items = []
    for pid, entry in _cart_dict().items():
        product = get_product(pid)
        if not product:
            continue
        qty = max(1, int(entry.get("qty", 1)))
        unit_price = product["sale_price"] or product["price"]
        items.append({
            "product": product,
            "qty": qty,
            "size": entry.get("size") or (product["sizes"][0] if product["sizes"] else ""),
            "color": entry.get("color") or (product["colors"][0] if product["colors"] else ""),
            "unit_price": unit_price,
            "line_total": unit_price * qty,
        })
    return items


def _cart_count():
    return sum(e.get("qty", 1) for e in _cart_dict().values())


def _cart_totals(items, promo_code=None):
    subtotal = sum(i["line_total"] for i in items)
    discount = 0
    promo_label = None
    if promo_code:
        code = DISCOUNT_CODES.get(promo_code.strip().upper())
        if code:
            discount = round(subtotal * code["percent_off"] / 100, 2)
            promo_label = f'{promo_code.strip().upper()} — {code["description"]}'
    shipping = 0 if (subtotal - discount) >= FREE_SHIPPING_THRESHOLD or subtotal == 0 else 6.99
    total = max(0, subtotal - discount) + shipping
    return {
        "subtotal": subtotal, "discount": discount, "promo_label": promo_label,
        "shipping": shipping, "total": total,
    }


# ---- Shared page shell ----------------------------------------------------

CSS = """
:root {
  --ink:#0E0F0C; --paper:#FAFAF7; --card:#FFFFFF; --line:#E4E4DE;
  --muted:#6B6B62; --volt:#C6FF3D; --volt-ink:#233300; --sale:#D3352A;
  --sale-bg:#FCEAE8; --good:#1E7A4C; --shadow:0 10px 30px rgba(14,15,12,.08);
}
* { box-sizing:border-box; }
body { margin:0; background:var(--paper); color:var(--ink); font-family:'Inter',system-ui,sans-serif; -webkit-font-smoothing:antialiased; }
h1,h2,h3 { font-family:'Space Grotesk',sans-serif; letter-spacing:-.01em; margin:0; }
a { color:inherit; }
img,svg { display:block; }
.wrap { max-width:1180px; margin:0 auto; padding:0 24px; }

.promo-bar { background:var(--ink); color:var(--volt); text-align:center; font-size:.8rem; font-weight:600; letter-spacing:.02em; padding:9px 16px; }
.promo-bar strong { color:#fff; }

.nav { display:flex; align-items:center; justify-content:space-between; padding:18px 0; border-bottom:1px solid var(--line); }
.nav-logo { font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:1.4rem; letter-spacing:-.02em; text-decoration:none; }
.nav-links { display:flex; gap:26px; list-style:none; margin:0; padding:0; }
.nav-links a { font-size:.88rem; font-weight:600; text-decoration:none; color:var(--ink); }
.nav-links a:hover { color:var(--good); text-decoration:underline; }
.nav-right { display:flex; align-items:center; gap:18px; }
.nav-icon-link { font-size:.85rem; font-weight:600; text-decoration:none; color:var(--ink); display:flex; align-items:center; gap:6px; }
.cart-count { background:var(--volt); color:var(--volt-ink); border-radius:999px; font-size:.72rem; font-weight:700; padding:1px 7px; }

.hero { background:var(--ink); color:#fff; padding:76px 0 88px; }
.hero-inner { display:flex; align-items:center; gap:48px; flex-wrap:wrap; }
.hero-copy { flex:1; min-width:280px; }
.hero-eyebrow { color:var(--volt); font-weight:700; font-size:.8rem; letter-spacing:.08em; text-transform:uppercase; margin-bottom:14px; }
.hero-title { font-size:3.2rem; line-height:1.02; margin-bottom:18px; }
.hero-sub { color:#C7C7BE; font-size:1.05rem; max-width:440px; margin-bottom:28px; line-height:1.55; }
.hero-visual { flex:1; min-width:260px; aspect-ratio:1/1; max-width:420px; border-radius:20px; background:linear-gradient(135deg,#20241a,#3a4230); display:flex; align-items:center; justify-content:center; font-size:6rem; }

.btn { display:inline-flex; align-items:center; gap:8px; background:var(--volt); color:var(--volt-ink); font-weight:700; font-size:.9rem; padding:14px 26px; border-radius:999px; text-decoration:none; border:none; cursor:pointer; font-family:inherit; }
.btn:hover { opacity:.9; }
.btn-outline { background:transparent; color:#fff; border:1.5px solid rgba(255,255,255,.5); }
.btn-outline:hover { border-color:#fff; opacity:1; }
.btn-dark { background:var(--ink); color:#fff; }
.btn-block { width:100%; justify-content:center; }
.btn-sm { padding:9px 16px; font-size:.8rem; }

.section { padding:56px 0; }
.section-head { display:flex; justify-content:space-between; align-items:flex-end; margin-bottom:26px; gap:16px; flex-wrap:wrap; }
.section-title { font-size:1.6rem; }
.section-link { font-size:.85rem; font-weight:600; text-decoration:none; color:var(--good); white-space:nowrap; }
.section-link:hover { text-decoration:underline; }

.cat-grid { display:grid; grid-template-columns:repeat(6,1fr); gap:14px; }
.cat-tile { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:22px 14px; text-align:center; text-decoration:none; color:var(--ink); transition:box-shadow .15s, transform .15s; }
.cat-tile:hover { box-shadow:var(--shadow); transform:translateY(-2px); }
.cat-tile-icon { font-size:1.8rem; margin-bottom:8px; }
.cat-tile-label { font-size:.8rem; font-weight:700; }

.grid { display:grid; grid-template-columns:repeat(4,1fr); gap:20px; }
.pcard { background:var(--card); border:1px solid var(--line); border-radius:16px; overflow:hidden; text-decoration:none; color:var(--ink); display:flex; flex-direction:column; transition:box-shadow .15s, transform .15s; }
.pcard:hover { box-shadow:var(--shadow); transform:translateY(-3px); }
.pcard-swatch { aspect-ratio:1/1; display:flex; align-items:center; justify-content:center; font-size:3.4rem; position:relative; }
.pcard-badge { position:absolute; top:10px; left:10px; background:var(--ink); color:#fff; font-size:.66rem; font-weight:700; letter-spacing:.03em; text-transform:uppercase; padding:4px 10px; border-radius:999px; }
.pcard-badge.sale { background:var(--sale); }
.pcard-body { padding:14px 16px 18px; flex:1; display:flex; flex-direction:column; gap:5px; }
.pcard-cat { font-size:.68rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); }
.pcard-name { font-size:.92rem; font-weight:600; line-height:1.3; }
.pcard-rating { font-size:.75rem; color:var(--muted); }
.pcard-price { margin-top:auto; padding-top:6px; display:flex; align-items:baseline; gap:8px; }
.price-now { font-weight:700; font-size:1rem; }
.price-was { font-size:.82rem; color:var(--muted); text-decoration:line-through; }
.price-only { font-weight:700; font-size:1rem; }

.promo-strip { background:var(--volt); color:var(--volt-ink); padding:34px 0; }
.promo-strip-inner { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:16px; }
.promo-strip h3 { font-size:1.4rem; }
.promo-strip p { margin:4px 0 0; font-size:.9rem; font-weight:600; }
.promo-code { background:var(--volt-ink); color:var(--volt); font-family:'Space Grotesk',monospace; font-weight:700; padding:3px 10px; border-radius:6px; }

.filter-bar { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:26px; }
.filter-chip { border:1px solid var(--line); background:var(--card); border-radius:999px; padding:8px 16px; font-size:.82rem; font-weight:600; text-decoration:none; color:var(--ink); }
.filter-chip.active { background:var(--ink); color:#fff; border-color:var(--ink); }
.filter-chip:hover { border-color:var(--ink); }

.pd-layout { display:flex; gap:48px; flex-wrap:wrap; padding:48px 0; }
.pd-visual { flex:1; min-width:320px; aspect-ratio:1/1; max-width:480px; border-radius:20px; display:flex; align-items:center; justify-content:center; font-size:8rem; }
.pd-info { flex:1; min-width:300px; }
.pd-cat { font-size:.75rem; font-weight:700; letter-spacing:.04em; text-transform:uppercase; color:var(--muted); margin-bottom:6px; }
.pd-name { font-size:1.9rem; margin-bottom:10px; }
.pd-rating { font-size:.85rem; color:var(--muted); margin-bottom:16px; }
.pd-price { display:flex; align-items:baseline; gap:10px; margin-bottom:18px; }
.pd-price .price-now { font-size:1.5rem; }
.pd-price .price-was { font-size:1.05rem; }
.pd-desc { color:#3A3A34; line-height:1.6; margin-bottom:26px; }
.pd-field { margin-bottom:20px; }
.pd-field-label { font-size:.78rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; margin-bottom:8px; display:block; }
.swatch-row { display:flex; gap:8px; flex-wrap:wrap; }
.swatch-opt { border:1.5px solid var(--line); background:var(--card); border-radius:8px; padding:9px 14px; font-size:.82rem; font-weight:600; cursor:pointer; }
.swatch-opt input { display:none; }
.swatch-opt:has(input:checked) { border-color:var(--ink); background:var(--ink); color:#fff; }

.demo-note { background:#F2F2EC; border:1px solid var(--line); border-radius:10px; padding:10px 14px; font-size:.78rem; color:var(--muted); margin:18px 0; }

table.cart-table { width:100%; border-collapse:collapse; margin-bottom:24px; }
table.cart-table th { text-align:left; font-size:.72rem; text-transform:uppercase; letter-spacing:.03em; color:var(--muted); padding:0 0 10px; border-bottom:1px solid var(--line); }
table.cart-table td { padding:16px 0; border-bottom:1px solid var(--line); vertical-align:middle; }
.cart-prod { display:flex; align-items:center; gap:14px; }
.cart-swatch { width:64px; height:64px; border-radius:10px; display:flex; align-items:center; justify-content:center; font-size:1.6rem; flex:none; }
.cart-prod-name { font-weight:600; font-size:.9rem; }
.cart-prod-meta { font-size:.78rem; color:var(--muted); }
.qty-form { display:flex; align-items:center; gap:6px; }
.qty-form input[type=number] { width:52px; padding:6px; border:1px solid var(--line); border-radius:6px; font-family:inherit; }
.qty-form button, .remove-btn { background:none; border:1px solid var(--line); border-radius:6px; padding:6px 10px; cursor:pointer; font-family:inherit; font-size:.78rem; }
.remove-btn { color:var(--sale); border-color:var(--sale-bg); }

.summary-card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:24px; }
.summary-row { display:flex; justify-content:space-between; font-size:.88rem; padding:7px 0; }
.summary-row.total { font-weight:700; font-size:1.05rem; border-top:1px solid var(--line); margin-top:8px; padding-top:14px; }
.promo-form { display:flex; gap:8px; margin:16px 0; }
.promo-form input[type=text] { flex:1; padding:10px 12px; border:1px solid var(--line); border-radius:8px; font-family:inherit; text-transform:uppercase; }
.promo-msg { font-size:.78rem; margin:-8px 0 14px; }
.promo-msg.ok { color:var(--good); }
.promo-msg.err { color:var(--sale); }

.empty-state { text-align:center; padding:80px 20px; color:var(--muted); }
.empty-state .btn { margin-top:18px; }

.form-grid { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.form-grid .full { grid-column:1/-1; }
.form-field label { display:block; font-size:.78rem; font-weight:700; margin-bottom:6px; }
.form-field input, .form-field select { width:100%; padding:11px 12px; border:1px solid var(--line); border-radius:8px; font-family:inherit; font-size:.88rem; }

.checkout-layout { display:flex; gap:40px; padding:44px 0; flex-wrap:wrap; align-items:flex-start; }
.checkout-main { flex:1.4; min-width:340px; }
.checkout-side { flex:1; min-width:300px; }
.mini-line { display:flex; justify-content:space-between; font-size:.82rem; padding:6px 0; }

.auth-wrap { max-width:400px; margin:60px auto; padding:0 24px; }
.auth-card { background:var(--card); border:1px solid var(--line); border-radius:16px; padding:32px; }
.auth-card h1 { font-size:1.5rem; margin-bottom:6px; }
.auth-card p.sub { color:var(--muted); font-size:.85rem; margin-bottom:22px; }
.auth-card form { display:flex; flex-direction:column; gap:14px; }
.auth-card input { padding:11px 12px; border:1px solid var(--line); border-radius:8px; font-family:inherit; font-size:.9rem; }
.auth-switch { text-align:center; font-size:.82rem; color:var(--muted); margin-top:16px; }
.auth-switch a { color:var(--good); font-weight:600; text-decoration:none; }
.flash-err { background:var(--sale-bg); color:var(--sale); font-size:.8rem; padding:10px 12px; border-radius:8px; }
.flash-ok { background:#E7F5EC; color:var(--good); font-size:.8rem; padding:10px 12px; border-radius:8px; }

.order-card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:20px 22px; margin-bottom:14px; }
.order-head { display:flex; justify-content:space-between; font-size:.85rem; margin-bottom:10px; flex-wrap:wrap; gap:6px; }
.order-head strong { font-family:'Space Grotesk',monospace; }
.order-items { font-size:.82rem; color:var(--muted); }

.stars { color:#E0A100; }

.footer { background:var(--ink); color:#C7C7BE; padding:48px 0 28px; margin-top:60px; }
.footer-grid { display:flex; justify-content:space-between; flex-wrap:wrap; gap:32px; margin-bottom:32px; }
.footer-brand { font-family:'Space Grotesk',sans-serif; font-weight:700; font-size:1.3rem; color:#fff; }
.footer-tag { font-size:.82rem; margin-top:6px; max-width:260px; line-height:1.5; }
.footer-col h4 { font-size:.76rem; text-transform:uppercase; letter-spacing:.04em; color:#8C8C82; margin-bottom:12px; }
.footer-col a { display:block; font-size:.84rem; color:#C7C7BE; text-decoration:none; margin-bottom:8px; }
.footer-col a:hover { color:var(--volt); }
.newsletter-form { display:flex; gap:8px; max-width:320px; }
.newsletter-form input { flex:1; padding:10px 12px; border-radius:8px; border:none; font-family:inherit; }
.footer-bottom { border-top:1px solid #2A2B24; padding-top:20px; font-size:.75rem; color:#8C8C82; display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; }

@media (max-width: 900px) {
  .cat-grid { grid-template-columns:repeat(3,1fr); }
  .grid { grid-template-columns:repeat(2,1fr); }
  .hero-title { font-size:2.2rem; }
  .form-grid { grid-template-columns:1fr; }
}
"""

PAGE_SHELL = (
    "<!doctype html><html><head><meta charset=\"utf-8\">"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
    "<title>{{TITLE}}</title>"
    "<link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">"
    "<link rel=\"stylesheet\" href=\"https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600;700&display=swap\">"
    "<style>{{CSS}}</style></head><body>"
    "{{BODY}}"
    "</body></html>"
)


def _nav_html(shopper):
    links = "".join(
        f'<li><a href="/shop/{_esc(c["slug"])}">{_esc(c["label"])}</a></li>' for c in CATEGORIES[:5]
    )
    cart_n = _cart_count()
    cart_badge = f'<span class="cart-count">{cart_n}</span>' if cart_n else ""
    account_link = (
        f'<a class="nav-icon-link" href="/account">👤 {_esc(shopper["name"].split()[0])}</a>'
        if shopper else '<a class="nav-icon-link" href="/login">👤 Log in</a>'
    )
    return (
        '<div class="wrap nav">'
        f'<a class="nav-logo" href="/">{_esc(BRAND_NAME)}</a>'
        f'<ul class="nav-links">{links}<li><a href="/shop?sale=1">Sale</a></li></ul>'
        '<div class="nav-right">'
        f'{account_link}'
        f'<a class="nav-icon-link" href="/cart">🛒 Cart{cart_badge}</a>'
        '</div></div>'
    )


def _footer_html():
    return (
        '<footer class="footer"><div class="wrap">'
        '<div class="footer-grid">'
        '<div>'
        f'<p class="footer-brand">{_esc(BRAND_NAME)}</p>'
        f'<p class="footer-tag">{_esc(BRAND_TAGLINE)} A demo storefront built to show what a full public shopping '
        'experience looks like end to end — browsing, discounts, cart, and checkout.</p>'
        '</div>'
        '<div class="footer-col"><h4>Shop</h4>'
        + "".join(f'<a href="/shop/{_esc(c["slug"])}">{_esc(c["label"])}</a>' for c in CATEGORIES)
        + '</div>'
        '<div class="footer-col"><h4>Get 10% off</h4>'
        '<form class="newsletter-form" method="post" action="/newsletter">'
        '<input type="email" name="email" placeholder="you@email.com" required>'
        '<button class="btn btn-sm" type="submit">Join</button>'
        '</form></div>'
        '</div>'
        f'<div class="footer-bottom"><span>&copy; {datetime.now().year} {_esc(BRAND_NAME)} — a fictional demo brand.</span>'
        '<span>Not a real store · no real payments are processed</span></div>'
        '</div></footer>'
    )


def _page(title, body_inner):
    shopper = _current_shopper()
    body = f'{_nav_html(shopper)}{body_inner}{_footer_html()}'
    out = PAGE_SHELL
    out = out.replace("{{TITLE}}", f"{_esc(title)} — {_esc(BRAND_NAME)}")
    out = out.replace("{{CSS}}", CSS)
    out = out.replace("{{BODY}}", body)
    return out


# ---- Product card / grid rendering ----------------------------------------

def _product_card_html(p):
    badge_html = ""
    if p["sale_price"]:
        badge_html = '<span class="pcard-badge sale">Sale</span>'
    elif p["badge"]:
        badge_html = f'<span class="pcard-badge">{_esc(p["badge"])}</span>'
    if p["sale_price"]:
        price_html = f'<span class="price-now">{_money(p["sale_price"])}</span><span class="price-was">{_money(p["price"])}</span>'
    else:
        price_html = f'<span class="price-only">{_money(p["price"])}</span>'
    return (
        f'<a class="pcard" href="/product/{_esc(p["id"])}">'
        f'<div class="pcard-swatch" style="background:{p["swatch"]["bg"]}22">{badge_html}'
        f'<span>{p["swatch"]["icon"]}</span></div>'
        '<div class="pcard-body">'
        f'<span class="pcard-cat">{_esc(next((c["label"] for c in CATEGORIES if c["slug"] == p["category"]), ""))}</span>'
        f'<span class="pcard-name">{_esc(p["name"])}</span>'
        f'<span class="pcard-rating">★ {p["rating"]} ({p["review_count"]:,})</span>'
        f'<div class="pcard-price">{price_html}</div>'
        '</div></a>'
    )


def _grid_html(products):
    if not products:
        return '<div class="empty-state"><p>No products match this filter yet.</p></div>'
    return f'<div class="grid">{"".join(_product_card_html(p) for p in products)}</div>'


# ---- Routes: homepage -------------------------------------------------------

@app.route("/")
def home():
    hero = (
        '<div class="hero"><div class="wrap hero-inner">'
        '<div class="hero-copy">'
        '<p class="hero-eyebrow">New season drop</p>'
        f'<h1 class="hero-title">Train harder.<br>Recover louder.</h1>'
        '<p class="hero-sub">Squat-proof leggings, sweat-proof sports bras, and everything else '
        'built for the days you actually show up.</p>'
        '<a class="btn" href="/shop">Shop the collection →</a>'
        ' <a class="btn btn-outline" href="/shop?sale=1" style="margin-left:10px;">Shop sale</a>'
        '</div>'
        '<div class="hero-visual">🏋️</div>'
        '</div></div>'
    )
    promo_strip = (
        '<div class="promo-strip"><div class="wrap promo-strip-inner">'
        '<div><h3>Take 20% off your first order</h3><p>Use code <span class="promo-code">SWEAT20</span> at checkout · free shipping over $75</p></div>'
        '<a class="btn btn-dark" href="/shop">Start shopping</a>'
        '</div></div>'
    )
    cats = "".join(
        f'<a class="cat-tile" href="/shop/{_esc(c["slug"])}">'
        f'<div class="cat-tile-icon">{c["icon"]}</div><div class="cat-tile-label">{_esc(c["label"])}</div></a>'
        for c in CATEGORIES
    )
    bestsellers = _grid_html(get_bestsellers(4))
    new_arrivals = _grid_html(get_new_arrivals(4))
    sale_items = _grid_html(get_sale_items(4))
    body = (
        f'{hero}{promo_strip}'
        '<div class="wrap section"><div class="section-head"><h2 class="section-title">Shop by category</h2></div>'
        f'<div class="cat-grid">{cats}</div></div>'
        '<div class="wrap section"><div class="section-head"><h2 class="section-title">Bestsellers</h2>'
        '<a class="section-link" href="/shop">View all →</a></div>'
        f'{bestsellers}</div>'
        '<div class="wrap section"><div class="section-head"><h2 class="section-title">On sale right now</h2>'
        '<a class="section-link" href="/shop?sale=1">View all sale →</a></div>'
        f'{sale_items}</div>'
        '<div class="wrap section"><div class="section-head"><h2 class="section-title">New arrivals</h2>'
        '<a class="section-link" href="/shop">View all →</a></div>'
        f'{new_arrivals}</div>'
    )
    return _page("Home", body)


# ---- Routes: shop / catalog --------------------------------------------------

@app.route("/shop")
@app.route("/shop/<category>")
def shop(category=None):
    sale_only = request.args.get("sale") == "1"
    products = get_products_by_category(category)
    if sale_only:
        products = [p for p in products if p["sale_price"]]
    chips = ['<a class="filter-chip%s" href="/shop">All</a>' % (" active" if not category else "")]
    for c in CATEGORIES:
        active = " active" if category == c["slug"] else ""
        chips.append(f'<a class="filter-chip{active}" href="/shop/{_esc(c["slug"])}">{_esc(c["label"])}</a>')
    sale_active = " active" if sale_only else ""
    chips.append(f'<a class="filter-chip{sale_active}" href="/shop?sale=1">🔥 Sale</a>')
    title = next((c["label"] for c in CATEGORIES if c["slug"] == category), "Shop All")
    body = (
        '<div class="wrap section">'
        f'<div class="section-head"><h2 class="section-title">{_esc(title)}</h2>'
        f'<span class="pcard-rating">{len(products)} product{"s" if len(products) != 1 else ""}</span></div>'
        f'<div class="filter-bar">{"".join(chips)}</div>'
        f'{_grid_html(products)}'
        '</div>'
    )
    return _page(title, body)


@app.route("/product/<product_id>")
def product_detail(product_id):
    p = get_product(product_id)
    if not p:
        return _page("Not found", '<div class="wrap section"><div class="empty-state"><p>That product doesn\'t exist.</p><a class="btn" href="/shop">Back to shop</a></div></div>'), 404

    size_opts = "".join(
        f'<label class="swatch-opt"><input type="radio" name="size" value="{_esc(s)}"{" checked" if i == 0 else ""}>{_esc(s)}</label>'
        for i, s in enumerate(p["sizes"])
    )
    color_opts = "".join(
        f'<label class="swatch-opt"><input type="radio" name="color" value="{_esc(c)}"{" checked" if i == 0 else ""}>{_esc(c)}</label>'
        for i, c in enumerate(p["colors"])
    )
    if p["sale_price"]:
        price_html = f'<span class="price-now">{_money(p["sale_price"])}</span><span class="price-was">{_money(p["price"])}</span>'
    else:
        price_html = f'<span class="price-only">{_money(p["price"])}</span>'
    cat_label = next((c["label"] for c in CATEGORIES if c["slug"] == p["category"]), "")
    body = (
        '<div class="wrap pd-layout">'
        f'<div class="pd-visual" style="background:{p["swatch"]["bg"]}22">{p["swatch"]["icon"]}</div>'
        '<div class="pd-info">'
        f'<p class="pd-cat">{_esc(cat_label)}</p>'
        f'<h1 class="pd-name">{_esc(p["name"])}</h1>'
        f'<p class="pd-rating">★★★★★ {p["rating"]} · {p["review_count"]:,} reviews</p>'
        f'<div class="pd-price">{price_html}</div>'
        f'<p class="pd-desc">{_esc(p["description"])}</p>'
        f'<form method="post" action="/cart/add">'
        f'<input type="hidden" name="product_id" value="{_esc(p["id"])}">'
        f'<div class="pd-field"><span class="pd-field-label">Size</span><div class="swatch-row">{size_opts}</div></div>'
        f'<div class="pd-field"><span class="pd-field-label">Color</span><div class="swatch-row">{color_opts}</div></div>'
        '<button class="btn btn-block" type="submit">Add to cart — ' + _money(p["sale_price"] or p["price"]) + '</button>'
        '</form>'
        '</div></div>'
    )
    return _page(p["name"], body)


# ---- Routes: cart -----------------------------------------------------------

@app.route("/cart/add", methods=["POST"])
def cart_add():
    product_id = request.form.get("product_id")
    if get_product(product_id):
        cart = _cart_dict()
        entry = cart.get(product_id, {"qty": 0})
        entry["qty"] = entry.get("qty", 0) + 1
        entry["size"] = request.form.get("size") or entry.get("size")
        entry["color"] = request.form.get("color") or entry.get("color")
        cart[product_id] = entry
        session.modified = True
    return redirect("/cart")


@app.route("/cart/update/<product_id>", methods=["POST"])
def cart_update(product_id):
    cart = _cart_dict()
    if product_id in cart:
        try:
            qty = int(request.form.get("qty", 1))
        except ValueError:
            qty = 1
        if qty <= 0:
            cart.pop(product_id, None)
        else:
            cart[product_id]["qty"] = min(qty, 20)
        session.modified = True
    return redirect("/cart")


@app.route("/cart/remove/<product_id>", methods=["POST"])
def cart_remove(product_id):
    _cart_dict().pop(product_id, None)
    session.modified = True
    return redirect("/cart")


@app.route("/cart/promo", methods=["POST"])
def cart_promo():
    session["promo_code"] = request.form.get("promo_code", "").strip().upper() or None
    return redirect("/cart")


@app.route("/cart")
def cart_view():
    items = _cart_items()
    promo_code = session.get("promo_code")
    totals = _cart_totals(items, promo_code)

    if not items:
        body = (
            '<div class="wrap section"><div class="empty-state">'
            '<h2 class="section-title">Your cart is empty</h2>'
            '<p>Nothing here yet — go find something worth training in.</p>'
            '<a class="btn" href="/shop">Shop now</a>'
            '</div></div>'
        )
        return _page("Your cart", body)

    rows = []
    for it in items:
        p = it["product"]
        rows.append(
            '<tr><td><div class="cart-prod">'
            f'<div class="cart-swatch" style="background:{p["swatch"]["bg"]}22">{p["swatch"]["icon"]}</div>'
            f'<div><div class="cart-prod-name">{_esc(p["name"])}</div>'
            f'<div class="cart-prod-meta">{_esc(it["size"])} · {_esc(it["color"])}</div></div>'
            '</div></td>'
            f'<td>{_money(it["unit_price"])}</td>'
            f'<td><form class="qty-form" method="post" action="/cart/update/{_esc(p["id"])}">'
            f'<input type="number" name="qty" min="1" max="20" value="{it["qty"]}">'
            '<button type="submit">Update</button></form></td>'
            f'<td>{_money(it["line_total"])}</td>'
            f'<td><form method="post" action="/cart/remove/{_esc(p["id"])}">'
            '<button class="remove-btn" type="submit">Remove</button></form></td>'
            '</tr>'
        )
    promo_msg = ""
    if promo_code:
        if totals["promo_label"]:
            promo_msg = f'<p class="promo-msg ok">✓ {_esc(totals["promo_label"])} applied</p>'
        else:
            promo_msg = f'<p class="promo-msg err">"{_esc(promo_code)}" isn\'t a valid code</p>'
    shipping_line = "Free" if totals["shipping"] == 0 else _money(totals["shipping"])
    body = (
        '<div class="wrap section">'
        '<h1 class="section-title" style="margin-bottom:24px;">Your cart</h1>'
        '<div class="checkout-layout">'
        '<div class="checkout-main">'
        '<table class="cart-table"><thead><tr><th>Product</th><th>Price</th><th>Qty</th><th>Total</th><th></th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        '</div>'
        '<div class="checkout-side"><div class="summary-card">'
        '<form class="promo-form" method="post" action="/cart/promo">'
        f'<input type="text" name="promo_code" placeholder="Promo code" value="{_esc(promo_code or "")}">'
        '<button class="btn btn-sm" type="submit">Apply</button></form>'
        f'{promo_msg}'
        f'<div class="summary-row"><span>Subtotal</span><span>{_money(totals["subtotal"])}</span></div>'
        + (f'<div class="summary-row"><span>Discount</span><span>-{_money(totals["discount"])}</span></div>' if totals["discount"] else "")
        + f'<div class="summary-row"><span>Shipping</span><span>{shipping_line}</span></div>'
        f'<div class="summary-row total"><span>Total</span><span>{_money(totals["total"])}</span></div>'
        '<a class="btn btn-block" href="/checkout" style="margin-top:14px;">Checkout →</a>'
        '</div></div>'
        '</div></div>'
    )
    return _page("Your cart", body)


# ---- Routes: checkout (demo — no real payment) ------------------------------

@app.route("/checkout", methods=["GET"])
def checkout_view():
    items = _cart_items()
    if not items:
        return redirect("/cart")
    totals = _cart_totals(items, session.get("promo_code"))
    shopper = _current_shopper()
    lines = "".join(
        f'<div class="mini-line"><span>{it["qty"]}× {_esc(it["product"]["name"])}</span><span>{_money(it["line_total"])}</span></div>'
        for it in items
    )
    shipping_line = "Free" if totals["shipping"] == 0 else _money(totals["shipping"])
    body = (
        '<div class="wrap checkout-layout">'
        '<div class="checkout-main">'
        '<h1 class="section-title" style="margin-bottom:18px;">Checkout</h1>'
        '<div class="demo-note">🔒 This is a demo store — no real payment is collected or processed. '
        'Placing an order just saves it to your demo account.</div>'
        '<form method="post" action="/checkout">'
        '<div class="form-grid">'
        f'<div class="form-field full"><label>Full name</label><input type="text" name="full_name" value="{_esc(shopper["name"] if shopper else "")}" required></div>'
        f'<div class="form-field full"><label>Email</label><input type="email" name="email" value="{_esc(session.get("shopper_email") or "")}" required></div>'
        '<div class="form-field full"><label>Street address</label><input type="text" name="address" required></div>'
        '<div class="form-field"><label>City</label><input type="text" name="city" required></div>'
        '<div class="form-field"><label>ZIP / postal code</label><input type="text" name="zip" required></div>'
        '</div>'
        '<button class="btn btn-block" type="submit" style="margin-top:22px;">Place demo order — ' + _money(totals["total"]) + '</button>'
        '</form>'
        '</div>'
        '<div class="checkout-side"><div class="summary-card">'
        '<h3 style="font-size:1rem;margin-bottom:12px;">Order summary</h3>'
        f'{lines}'
        f'<div class="summary-row"><span>Subtotal</span><span>{_money(totals["subtotal"])}</span></div>'
        + (f'<div class="summary-row"><span>Discount</span><span>-{_money(totals["discount"])}</span></div>' if totals["discount"] else "")
        + f'<div class="summary-row"><span>Shipping</span><span>{shipping_line}</span></div>'
        f'<div class="summary-row total"><span>Total</span><span>{_money(totals["total"])}</span></div>'
        '</div></div>'
        '</div>'
    )
    return _page("Checkout", body)


@app.route("/checkout", methods=["POST"])
def checkout_submit():
    items = _cart_items()
    if not items:
        return redirect("/cart")
    totals = _cart_totals(items, session.get("promo_code"))
    order_id = f"STR-{uuid.uuid4().hex[:8].upper()}"
    order = {
        "id": order_id,
        "placed_at": datetime.now().strftime("%b %d, %Y %H:%M"),
        "full_name": request.form.get("full_name", "").strip(),
        "email": request.form.get("email", "").strip(),
        "address": request.form.get("address", "").strip(),
        "city": request.form.get("city", "").strip(),
        "zip": request.form.get("zip", "").strip(),
        "items": [
            {"name": it["product"]["name"], "qty": it["qty"], "size": it["size"], "color": it["color"], "line_total": it["line_total"]}
            for it in items
        ],
        "totals": totals,
    }
    owner_email = session.get("shopper_email") or order["email"]
    ORDERS.setdefault(owner_email, []).insert(0, order)
    session["cart"] = {}
    session["promo_code"] = None
    session["last_order_id"] = order_id
    session["last_order_owner"] = owner_email
    session.modified = True
    return redirect(f"/order/{order_id}")


@app.route("/order/<order_id>")
def order_confirmation(order_id):
    owner_email = session.get("last_order_owner")
    order = next((o for o in ORDERS.get(owner_email, []) if o["id"] == order_id), None) if owner_email else None
    if not order:
        return redirect("/")
    lines = "".join(
        f'<div class="mini-line"><span>{it["qty"]}× {_esc(it["name"])} ({_esc(it["size"])}, {_esc(it["color"])})</span><span>{_money(it["line_total"])}</span></div>'
        for it in order["items"]
    )
    body = (
        '<div class="wrap section" style="max-width:640px;">'
        '<div class="empty-state" style="padding:20px 0 40px;">'
        '<div style="font-size:3rem;">✅</div>'
        '<h1 class="section-title">Order placed!</h1>'
        f'<p>Demo order <strong>{_esc(order["id"])}</strong> — nothing was actually charged.</p>'
        '</div>'
        '<div class="summary-card">'
        f'<div class="mini-line"><strong>Shipping to</strong><span></span></div>'
        f'<p style="font-size:.85rem;color:var(--muted);margin:0 0 16px;">{_esc(order["full_name"])}<br>{_esc(order["address"])}, {_esc(order["city"])} {_esc(order["zip"])}</p>'
        f'{lines}'
        f'<div class="summary-row total"><span>Total</span><span>{_money(order["totals"]["total"])}</span></div>'
        '</div>'
        '<div style="text-align:center;margin-top:26px;">'
        '<a class="btn" href="/shop">Continue shopping</a> '
        '<a class="btn btn-outline" style="color:var(--ink);border-color:var(--line);" href="/account">View my orders</a>'
        '</div>'
        '</div>'
    )
    return _page("Order confirmed", body)


# ---- Routes: shopper accounts -----------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    error = None
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or not password:
            error = "All fields are required."
        elif email in SHOPPERS:
            error = "An account with that email already exists — try logging in instead."
        else:
            SHOPPERS[email] = {"name": name, "password_hash": generate_password_hash(password)}
            session["shopper_email"] = email
            return redirect("/account")
    error_html = f'<p class="flash-err">{_esc(error)}</p>' if error else ""
    body = (
        '<div class="auth-wrap"><div class="auth-card">'
        '<h1>Create your account</h1><p class="sub">Track orders and check out faster next time.</p>'
        f'{error_html}'
        '<form method="post">'
        '<input type="text" name="name" placeholder="Full name" required>'
        '<input type="email" name="email" placeholder="Email" required>'
        '<input type="password" name="password" placeholder="Password" required>'
        '<button class="btn btn-block" type="submit">Create account</button>'
        '</form>'
        '<p class="auth-switch">Already have an account? <a href="/login">Log in</a></p>'
        '</div></div>'
    )
    return _page("Create account", body)


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        shopper = SHOPPERS.get(email)
        if shopper and check_password_hash(shopper["password_hash"], password):
            session["shopper_email"] = email
            return redirect("/account")
        error = "Incorrect email or password."
    error_html = f'<p class="flash-err">{_esc(error)}</p>' if error else ""
    body = (
        '<div class="auth-wrap"><div class="auth-card">'
        '<h1>Log in</h1><p class="sub">Welcome back.</p>'
        f'{error_html}'
        '<form method="post">'
        '<input type="email" name="email" placeholder="Email" required>'
        '<input type="password" name="password" placeholder="Password" required>'
        '<button class="btn btn-block" type="submit">Log in</button>'
        '</form>'
        '<p class="auth-switch">New here? <a href="/signup">Create an account</a></p>'
        '</div></div>'
    )
    return _page("Log in", body)


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("shopper_email", None)
    return redirect("/")


@app.route("/account")
def account():
    email = _require_shopper()
    if not email:
        return redirect("/login")
    shopper = SHOPPERS[email]
    orders = ORDERS.get(email, [])
    if orders:
        cards = []
        for o in orders:
            item_bits = []
            for it in o["items"]:
                item_bits.append(f'{it["qty"]}× {_esc(it["name"])}')
            items_summary = ", ".join(item_bits)
            cards.append(
                '<div class="order-card">'
                f'<div class="order-head"><strong>{_esc(o["id"])}</strong><span>{_esc(o["placed_at"])}</span>'
                f'<span>{_money(o["totals"]["total"])}</span></div>'
                f'<div class="order-items">{items_summary}</div>'
                '</div>'
            )
        order_cards = "".join(cards)
    else:
        order_cards = '<p style="color:var(--muted);font-size:.88rem;">No orders yet.</p>'
    logout_form = '<form method="post" action="/logout" style="display:inline;"><button class="btn btn-sm btn-outline" style="color:var(--ink);border-color:var(--line);" type="submit">Log out</button></form>'
    body = (
        '<div class="wrap section" style="max-width:720px;">'
        f'<div class="section-head"><div><h1 class="section-title">Hey, {_esc(shopper["name"])}</h1>'
        f'<p style="color:var(--muted);font-size:.85rem;margin-top:4px;">{_esc(email)}</p></div>{logout_form}</div>'
        '<h3 style="font-size:1.05rem;margin-bottom:14px;">Order history</h3>'
        f'{order_cards}'
        '</div>'
    )
    return _page("My account", body)


@app.route("/newsletter", methods=["POST"])
def newsletter():
    # Demo-only — nothing is actually sent anywhere; just confirms the
    # signup so the footer form isn't a dead end.
    return redirect(request.referrer or "/")


if __name__ == "__main__":
    print(f"STRYDE storefront running at http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False)
