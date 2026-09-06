"""
The component schema — the fixed vocabulary the Composition Engine is allowed
to build a screen from. This is what keeps generated output safe and
renderable instead of the model inventing arbitrary layout each time.

Mirrors the component types already used in the interactive demo:
stat_grid, panel, list.

Added 2026-09-05: timeline, metric, data_table — three more shapes, on top
of the original four, aimed squarely at "every screen ends up looking like
a grid" (the user's own words). Deliberately NOT a wholesale port of
workspace_primitives.py's 13-type vocabulary (that system's Primitives are
still unwired into Studio, per this project's own architecture notes) —
just the three that solve real, distinct problems the original four don't:
a chronological view (timeline), a single emphasized headline number
(metric, vs. stat_grid's grid-of-cards treatment for a single stat), and a
real multi-column table (data_table, vs. cramming several facts into one
list row's 'note' string — see agent_jira.py's own system prompt, which
already explicitly warns against exactly that). Each new type reuses an
existing field wherever possible instead of inventing a new one, so the
fabrication guardrails in guardrails.py keep covering it for free:
timeline reuses 'rows' (identical shape to 'list'), metric reuses 'stats'
(identical shape to 'stat_grid', expects exactly one entry). data_table is
the one genuinely new shape — 'columns' + 'table_rows' — and
guardrails.find_fabricated_content was extended with one matching loop
over table_rows[].values, same pattern as its existing rows/fields loops.

Added 2026-09-05 (same day, second round): chart, alert, task_queue —
display-only additions, same "reuse an existing field" discipline as
above. chart reuses 'stats' (a third consumer alongside stat_grid/metric —
rendered as a bar per entry instead of a card or a single big number).
task_queue reuses 'rows' (a third consumer alongside list/timeline —
rendered as a checklist). alert is the interesting one: it reuses 'title'/
'subtitle'/'badge', fields every component already has, so it needed NO
new schema fields at all — but unlike timeline/metric/chart/task_queue
(which only ever reuse fields guardrails.py already checks verbatim, like
rows[].name or stats[].value), an alert's title/subtitle is freeform prose
synthesizing a real condition ("3 issues are blocked and overdue"), which
is exactly the shape of claim find_ungrounded_suggestion_facts already
exists to police for 'suggestions' components — so guardrails.py gained a
sibling check, find_ungrounded_alert_claims, rather than leaving alert's
freeform text unchecked. Two more Primitive types remain deliberately
unbuilt for now (action_panel, approval_panel) — those need a real
write-capable backend action behind them, not just a renderer, and were
explicitly scoped as separate future work rather than bundled in here.

Added 2026-09-06 (round 3), from a much bigger vocabulary the user sketched
across LAYOUT/NAVIGATION/DISPLAY/INPUT/ACTION/OVERLAY/STATE — this round is
deliberately just the subset that fits the CURRENT flat-components-per-
screen architecture with no new backend capability: detail_view,
status_badge, empty_state, error_state, connection_state, pagination,
popover. Left out on purpose, same "action_panel/approval_panel" reasoning
as above: every INPUT type (form, text_input, select, ...) and every
ACTION type with a real effect (button/row_actions/bulk_actions that
DO something) need a write-capable backend no connector has yet; LAYOUT's
page/section/stack/grid/split_view and NAVIGATION's tabs need a real
nested/recursive schema (today's 'components' is a flat top-level array) —
a contained architecture change, not a vocabulary add, and intentionally
not bundled into this round either.
  - detail_view reuses 'title'/'subtitle'/'badge'/'fields'/'action' —
    the exact same shape as 'panel', just a visually richer, more
    prominent treatment for when the ENTIRE request is about one specific
    record (that record IS the whole answer), vs. panel's compact detail
    box or aggregate-stats use.
  - status_badge reuses 'title'/'badge' — a quiet, single-line status
    indicator (e.g. "Sync status: Up to date"), distinct from 'alert'
    which is for something urgent enough to warrant a loud banner.
  - empty_state reuses 'title'/'subtitle' — the deliberate, explicit way
    to render "nothing matched, and that itself is the real answer" as
    its own screen-level component, rather than leaving a genuine zero
    result to an empty list's fallback message or (worse) a plain-text
    chat reply no guardrail ever checks (see skills/render_dont_narrate.md).
  - error_state reuses 'title'/'subtitle'/'badge'/'action' — a structured,
    visible card for "this data source failed" as part of the rendered
    screen itself, alongside whatever other sources DID work, rather than
    only ever a one-line 'meta' note.
  - connection_state reuses 'stats' (label/value/tone) — one entry per
    app/source with value "Connected"/"Not connected", for when a request
    is literally about integration status; kept distinct from stat_grid
    so the model doesn't reach for a big-number card treatment on what's
    really a short status string.
  - pagination is the one genuinely new shape: 'page'/'total_pages'/
    'total_count'. Ships as a display primitive now — no connector
    currently returns real pagination metadata (get_issues/get_tickets/
    get_gmail_messages have no offset/cursor support), so wiring an
    actual "fetch page 2" flow per connector is separate, later work,
    same "ship the primitive, wire deeper integration after" pattern as
    round 1/2's Studio-integration fixes.
  - popover reuses 'title' (the trigger/summary label) + 'fields' (the
    extra content revealed on click) — rendered with a native HTML
    <details>/<summary> disclosure, so it needs no JavaScript at all.
  - 'hint' is NOT a new component type — it's a new OPTIONAL field added
    to 'fields' items and to every 'badge' object, rendered as a native
    title="..." hover tooltip. This is the one item from the user's list
    (OVERLAY's "tooltip") that's structurally an attribute of something
    else, not a freestanding block, so it's modeled as one instead of
    forcing it into the type enum.
  - guardrails.py's find_ungrounded_alert_claims was broadened to also
    scan status_badge/empty_state/error_state's title/subtitle — the same
    freeform-claim-needs-grounding shape as alert, so this round needed no
    new guardrail function, just a wider net on the existing one.
"""

UI_SCHEMA = {
    "type": "object",
    "properties": {
        "heading": {
            "type": "string",
            "description": "The screen's main heading — should directly answer the request, e.g. '3 critical incidents affecting customer systems'."
        },
        "meta": {
            "type": "string",
            "description": "One short line of context under the heading, e.g. 'ranked by what needs investigating first'."
        },
        "components": {
            "type": "array",
            "description": "The ordered list of components that make up the screen. Include ONLY what's needed to answer the request — no extra sections.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": [
                            "stat_grid", "panel", "list", "suggestions", "timeline", "metric",
                            "data_table", "chart", "alert", "task_queue", "detail_view",
                            "status_badge", "empty_state", "error_state", "connection_state",
                            "pagination", "popover",
                        ]
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "A section title for most component types. For type=alert, this is the "
                            "claim itself — the one flagged condition, e.g. '3 issues are blocked "
                            "and overdue'. It must be a real, derived-from-real-data statement (a "
                            "count or fact that traces back to what you actually fetched), never an "
                            "invented figure — same standard as a stat_grid/metric value, just "
                            "stated as a sentence instead of a label+number pair. For type=status_badge, "
                            "a short real status label (e.g. 'Sync status'). For type=empty_state, the "
                            "honest 'nothing matched' statement itself, e.g. 'No blocked issues right "
                            "now'. For type=error_state, which real source/step failed, e.g. 'Gmail "
                            "isn't accessible right now'. For type=popover, the trigger/summary label "
                            "someone clicks to reveal the fields below it, e.g. 'Show full details'."
                        )
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Supporting detail under the title. For type=alert, one more sentence of real context — what to do about it, or why it matters — not a second, different claim. For type=empty_state/error_state, one more sentence of real context — why, or what to do about it."
                    },
                    "badge": {
                        "type": "object",
                        "description": "A small tone-colored tag. For type=alert, sets its overall tone/urgency (e.g. tone='critical' for something needing immediate attention) — every alert should set one. For type=status_badge, IS the status itself (text + tone) alongside the title label.",
                        "properties": {
                            "text": {"type": "string"},
                            "tone": {"type": "string", "enum": ["default", "critical", "warning", "good"]},
                            "hint": {"type": "string", "description": "OPTIONAL short hover tooltip explaining this badge, shown on mouseover. Never a place for a new fact — explanatory text only, e.g. 'Highest priority = drop everything else'."}
                        }
                    },
                    "fields": {
                        "type": "array",
                        "description": "Key/value pairs for a panel or detail_view, e.g. affected systems, threat intel. For type=popover, the extra content revealed when the trigger (the 'title') is clicked.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "hint": {"type": "string", "description": "OPTIONAL short hover tooltip explaining this field, shown on mouseover. Never a place for a new fact — explanatory text only, e.g. 'Story points = relative complexity estimate, not hours'."}
                            },
                            "required": ["label", "value"]
                        }
                    },
                    "action": {"type": "string", "description": "Label for a single primary button, if this screen needs one. For type=error_state, a real recovery step, e.g. 'Reconnect Gmail'."},
                    "stats": {
                        "type": "array",
                        "description": (
                            "For type=stat_grid (several related numbers shown as a grid of cards), "
                            "type=metric (exactly ONE headline number, shown big and emphasized — "
                            "use metric instead of a one-card stat_grid whenever the answer to the "
                            "request really is a single number, e.g. 'how many issues are blocked?'; "
                            "provide exactly one entry in stats when type=metric), type=chart "
                            "(several related numbers shown as a bar breakdown — use chart instead "
                            "of stat_grid when the request is about how something splits up/compares "
                            "across categories, e.g. 'break down open issues by priority'; each "
                            "entry's 'value' should be the plain count/number as a string, e.g. '5', "
                            "not a formatted sentence, so bar lengths can be compared), or "
                            "type=connection_state (one entry per app/source — label = the app name, "
                            "value = its real connection state as a short string, e.g. 'Connected'/"
                            "'Not connected'/'Token expired', tone = good/critical to match — use this "
                            "instead of stat_grid when the request is literally about integration/"
                            "connectivity status, not a count)."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"},
                                "tone": {"type": "string", "enum": ["default", "critical", "warning", "good"]}
                            },
                            "required": ["label", "value"]
                        }
                    },
                    "suggestions": {
                        "type": "array",
                        "description": (
                            "For type=suggestions ONLY. Each item is a short recommended next "
                            "action, e.g. 'Proactively reach out about the delayed order' — YOUR "
                            "judgment based on the real data already fetched, not a fact and not "
                            "itself something to state as certain. Never put a specific number, "
                            "name, or other fact here that doesn't already appear in a stat_grid/"
                            "panel/list component on this same screen — a suggestion can reason "
                            "about real data shown elsewhere, but must not introduce new claims of "
                            "its own. Omit this component entirely rather than force a suggestion "
                            "when nothing about the real data actually warrants one."
                        ),
                        "items": {"type": "string"}
                    },
                    "rows": {
                        "type": "array",
                        "description": (
                            "For type=list (a plain scannable list of items), type=timeline "
                            "(the SAME row shape, but rendered as a chronological sequence — use "
                            "timeline instead of list when the request is naturally about order/"
                            "history over time, e.g. 'what happened on this issue', 'recent "
                            "activity', a sequence of status changes. Put rows in chronological "
                            "order — usually oldest first — and use 'note' for the when/context, "
                            "e.g. name='Moved to In Progress', note='2 days ago by Priya'), or "
                            "type=task_queue (the SAME row shape again, rendered as a checklist — "
                            "use task_queue instead of list when the request is about outstanding "
                            "work/to-dos, e.g. 'what's overdue', 'what needs to happen next'; "
                            "'name' = the task/issue, 'note' = owner and/or due date, e.g. "
                            "'Priya Nair — due 2026-09-02')."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "note": {"type": "string"},
                                "action": {"type": "string"},
                                "url": {
                                    "type": "string",
                                    "description": "OPTIONAL real link back to this row's source (e.g. the exact url a search result returned). Only set this to a value that appeared verbatim in real fetched tool data — never invent one. Omit entirely if there's no real link for this row."
                                },
                                "badge": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "tone": {"type": "string", "enum": ["default", "critical", "warning", "good"]},
                                        "hint": {"type": "string", "description": "OPTIONAL short hover tooltip explaining this badge. Explanatory text only, never a new fact."}
                                    }
                                }
                            },
                            "required": ["name"]
                        }
                    },
                    "columns": {
                        "type": "array",
                        "description": (
                            "For type=data_table ONLY. Ordered column headers, e.g. "
                            "['Key', 'Summary', 'Assignee', 'Priority']. Pick the 2-5 columns that "
                            "actually answer the request — same 'don't dump every field' judgment "
                            "already used for list/panel, just applied to real columns instead of "
                            "cramming several facts into one row's note."
                        ),
                        "items": {"type": "string"}
                    },
                    "table_rows": {
                        "type": "array",
                        "description": (
                            "For type=data_table ONLY. One entry per record. Each entry's 'values' "
                            "array must have exactly as many strings, in the exact same order, as "
                            "'columns' — values[i] is the value for columns[i]."
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "values": {"type": "array", "items": {"type": "string"}},
                                "badge": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "tone": {"type": "string", "enum": ["default", "critical", "warning", "good"]},
                                        "hint": {"type": "string", "description": "OPTIONAL short hover tooltip explaining this badge. Explanatory text only, never a new fact."}
                                    }
                                }
                            },
                            "required": ["values"]
                        }
                    },
                    "page": {
                        "type": "integer",
                        "description": "For type=pagination ONLY. The current real page number (1-indexed) of the data actually fetched."
                    },
                    "total_pages": {
                        "type": "integer",
                        "description": "For type=pagination ONLY. The real total number of pages, given how many items fit on one page."
                    },
                    "total_count": {
                        "type": "integer",
                        "description": "For type=pagination ONLY. The real total number of items across all pages — never an estimate."
                    }
                },
                "required": ["type"]
            }
        }
    },
    "required": ["heading", "components"]
}
