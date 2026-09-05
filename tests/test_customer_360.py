"""
Structural + guardrail tests for the customer-360 feature (2026-08-27):
primitives.py's retail_orders, schema.py's "suggestions" component,
guardrails.py's find_ungrounded_suggestion_facts, and studio.py's
/customers routes. Deliberately NOT a live-API test (see
test_first_turn_live.py for that pattern) — these check the parts that
don't need a real Claude call: registration, rendering, and the
grounding-check bug caught live while building this (a suggestion's whole
sentence was being checked against fetched data instead of just the
monetary figure inside it, which false-positived on legitimate suggestions
— see guardrails.py's _CURRENCY_TOKEN_RE comment for the full story).
"""

import sys
sys.path.insert(0, "/home/claude/pilant-agent")

from primitives import REGISTRY
from schema import UI_SCHEMA
from renderer import _component_html
from guardrails import find_ungrounded_suggestion_facts


def test_retail_orders_registered():
    p = REGISTRY.get("retail_orders")
    assert p is not None
    assert p["connector"] == "retail"
    assert p["type"] == "data_source"
    assert "customer" in p["input_schema"]["properties"]


def test_schema_has_suggestions_type():
    types = UI_SCHEMA["properties"]["components"]["items"]["properties"]["type"]["enum"]
    assert "suggestions" in types
    assert "suggestions" in UI_SCHEMA["properties"]["components"]["items"]["properties"]


def test_suggestions_render_distinct_from_facts():
    html = _component_html({
        "type": "suggestions",
        "title": "Recommended",
        "suggestions": ["Reach out about the delayed order"],
    })
    assert "suggestion-panel" in html
    assert "AI suggestion" in html
    # Never rendered inside .panel — that's the fact-panel treatment; a
    # suggestion must never be visually indistinguishable from real data.
    assert 'class="panel"' not in html


def test_suggestions_empty_renders_nothing():
    assert _component_html({"type": "suggestions", "suggestions": []}) == ""


def _real_order_messages():
    return [{
        "role": "user",
        "content": [{
            "type": "tool_result",
            "tool_use_id": "x",
            "content": '[{"id": "#1001", "total": "$42.00 USD", "status": "unfulfilled", "customer": "J. Okafor"}]',
        }],
    }]


def test_grounded_suggestion_not_flagged():
    """A suggestion that cites a real figure, wrapped in normal prose, must
    NOT be flagged — this is the exact case that was broken before the
    _CURRENCY_TOKEN_RE fix (the whole sentence was checked instead of just
    the figure, and the sentence's connective words dragged the token
    overlap below threshold)."""
    view = {"components": [{
        "type": "suggestions",
        "suggestions": ["Order #1001 totalling $42.00 is unfulfilled — check on it"],
    }]}
    assert find_ungrounded_suggestion_facts(view, _real_order_messages()) == []


def test_fabricated_suggestion_figure_is_flagged():
    view = {"components": [{
        "type": "suggestions",
        "suggestions": ["They have spent $10,000 with us this year, consider a loyalty discount"],
    }]}
    bad = find_ungrounded_suggestion_facts(view, _real_order_messages())
    assert len(bad) == 1


def test_suggestion_with_no_currency_is_never_checked():
    view = {"components": [{
        "type": "suggestions",
        "suggestions": ["Proactively follow up about the unfulfilled order"],
    }]}
    assert find_ungrounded_suggestion_facts(view, _real_order_messages()) == []


def test_customer_routes_registered():
    import studio
    rules = {r.rule for r in studio.app.url_map.iter_rules()}
    assert "/customers" in rules
    assert "/customers/open" in rules


if __name__ == "__main__":
    test_retail_orders_registered()
    test_schema_has_suggestions_type()
    test_suggestions_render_distinct_from_facts()
    test_suggestions_empty_renders_nothing()
    test_grounded_suggestion_not_flagged()
    test_fabricated_suggestion_figure_is_flagged()
    test_suggestion_with_no_currency_is_never_checked()
    test_customer_routes_registered()
    print("all customer-360 tests passed")
