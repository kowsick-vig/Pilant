"""
Pilant's lightweight skills layer.

A "skill" here is a small, standalone chunk of reusable guidance text that
more than one connector's SYSTEM prompt wants — the running example is
"which render_view component type fits this request" (see
skills/ui_composition.md). Before this existed, that guidance had to be
hand-copied into every agent_X.py file's SYSTEM string one at a time: when
the 2026-09-05 component types (timeline/metric/data_table/chart/alert/
task_queue) were added, only agent_jira.py actually got the usage guidance,
because copying the same paragraph into five more multi-thousand-line
connector files by hand wasn't done in the same pass. A skill lives once,
as a plain text file under skills/, and any connector pulls it in with
load_skill(name) — one edit to the file updates every connector that loads
it, instead of N manual edits across N files.

Deliberately NOT the same thing as this session's own Skill system (a
SKILL.md with frontmatter, discovered and invoked by name through a
dedicated tool, sometimes running in its own subagent) — this is a much
smaller, purely-Pilant-internal version of the same idea: no frontmatter,
no discovery/triggering logic, no invocation protocol, just "read a text
file, cache it, concatenate it into a prompt string." If Pilant's own
needs grow past that — many skills, choosing which ones apply to a given
request, per-customer skill sets — this module is the seam to extend
rather than the ceiling to work around.

Usage, from any agent_*.py connector:

    from skills import load_skill
    SYSTEM = (
        "...the connector's own domain-specific instructions..."
        + load_skill("ui_composition") + "\n\n" +
        "...whatever comes after..."
    )

load_skill() reads skills/<name>.md relative to this file (not relative to
the caller or the process's cwd, so it works the same regardless of where
studio.py is actually run from), strips surrounding whitespace, and caches
the result for the life of the process — these files aren't expected to
change while the app is running, so there's no reason to re-read one on
every single request.
"""

import os

_SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")

_cache = {}


def load_skill(name):
    """Returns the guidance text for skills/<name>.md. Raises FileNotFoundError
    with a clear message if the skill doesn't exist — fail loudly at import
    time rather than silently shipping a connector with a chunk of its
    prompt missing."""
    if name not in _cache:
        path = os.path.join(_SKILLS_DIR, f"{name}.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                _cache[name] = f.read().strip()
        except FileNotFoundError:
            raise FileNotFoundError(
                f"skills.load_skill({name!r}): no such skill file at {path}. "
                "Check the name, or that skills/{name}.md exists."
            )
    return _cache[name]
