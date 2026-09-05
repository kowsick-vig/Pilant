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
                        "enum": ["stat_grid", "panel", "list", "suggestions", "timeline", "metric", "data_table", "chart", "alert", "task_queue"]
                    },
                    "title": {
                        "type": "string",
                        "description": (
                            "A section title for most component types. For type=alert, this is the "
                            "claim itself — the one flagged condition, e.g. '3 issues are blocked "
                            "and overdue'. It must be a real, derived-from-real-data statement (a "
                            "count or fact that traces back to what you actually fetched), never an "
                            "invented figure — same standard as a stat_grid/metric value, just "
                            "stated as a sentence instead of a label+number pair."
                        )
                    },
                    "subtitle": {
                        "type": "string",
                        "description": "Supporting detail under the title. For type=alert, one more sentence of real context — what to do about it, or why it matters — not a second, different claim."
                    },
                    "badge": {
                        "type": "object",
                        "description": "A small tone-colored tag. For type=alert, sets its overall tone/urgency (e.g. tone='critical' for something needing immediate attention) — every alert should set one.",
                        "properties": {
                            "text": {"type": "string"},
                            "tone": {"type": "string", "enum": ["default", "critical", "warning", "good"]}
                        }
                    },
                    "fields": {
                        "type": "array",
                        "description": "Key/value pairs for a panel, e.g. affected systems, threat intel.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "value": {"type": "string"}
                            },
                            "required": ["label", "value"]
                        }
                    },
                    "action": {"type": "string", "description": "Label for a single primary button, if this screen needs one."},
                    "stats": {
                        "type": "array",
                        "description": (
                            "For type=stat_grid (several related numbers shown as a grid of cards), "
                            "type=metric (exactly ONE headline number, shown big and emphasized — "
                            "use metric instead of a one-card stat_grid whenever the answer to the "
                            "request really is a single number, e.g. 'how many issues are blocked?'; "
                            "provide exactly one entry in stats when type=metric), or type=chart "
                            "(several related numbers shown as a bar breakdown — use chart instead "
                            "of stat_grid when the request is about how something splits up/compares "
                            "across categories, e.g. 'break down open issues by priority'; each "
                            "entry's 'value' should be the plain count/number as a string, e.g. '5', "
                            "not a formatted sentence, so bar lengths can be compared)."
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
                                        "tone": {"type": "string", "enum": ["default", "critical", "warning", "good"]}
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
                                        "tone": {"type": "string", "enum": ["default", "critical", "warning", "good"]}
                                    }
                                }
                            },
                            "required": ["values"]
                        }
                    }
                },
                "required": ["type"]
            }
        }
    },
    "required": ["heading", "components"]
}
