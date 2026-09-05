"""
Validates a generated view against the UI schema and produces a short,
specific error the model can act on — this is what turns "the model did
something wrong" into "the model corrects itself and retries," instead of
malformed output silently reaching a renderer.

Note on schema.py's 2026-09-05 new component types (timeline, metric,
data_table): nothing here needed to change. This validator runs whatever
JSON Schema schema.py hands it via Draft7Validator, generically — it has
no per-type logic of its own — so the new types are already fully
enforced the moment schema.py's enum/field definitions include them.
"""

from jsonschema import Draft7Validator


def validate_view(view, schema):
    """Returns None if valid, otherwise a short string describing what's wrong."""
    if not isinstance(view, dict):
        return f"render_view must be called with a JSON object, not {type(view).__name__}."

    validator = Draft7Validator(schema)
    errors = sorted(validator.iter_errors(view), key=lambda e: list(e.path))
    if not errors:
        return None

    first = errors[0]
    path = ".".join(str(p) for p in first.path) or "(root)"
    return (
        f"Invalid render_view call at '{path}': {first.message}. "
        "Fix this and call render_view again. Use real, correctly-typed data from "
        "the tools you already called — never placeholder or template syntax like "
        "{{tool_name(...)}}. If you haven't actually called a data tool yet, call it "
        "first, wait for its result, then use those real values."
    )
