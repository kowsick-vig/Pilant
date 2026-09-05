"""
Static catalog + discount data for storefront.py — a brand-new, fictional
public-facing athletic-apparel storefront ("STRYDE"), built at the user's
request for "a proper gym[shark-style] website for public users ... crowded
... add some discount, offers ... want all in the things in gym shark."

IMPORTANT: this is NOT Gymshark. The user asked for a Gymshark-style public
storefront, but Gymshark is a real company — using its actual name, logo, or
branding would be impersonating a real business, which is refused
regardless of the reason given, "it's just a demo" included. Confirmed with
the user directly (AskUserQuestion): build a fictional brand with the same
bold, promo-heavy, athletic-wear energy instead. STRYDE is that brand —
invented for this project, not modeled on any single real company's
identity beyond the general "athletic apparel DTC storefront" genre.

Same static-connector shape as every other mock data source in this
project (connectors_jira.py's ISSUES, connectors_helpdesk.py's TICKETS): a
plain in-memory Python list, no network call, no external dependency —
easy to read, easy to extend, and exactly as fake as it looks (no attempt
to pass off product photos as real; PRODUCTS' "swatch" field below is a
CSS color + an emoji glyph used to render a placeholder product tile
instead of a fabricated photo).
"""

BRAND_NAME = "STRYDE"
BRAND_TAGLINE = "Train loud."
BRAND_DOMAIN_LABEL = "stryde.example"  # deliberately a non-resolving placeholder domain, never presented as a real live site

CATEGORIES = [
    {"slug": "leggings", "label": "Leggings", "icon": "🦵"},
    {"slug": "sports-bras", "label": "Sports Bras", "icon": "🎽"},
    {"slug": "tops", "label": "Tops & Tanks", "icon": "👕"},
    {"slug": "shorts", "label": "Shorts", "icon": "🩳"},
    {"slug": "outerwear", "label": "Hoodies & Outerwear", "icon": "🧥"},
    {"slug": "accessories", "label": "Accessories", "icon": "🎒"},
]

# Each product: id, name, category slug, price (USD), sale_price (None if
# not on sale), badge (New/Bestseller/Sale/Limited or None), swatch (a hex
# color + emoji standing in for a real product photo — see module
# docstring), sizes, colors, rating (1-5), review_count, description.
PRODUCTS = [
    {"id": "lg-001", "name": "Momentum Seamless Leggings", "category": "leggings", "price": 68, "sale_price": 48,
     "badge": "Bestseller", "swatch": {"bg": "#1B1F23", "icon": "🦵"}, "sizes": ["XS", "S", "M", "L", "XL"],
     "colors": ["Jet Black", "Storm Grey", "Volt Green"], "rating": 4.8, "review_count": 1204,
     "description": "Squat-proof seamless compression leggings with a high waistband and side phone pocket."},
    {"id": "lg-002", "name": "Flux High-Waist Leggings", "category": "leggings", "price": 62, "sale_price": None,
     "badge": "New", "swatch": {"bg": "#2E2A55", "icon": "🦵"}, "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
     "colors": ["Deep Indigo", "Jet Black"], "rating": 4.6, "review_count": 312,
     "description": "Buttery-soft four-way stretch fabric built for lifting days, with a wide non-slip waistband."},
    {"id": "lg-003", "name": "Cargo Utility Leggings", "category": "leggings", "price": 72, "sale_price": 54,
     "badge": "Sale", "swatch": {"bg": "#3A4230", "icon": "🦵"}, "sizes": ["S", "M", "L", "XL"],
     "colors": ["Olive", "Jet Black"], "rating": 4.4, "review_count": 189,
     "description": "Utility-pocket leggings that go straight from the gym to the street."},
    {"id": "sb-001", "name": "Pulse Sculpt Sports Bra", "category": "sports-bras", "price": 38, "sale_price": 28,
     "badge": "Bestseller", "swatch": {"bg": "#8A1F3D", "icon": "🎽"}, "sizes": ["XS", "S", "M", "L", "XL"],
     "colors": ["Berry", "Jet Black", "White"], "rating": 4.9, "review_count": 2041,
     "description": "High-support racerback bra with removable padding, built for heavy lifting and HIIT."},
    {"id": "sb-002", "name": "Aero Strappy Bra", "category": "sports-bras", "price": 34, "sale_price": None,
     "badge": "New", "swatch": {"bg": "#0F5C5C", "icon": "🎽"}, "sizes": ["XS", "S", "M", "L"],
     "colors": ["Teal", "Jet Black"], "rating": 4.5, "review_count": 96,
     "description": "Low-impact strappy-back bra with a barely-there feel for yoga and studio classes."},
    {"id": "tp-001", "name": "Momentum Crop Tank", "category": "tops", "price": 32, "sale_price": 22,
     "badge": "Sale", "swatch": {"bg": "#1B1F23", "icon": "👕"}, "sizes": ["XS", "S", "M", "L", "XL"],
     "colors": ["Jet Black", "White", "Volt Green"], "rating": 4.7, "review_count": 743,
     "description": "Cropped, breathable racerback tank with a relaxed fit for lifting and running alike."},
    {"id": "tp-002", "name": "Oversized Rest Day Tee", "category": "tops", "price": 36, "sale_price": None,
     "badge": None, "swatch": {"bg": "#C9C2B4", "icon": "👕"}, "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
     "colors": ["Sand", "Jet Black", "Storm Grey"], "rating": 4.6, "review_count": 508,
     "description": "Heavyweight oversized cotton tee for everything that isn't training."},
    {"id": "tp-003", "name": "Featherweight Long Sleeve", "category": "tops", "price": 44, "sale_price": 34,
     "badge": "Sale", "swatch": {"bg": "#2E2A55", "icon": "👕"}, "sizes": ["XS", "S", "M", "L"],
     "colors": ["Deep Indigo", "Jet Black"], "rating": 4.5, "review_count": 221,
     "description": "Ultra-light long sleeve with thumbholes, built to layer under a hoodie or wear alone."},
    {"id": "sh-001", "name": "Sprint 5\" Running Shorts", "category": "shorts", "price": 40, "sale_price": None,
     "badge": "New", "swatch": {"bg": "#8A1F3D", "icon": "🩳"}, "sizes": ["XS", "S", "M", "L", "XL"],
     "colors": ["Berry", "Jet Black"], "rating": 4.6, "review_count": 167,
     "description": "Built-in liner, zip pocket, and a 5-inch inseam for tempo runs and track days."},
    {"id": "sh-002", "name": "Momentum Bike Shorts", "category": "shorts", "price": 42, "sale_price": 30,
     "badge": "Bestseller", "swatch": {"bg": "#1B1F23", "icon": "🩳"}, "sizes": ["XS", "S", "M", "L", "XL"],
     "colors": ["Jet Black", "Volt Green"], "rating": 4.8, "review_count": 986,
     "description": "The bike short version of the Momentum leggings — same squat-proof fabric, cropped."},
    {"id": "ow-001", "name": "Heavyweight Zip Hoodie", "category": "outerwear", "price": 78, "sale_price": 58,
     "badge": "Sale", "swatch": {"bg": "#1B1F23", "icon": "🧥"}, "sizes": ["XS", "S", "M", "L", "XL", "XXL"],
     "colors": ["Jet Black", "Storm Grey"], "rating": 4.7, "review_count": 654,
     "description": "Brushed-fleece full-zip hoodie with a kangaroo pocket, built for warm-ups and rest days."},
    {"id": "ow-002", "name": "Windbreak Running Jacket", "category": "outerwear", "price": 84, "sale_price": None,
     "badge": "New", "swatch": {"bg": "#0F5C5C", "icon": "🧥"}, "sizes": ["S", "M", "L", "XL"],
     "colors": ["Teal", "Jet Black"], "rating": 4.4, "review_count": 58,
     "description": "Packable, water-resistant shell with underarm vents for outdoor training in any weather."},
    {"id": "ac-001", "name": "Motion Duffel Bag", "category": "accessories", "price": 56, "sale_price": 40,
     "badge": "Sale", "swatch": {"bg": "#1B1F23", "icon": "🎒"}, "sizes": ["One Size"],
     "colors": ["Jet Black"], "rating": 4.7, "review_count": 312,
     "description": "Wet-pocket gym duffel with a shoe compartment and adjustable strap."},
    {"id": "ac-002", "name": "Grip Training Gloves", "category": "accessories", "price": 24, "sale_price": None,
     "badge": None, "swatch": {"bg": "#3A4230", "icon": "🎒"}, "sizes": ["S", "M", "L"],
     "colors": ["Olive", "Jet Black"], "rating": 4.3, "review_count": 141,
     "description": "Palm-padded lifting gloves with wrist wraps for heavy pulling days."},
    {"id": "ac-003", "name": "Volt Steel Water Bottle", "category": "accessories", "price": 22, "sale_price": 16,
     "badge": "Sale", "swatch": {"bg": "#B8FF3C", "icon": "🎒"}, "sizes": ["24oz"],
     "colors": ["Volt Green", "Jet Black"], "rating": 4.9, "review_count": 890,
     "description": "Insulated steel bottle that keeps drinks cold through a full training block."},
    {"id": "ac-004", "name": "No-Slip Headband 2-Pack", "category": "accessories", "price": 14, "sale_price": None,
     "badge": None, "swatch": {"bg": "#8A1F3D", "icon": "🎒"}, "sizes": ["One Size"],
     "colors": ["Berry / Black"], "rating": 4.5, "review_count": 233,
     "description": "Moisture-wicking, silicone-gripped headbands that actually stay put through burpees."},
]

# Sitewide promo codes — checked at checkout (see storefront.py's
# _apply_discount_code). Percent-off, applied to the cart's subtotal
# AFTER each item's own sale price, same order real storefronts apply
# stacked discounts in.
DISCOUNT_CODES = {
    "SWEAT20": {"percent_off": 20, "description": "20% off your order"},
    "STRYDE10": {"percent_off": 10, "description": "10% off for new accounts"},
    "FREESHIP": {"percent_off": 0, "description": "Free shipping (shipping is already free on this demo store)"},
}

FREE_SHIPPING_THRESHOLD = 75


def get_product(product_id):
    for p in PRODUCTS:
        if p["id"] == product_id:
            return p
    return None


def get_products_by_category(slug):
    if not slug or slug == "all":
        return list(PRODUCTS)
    return [p for p in PRODUCTS if p["category"] == slug]


def get_bestsellers(limit=4):
    return [p for p in PRODUCTS if p["badge"] == "Bestseller"][:limit]


def get_new_arrivals(limit=4):
    return [p for p in PRODUCTS if p["badge"] == "New"][:limit]


def get_sale_items(limit=8):
    return [p for p in PRODUCTS if p["sale_price"]][:limit]
