"""
Shared Claude Messages-API engine for Pilant Studio's Composition Engine
connectors — built 2026-08-26, the first step of the NVIDIA (llama-3.1-8b)
-> Claude migration scoped earlier the same day (see the delivered
claude-migration-scope.md for the full reasoning on what's provider-specific
vs. shared, and what gets deleted vs. kept).

Every connector under the old NVIDIA setup (agent_gmail.py, agent_slack.py,
agent_github.py, agent_helpdesk.py, agent_healthcare.py, agent_custom.py)
duplicated an almost-identical bounded tool-calling loop — differing only in
its TOOLS list, SYSTEM prompt, and per-tool handling/guardrails — because
each one also had to independently re-implement JSON-repair, quote-escaping
recovery, and plain-text-tool-call recovery to survive that model. Claude's
tool_use blocks arrive as an ALREADY-PARSED, schema-validated object (see
ToolUseBlock.input: Dict[str, object] in the SDK's own types) — there is no
raw string to repair, so that whole category of workaround doesn't port
here at all. This module factors the loop out ONCE, in Claude's native
format, so each connector shrinks to just its own domain logic (tools,
system prompt, guardrails).

CONTRACT PRESERVED from every existing connector's run_agent(), unchanged,
so studio.py's CONNECTORS registry and _run_agent_for_workflow() need ZERO
changes to call a Claude-backed connector exactly like an NVIDIA-backed one:

    run_agent(user_request=None, max_steps=8, verbose=True, messages=None, fetched_data=False)
    -> {"clarify": str, "messages": [...], "fetched_data": bool}   # ask_user accepted
     | {"render": dict}                                            # render_view accepted
     | {"text": str}                                                # plain conversational reply
     | {"error": str}                                               # gave up

IMPORTANT — the `messages` shape stored in this return value (and round-
tripped back in by studio.py on resume) is Claude's own shape now (assistant
turns carry a list of content blocks, tool results are role="user" messages
with tool_result blocks), NOT the old OpenAI tool_calls/role="tool" shape.
studio.py never inspects this shape directly — it only ever appends one
`{"role": "user", "content": text}` onto it, which is valid under Claude's
Messages API too (plain string content is allowed on any turn, mixed freely
with content-block turns) — so this is a safe, transparent swap from
studio.py's point of view.

THE ONE REAL GOTCHA (see the migration scope doc's section of the same
name): studio.py's cross-request "memory" is seeded as
`[{"role": "system", "content": SYSTEM}, ...]`, and Claude's API does not
accept `role: "system"` inside `messages` at all — the system prompt is its
own separate top-level argument. _split_system() below strips a leading
system-role message out of whatever `messages=` a connector is resumed
with and returns it as the `system` argument instead, transparently, on
every call — connectors and studio.py never have to know this happened.
"""

import os
import sys
import time
from pathlib import Path

import anthropic

env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

client = anthropic.Anthropic()

# claude-haiku-4-5 is the right default for this app's actual task shape
# (read a handful of structured records, fill a fixed UI schema, ask at
# most one clarifying question) — see claude-migration-scope.md's "Model
# choice" section. A connector can override per-call if one of them turns
# out to need more reasoning (agent_github's identity-scoping logic is the
# most likely candidate).
DEFAULT_MODEL = "claude-haiku-4-5"
DEFAULT_MAX_TOKENS = 4096


def block_get(block, key, default=None):
    """A tool_use/tool_result block is, as of the 2026-08-29 fix documented
    on _run_once's assistant-turn append below, always a plain dict by the
    time anything outside this module's current call stack sees it — but
    this still accepts a real SDK object too (ToolUseBlock, TextBlock, ...)
    defensively, in case a caller ever hands back a conversation this
    module didn't just build (or an older in-flight one from before that
    fix). Callers that need to inspect prior conversation content
    (guardrails like "was ask_user already used") go through this instead
    of assuming one shape."""
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _dump_block(b):
    """Turns one resp.content block into a plain, JSON-safe dict — see
    _run_once's assistant-turn append below for why this exists. Prefers
    the real SDK object's own .model_dump() (a pydantic BaseModel method,
    present on every genuine ToolUseBlock/TextBlock/... this module gets
    back from a real API call), but doesn't require it: several existing
    tests across this codebase stand in a lightweight hand-rolled fake
    block (plain .type/.id/.name/.input/.text attributes, no
    .model_dump()) instead of a real SDK object, and those are just as
    valid a shape to accept here — the whole point of this fallback is
    that this module's behavior shouldn't depend on which one a caller
    happens to be holding. Already a dict (e.g. this module's own
    previously-stored blocks, round-tripped back in on a resume) passes
    through unchanged. Anything else falls back to picking out the fields
    the two block types this loop actually ever needs to store use — text
    or tool_use — rather than guessing at a schema."""
    if isinstance(b, dict):
        return b
    dump = getattr(b, "model_dump", None)
    if callable(dump):
        return dump()
    btype = getattr(b, "type", None)
    if btype == "tool_use":
        return {"type": "tool_use", "id": getattr(b, "id", None),
                "name": getattr(b, "name", None), "input": getattr(b, "input", None)}
    if btype == "text":
        return {"type": "text", "text": getattr(b, "text", "")}
    return {"type": btype}


def original_user_request(messages):
    """The person's first message in this conversation — always a plain
    string (see this module's docstring: the very first user turn, whether
    from a fresh call or from studio.py's memory-seeded/resumed messages,
    is always the literal request text, never a tool_result block list)."""
    for m in messages:
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


# The exact tool-result content a connector should write when accepting an
# ask_user call (see ToolOutcome usage in a connector's dispatch()) — used
# by ask_user_already_used() below to tell a genuinely ACCEPTED ask_user
# call apart from one this reliability layer itself REJECTED. Bug this
# fixes: ported forward from agent_custom.py's 2026-08-25 fix (see its own
# history) — counting every ask_user ATTEMPT as "already used" (instead of
# only an accepted one) meant a legitimately retried second attempt, after
# the first was rejected by any guardrail, got wrongly rejected too.
ASK_USER_ACCEPTED_SENTINEL = "(waiting for the user's answer)"


def ask_user_already_used(messages):
    """True only if a PRIOR ask_user call in this conversation was actually
    ACCEPTED and shown to the person — not merely attempted. Works over
    Claude-shaped messages: finds an assistant tool_use block named
    ask_user, then the LATER tool_result block (matched by tool_use_id)
    that answers it, and only counts it if that tool_result's content is
    exactly ASK_USER_ACCEPTED_SENTINEL — a rejected attempt's tool_result is
    always some other nudge string instead."""
    for i, m in enumerate(messages):
        if m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block_get(block, "type") != "tool_use" or block_get(block, "name") != "ask_user":
                continue
            tc_id = block_get(block, "id")
            for later in messages[i + 1:]:
                if later.get("role") != "user" or not isinstance(later.get("content"), list):
                    continue
                for lb in later["content"]:
                    if block_get(lb, "type") == "tool_result" and block_get(lb, "tool_use_id") == tc_id:
                        return block_get(lb, "content") == ASK_USER_ACCEPTED_SENTINEL
    return False


class ToolOutcome:
    """What a connector's dispatch() callback returns for ONE tool_use
    block:
      - tool_result=<str>   The normal, non-terminal case: this string is
                             appended as this tool_use's tool_result, and
                             the loop continues to the next step (e.g. a
                             rejection nudge, or fetched real data).
      - final=<result dict> Terminates the whole call, returning this dict
                             immediately — matching one of the shapes in
                             this module's docstring (e.g. {"render": ...}).
      - BOTH set             For an ACCEPTED ask_user call specifically: the
                             engine still appends tool_result (normally
                             ASK_USER_ACCEPTED_SENTINEL) for this block
                             before returning, so the returned/stored
                             conversation correctly shows it was accepted,
                             not just attempted — see ask_user_already_used()
                             above for why that distinction matters. Use
                             final["messages"] = NEEDS_CONVERSATION as a
                             placeholder; the engine fills in the real,
                             fully-up-to-date conversation object there
                             right before returning, since dispatch() itself
                             only ever sees the conversation as it stood
                             BEFORE this turn (prior_messages)."""
    __slots__ = ("final", "tool_result")

    def __init__(self, final=None, tool_result=None):
        assert final is not None or tool_result is not None, "set at least one of final/tool_result"
        self.final = final
        self.tool_result = tool_result


# Placeholder a connector's dispatch() can put at final["messages"] when
# accepting an ask_user call — see ToolOutcome's docstring above. The engine
# swaps this for the real, fully up-to-date conversation object right
# before returning.
NEEDS_CONVERSATION = object()


def run_claude_agent(
    dispatch,
    system,
    tools,
    *,
    model=DEFAULT_MODEL,
    max_tokens=DEFAULT_MAX_TOKENS,
    max_steps=8,
    verbose=True,
    messages=None,
    user_request=None,
    fetched_data=False,
    no_tool_call_nudge="You must call one of the available tools — do not respond in plain text.",
    on_no_tool_call=None,
    force_tool_choice=True,
):
    """
    Generic bounded tool-calling loop against Claude's Messages API. Every
    connector's run_agent() should be a thin wrapper that just calls this
    with its own tools/system/dispatch — see agent_custom.py for the
    reference port.

    force_tool_choice: True (the default) sends tool_choice={"type": "any"}
        — the model MUST call a tool every step, matching agent_custom.py's
        old tool_choice="required" behavior (it never has a legitimate
        plain-text reply). Set False for a connector like agent_gmail.py
        that supports genuine conversational replies ("thanks!", "what can
        you do?") alongside data requests — Claude then decides for itself
        whether to call a tool or just reply in text, and on_no_tool_call
        receives that text as a real answer rather than something to nudge
        away. This replaces the old NVIDIA version's brittle
        "looks_like_malformed_tool_call" text-sniffing heuristic entirely —
        Claude's tool_use blocks are structurally unambiguous, so there's
        no more guessing whether plain text was a dodged tool call or a
        genuine reply.

    dispatch(name, args, tc_id, prior_messages, fetched_data) -> ToolOutcome
        Called once per tool_use block the model emits, in the order the
        model emitted them. `args` is block.input — ALREADY a parsed,
        schema-validated dict, no JSON parsing needed. `prior_messages` is
        the conversation BEFORE this turn's assistant message (so a
        guardrail like ask_user_already_used sees only genuinely prior
        history, matching every existing connector's `messages[:-1]`
        convention). `fetched_data` is passed through read-only, exactly as
        given to run_claude_agent — a connector with a real fetch tool
        tracks whether it's been called via its own closure state (see
        agent_gmail.py's eventual port) since a plain callable can't mutate
        a caller's local by reference in Python.

    on_no_tool_call(text, stop_reason) -> ToolOutcome | None
        Optional. Called when a step produces no tool_use block at all. If
        it returns a ToolOutcome with .final set, that's returned as this
        call's result (e.g. a genuine conversational {"text": ...} reply,
        for connectors that support one). If it returns a ToolOutcome with
        .tool_result set, that string is used as the nudge appended before
        retrying. If it returns None, or the hook itself is None, falls
        back to no_tool_call_nudge. agent_custom.py leaves this unset — it
        has no conversational-reply mode, so every plain-text response gets
        nudged back toward calling a tool.

    Retries the WHOLE request exactly once on a network-level failure only
    (never on a rejected/invalid tool call) — same policy every existing
    connector's run_agent() already used against NVIDIA.
    """
    result = _run_once(dispatch, system, tools, model, max_tokens, max_steps,
                        verbose, messages, user_request, fetched_data,
                        no_tool_call_nudge, on_no_tool_call, force_tool_choice)
    if isinstance(result, dict) and result.get("_network_error"):
        if verbose:
            print("  [retry] first attempt failed on a network error — retrying the whole request once", file=sys.stderr)
        result = _run_once(dispatch, system, tools, model, max_tokens, max_steps,
                            verbose, messages, user_request, fetched_data,
                            no_tool_call_nudge, on_no_tool_call, force_tool_choice)
    if isinstance(result, dict):
        result.pop("_network_error", None)
    return result


def _split_system(messages, fallback_system):
    """See module docstring's 'ONE REAL GOTCHA' section."""
    if messages and messages[0].get("role") == "system":
        return messages[0]["content"], list(messages[1:])
    return fallback_system, list(messages) if messages else []


def _run_once(dispatch, default_system, tools, model, max_tokens, max_steps,
               verbose, messages, user_request, fetched_data,
               no_tool_call_nudge, on_no_tool_call, force_tool_choice):
    if messages is None:
        system = default_system
        conversation = [{"role": "user", "content": user_request}]
    else:
        system, conversation = _split_system(messages, default_system)

    for step in range(max_steps):
        if verbose:
            print(f"  [step {step+1}] calling model...", file=sys.stderr)
        step_start = time.time()
        try:
            if force_tool_choice:
                try:
                    resp = client.messages.create(
                        model=model, max_tokens=max_tokens, system=system,
                        tools=tools, tool_choice={"type": "any"}, messages=conversation,
                    )
                except Exception:
                    # Belt-and-braces, mirroring agent.py's existing fallback —
                    # not every model/account combination is guaranteed to
                    # honor tool_choice="any" identically; retry once without
                    # forcing a tool before treating this as a real failure.
                    resp = client.messages.create(
                        model=model, max_tokens=max_tokens, system=system,
                        tools=tools, messages=conversation,
                    )
            else:
                # Let Claude decide for itself whether to call a tool or
                # reply in plain text — see force_tool_choice's docstring.
                resp = client.messages.create(
                    model=model, max_tokens=max_tokens, system=system,
                    tools=tools, tool_choice={"type": "auto"}, messages=conversation,
                )
        except Exception as e:
            elapsed = time.time() - step_start
            if verbose:
                print(f"  [step {step+1}] model call failed after {elapsed:.1f}s: {type(e).__name__}: {e}", file=sys.stderr)
            return {
                "_network_error": True,
                "error": (
                    f"couldn't reach the model ({type(e).__name__}) after {elapsed:.0f}s — "
                    "this is a network problem talking to Anthropic's API, not the model taking "
                    "a long time to think. Check your connection and try again."
                ),
            }
        if verbose:
            print(f"  [step {step+1}] model call took {time.time()-step_start:.1f}s (stop_reason={resp.stop_reason})", file=sys.stderr)

        # 2026-08-29 fix: store each content block as a plain dict
        # (_dump_block above), not the raw SDK object resp.content itself
        # hands back — found live when agent_healthcare.py's migration to
        # this engine exposed a real bug in healthcare_dashboard.py's
        # Copilot panel: unlike studio.py (which keeps every workflow's
        # conversation in an in-memory dict, WORKFLOWS, never serialized),
        # that panel stores an in-progress ask_user conversation in a real
        # Flask session — a signed COOKIE, JSON-serialized by Flask's
        # default itsdangerous backend. A raw ToolUseBlock/TextBlock
        # instance sitting inside it blows up with "Object of type
        # TextBlock is not JSON serializable" the moment a clarify
        # round-trip tries to save the session. A plain dict with the same
        # "type"/"name"/"input"/"id"/"text" keys is both fully
        # JSON-safe AND exactly what Claude's Messages API itself accepts
        # as message content on the next call (a dict-shaped content
        # block is standard, documented usage, not a workaround) — so this
        # is a pure representation fix, not a behavior change. `resp.content`
        # itself (the original SDK objects, not this dict copy) is still
        # what tool_use_blocks/text extraction below read from, since
        # those only ever look at the CURRENT step, never the stored copy.
        conversation.append({"role": "assistant", "content": [_dump_block(b) for b in resp.content]})
        prior_messages = conversation[:-1]

        tool_use_blocks = [b for b in resp.content if b.type == "tool_use"]

        if not tool_use_blocks:
            text = "".join(b.text for b in resp.content if b.type == "text").strip()
            outcome = on_no_tool_call(text, resp.stop_reason) if on_no_tool_call else None
            if outcome is not None and outcome.final is not None:
                return outcome.final
            nudge = outcome.tool_result if outcome is not None else no_tool_call_nudge
            if verbose:
                print(f"  [step {step+1}] no tool_use block (stop_reason={resp.stop_reason}) — nudging", file=sys.stderr)
            conversation.append({"role": "user", "content": nudge})
            continue

        tool_results = []
        final = None
        for block in tool_use_blocks:
            outcome = dispatch(block.name, block.input, block.id, prior_messages, fetched_data)
            if outcome.tool_result is not None:
                tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": outcome.tool_result})
            if outcome.final is not None:
                final = outcome.final
                break
        if final is not None:
            # See ToolOutcome's docstring on the "BOTH set" case: an accepted
            # ask_user needs its acceptance tool_result recorded in the
            # conversation before it's captured into `final["messages"]`, so
            # a later resume can tell it apart from a rejected attempt.
            if tool_results:
                conversation.append({"role": "user", "content": tool_results})
            if isinstance(final, dict) and final.get("messages") is NEEDS_CONVERSATION:
                final = dict(final)
                final["messages"] = conversation
            return final
        conversation.append({"role": "user", "content": tool_results})

    return {"error": "hit max_steps without a valid render_view"}
