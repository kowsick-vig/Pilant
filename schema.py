"""
The component schema — the fixed vocabulary the Composition Engine is allowed
to build a screen from. This is what keeps generated output safe and
renderable instead of the model inventing arbitrary layout each time.

Mirrors the component types already used in the interactive demo:
stat_grid, panel, list.
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
                        "enum": ["stat_grid", "panel", "list", "suggestions"]
                    },
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "badge": {
                        "type": "object",
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
                        "description": "For type=stat_grid.",
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
                        "description": "For type=list.",
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
                    }
                },
                "required": ["type"]
            }
        }
    },
    "required": ["heading", "components"]
}
