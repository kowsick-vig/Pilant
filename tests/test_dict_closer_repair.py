import sys, json
sys.path.insert(0, "/tmp")
from round8_raw import raw_repr

outer = json.loads(raw_repr)
components_str = outer["components"]


def _insert_missing_dict_closers(s):
    """Only tracks real JSON double-quotes (with backslash-escape awareness),
    same convention as _close_truncated_json — must run AFTER
    _repair_blanket_quoted_json, never on still-single-quoted raw text (a
    literal apostrophe like "you've" would desync naive quote-tracking
    otherwise, same reason round 6 needed repair-then-close ordering)."""
    out = []
    stack = []
    in_string = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "{" or ch == "[":
            if ch == "{" and stack and stack[-1] == "{":
                j = len(out) - 1
                while j >= 0 and out[j] in (" ", "\t", "\n", "\r"):
                    j -= 1
                if j >= 0 and out[j] == ",":
                    out.insert(j, "}")
                    stack.pop()
            stack.append(ch)
            out.append(ch)
            i += 1
            continue
        if ch == "}" or ch == "]":
            if stack:
                stack.pop()
            out.append(ch)
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# Now run it through the SAME quote-repair heuristic already in agent_gmail.py FIRST,
# then the new dict-closer repair on the already-double-quoted text.
sys.path.insert(0, "/home/claude/pilant-agent")
import types
fake_openai = types.ModuleType("openai")
class FakeOpenAI:
    def __init__(self, *a, **k): pass
fake_openai.OpenAI = FakeOpenAI
sys.modules["openai"] = fake_openai
import connectors_gmail
connectors_gmail.get_gmail_messages = lambda *a, **k: []
import agent_gmail

quote_repaired = agent_gmail._repair_blanket_quoted_json(components_str)
print("Quote-repaired (first 300 chars):", quote_repaired[:300])
print()
dict_closed = _insert_missing_dict_closers(quote_repaired)
print("Dict-closed (first 400 chars):", dict_closed[:400])
print()
try:
    parsed = json.loads(dict_closed)
    print("PARSED OK. Row count:", len(parsed[0]["rows"]))
    for r in parsed[0]["rows"]:
        print(" -", r["name"][:60])
except Exception as e:
    print("PARSE FAILED:", e)
    print(dict_closed[:600])
