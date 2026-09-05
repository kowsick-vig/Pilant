"""
Reproduction test for the 2026-08-25 "Finance Summary" bug the user spotted live:
a Gmail workflow answering "summarize what came in from finance this week" rendered
a stat_grid titled "Finance Summary" with invented figures (TOTAL $10,000, DUE TODAY
$500) that trace back to nothing real — Gmail's fetched message data has no
monetary fields at all. find_fabricated_content() (guardrails.py) never checked
stat_grid's `stats[].value` at all (deliberately, to avoid false-flagging legitimate
derived counts/percentages), which is exactly the gap that let this through.

Fix: a new, narrower guardrails.find_fabricated_stats() checks ONLY stat values that
look financial (a currency symbol + digits, or a label like Total/Due/Balance) against
real fetched content, wired into all 4 connector agents' render_view rejection path
alongside the existing checks. Ordinary non-financial derived stats stay exempt.
"""
import sys, json

sys.path.insert(0, "/home/claude/pilant-agent")

from guardrails import find_fabricated_stats

FAKE_TOOL_MESSAGES = [
    {"role": "tool", "tool_call_id": "tc1", "content": json.dumps([
        {"from": "billing@acme.com", "subject": "Your invoice is ready", "date": "Aug 20, 2026",
         "snippet": "Nothing due, your subscription renews automatically.", "unread": True, "id": "m1"},
        {"from": "hr@acme.com", "subject": "Payroll update", "date": "Aug 22, 2026",
         "snippet": "New payroll schedule starting next month.", "unread": True, "id": "m2"},
    ])},
]

# ---- Scenario 1: the exact live bug — a fabricated financial stat_grid ------
bad_view = {
    "heading": "Finance Summary",
    "components": [{
        "type": "stat_grid",
        "title": "Finance Summary",
        "subtitle": "This week",
        "stats": [
            {"label": "Total", "value": "$10,000"},
            {"label": "Due Today", "value": "$500"},
        ],
    }],
}
bad = find_fabricated_stats(bad_view, FAKE_TOOL_MESSAGES)
assert bad, "expected the fabricated $10,000/$500 finance stats to be flagged"
assert any("10,000" in b or "10000" in b for b in bad), f"expected the fake TOTAL figure flagged, got {bad}"
assert any("500" in b for b in bad), f"expected the fake DUE TODAY figure flagged, got {bad}"
print(f"PASSED — Scenario 1: fabricated 'Finance Summary' stat_grid (TOTAL $10,000, DUE TODAY $500) "
      f"correctly REJECTED — flagged: {bad}")

# ---- Scenario 2: a legitimate, non-financial derived stat is NOT flagged ---
good_view = {
    "heading": "Inbox overview",
    "components": [{
        "type": "stat_grid",
        "stats": [
            {"label": "Unread", "value": "2"},
            {"label": "This week", "value": "2 emails"},
        ],
    }],
}
ok = find_fabricated_stats(good_view, FAKE_TOOL_MESSAGES)
assert not ok, f"legitimate non-financial derived stats should NOT be flagged, got {ok}"
print("PASSED — Scenario 2: ordinary derived counts ('Unread: 2', '2 emails') pass through untouched.")

# ---- Scenario 3: a financial figure that IS grounded in real fetched data --
grounded_messages = [
    {"role": "tool", "tool_call_id": "tc1", "content": json.dumps([
        {"from": "billing@acme.com", "subject": "Invoice #4471 for $1,240", "date": "Aug 20, 2026",
         "snippet": "Amount due: $1,240 by Sept 1.", "unread": True, "id": "m1"},
    ])},
]
grounded_view = {
    "heading": "Finance Summary",
    "components": [{
        "type": "stat_grid",
        "stats": [{"label": "Total Due", "value": "$1,240"}],
    }],
}
grounded_bad = find_fabricated_stats(grounded_view, grounded_messages)
assert not grounded_bad, f"a dollar figure that DOES appear in real fetched data should not be flagged, got {grounded_bad}"
print("PASSED — Scenario 3: a financial figure that genuinely appears in real fetched data is NOT flagged.")

print()
print("ALL FABRICATED-STATS GUARDRAIL SCENARIOS PASSED")
