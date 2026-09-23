#!/usr/bin/env python3
"""Staged context budget for Claude Code.

Three stages, driven by the live context size read from the session transcript:

  SOFT  hint   - "compact when you reach a natural break"
  FIRM  hint   - "compact now unless you are mid-step"
  HARD  release - compaction is no longer blocked, it happens

The gate itself is PreCompact: Claude Code's own auto-compact (set
`autoCompactWindow` to SOFT) fires at the soft threshold, and this hook blocks
it until the model has written a handoff file -- i.e. until the model itself
decided this is a good cut. SessionStart(compact) feeds that handoff back in
afterwards, so the model, not the summarizer, decides what survives.
"""

import json
import os
import sys
import tempfile

def _threshold(name, default):
    try:
        return int(os.environ["CONTEXT_BUDGET_%s" % name])
    except (KeyError, ValueError):
        return default


SOFT_TOKENS = _threshold("SOFT", 100_000)
FIRM_TOKENS = _threshold("FIRM", 150_000)
HARD_TOKENS = _threshold("HARD", 200_000)

# Safety net: never block compaction forever, a session that cannot compact
# dies with "Prompt is too long". On a 200k-context model, lower HARD_TOKENS.
MAX_BLOCKED_ATTEMPTS = 8

# A usage record describes the request that already ran, so the request the
# gate is deciding about is one step larger. Release on the projected size.
PROJECT_AHEAD = True

# The PreCompact block is invisible to the model, so the reminder has to come
# through PostToolUse/Stop. Repeat it once the context grew this much further.
REMIND_EVERY_TOKENS = 15_000

STATE_DIR = os.path.expanduser("~/.claude/context-budget")
# The handoff lives outside ~/.claude: Claude Code refuses writes there.
HANDOFF_FALLBACK_DIR = os.path.join(tempfile.gettempdir(), "claude-context-budget")

# The gate only engages if Claude Code's own auto-compact fires at the soft
# threshold, and a plugin cannot ship that setting. So check it and say so.
WINDOW_SETTING = "autoCompactWindow"
WINDOW_ENV = "CLAUDE_CODE_AUTO_COMPACT_WINDOW"


def read_event():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


CHARS_PER_TOKEN = 4


def context_tokens(transcript_path):
    """Current context size.

    The newest usage record describes the request that already ran, so anything
    written to the transcript since then -- tool results above all -- is already
    in context but not yet in any usage figure. Without that tail the reading
    lags by tens of thousands of tokens on a batch of large reads.
    """
    if not transcript_path or not os.path.exists(transcript_path):
        return 0
    measured = 0
    tail_chars = 0
    try:
        with open(transcript_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("isSidechain"):
                    continue
                usage = (entry.get("message") or {}).get("usage") if isinstance(entry.get("message"), dict) else None
                if isinstance(usage, dict):
                    measured = (
                        usage.get("input_tokens", 0)
                        + usage.get("cache_creation_input_tokens", 0)
                        + usage.get("cache_read_input_tokens", 0)
                    )
                    tail_chars = 0
                elif entry.get("type") in ("user", "assistant", "attachment"):
                    tail_chars += len(line)
    except OSError:
        return 0
    return measured + tail_chars // CHARS_PER_TOKEN


def state_path(session_id):
    return os.path.join(STATE_DIR, "%s.json" % session_id)


def load_state(event):
    session_id = event.get("session_id", "unknown")
    path = state_path(session_id)
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, ValueError):
        state = {}

    state.setdefault("hints_fired", [])
    state.setdefault("blocked_attempts", 0)
    state.setdefault("last_hint_tokens", 0)
    state.setdefault("last_seen_tokens", 0)
    if not state.get("handoff_path"):
        scratchpad = event.get("scratchpad_dir")
        if scratchpad:
            state["handoff_path"] = os.path.join(scratchpad, "handoff.md")
        else:
            os.makedirs(HANDOFF_FALLBACK_DIR, exist_ok=True)
            state["handoff_path"] = os.path.join(HANDOFF_FALLBACK_DIR, "%s.handoff.md" % session_id)
    return state


def save_state(event, state):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(state_path(event.get("session_id", "unknown")), "w", encoding="utf-8") as fh:
        json.dump(state, fh)


def emit(event_name, context=None, message=None):
    payload = {"hookEventName": event_name}
    if context:
        payload["additionalContext"] = context
    if message:
        payload["systemMessage"] = message
    print(json.dumps({"hookSpecificOutput": payload}))
    sys.exit(0)


def passthrough():
    print("{}")
    sys.exit(0)


def handoff_brief(handoff_path):
    return (
        "To do that, write a handoff file to %s. It is the only thing you carry "
        "across the compaction yourself: the automatic summary is out of your "
        "control. Record the goal, the current state, verified facts (file "
        "paths, line numbers, measurements, decisions and why they were made), "
        "what comes next, and which dead ends are already ruled out. Not a "
        "recap of the conversation, but what a fresh agent needs in order to "
        "continue without asking anything." % handoff_path
    )


def handle_threshold_hint(event):
    """PostToolUse / Stop: the only channel that actually reaches the model.

    Each stage speaks up once, and keeps reminding while the gate is holding a
    compaction back with no handoff in sight.
    """
    state = load_state(event)
    tokens = context_tokens(event.get("transcript_path"))
    handoff_path = state["handoff_path"]

    if os.path.exists(handoff_path):
        passthrough()

    stage = "firm" if tokens >= FIRM_TOKENS else "soft" if tokens >= SOFT_TOKENS else None
    if stage is None:
        passthrough()

    new_stage = stage not in state["hints_fired"]
    waiting = state["blocked_attempts"] > 0
    grew = tokens - state["last_hint_tokens"] >= REMIND_EVERY_TOKENS
    if not new_stage and not (waiting and grew):
        passthrough()

    if stage == "firm":
        context = (
            "[Context budget: %dk tokens used - stage 2 of 3]\n"
            "The context is now large enough that answer quality measurably "
            "suffers. Compact at the next possible point, not once everything "
            "is finished. If you are in the middle of an indivisible step, "
            "finish that step and cut immediately after.\n%s\n"
            "Once the file exists, carry on as usual: the compaction releases "
            "itself as soon as the handoff is there."
            % (tokens // 1000, handoff_brief(handoff_path))
        )
        message = "Context budget stage 2/3: %dk tokens - compaction expected soon" % (tokens // 1000)
    else:
        context = (
            "[Context budget: %dk tokens used - stage 1 of 3]\n"
            "A cut is worth making from here on. No pressure: keep working "
            "until you reach a clean stopping point - a finished sub-task, a "
            "green test run, a completed research step. Compact exactly "
            "then.\n%s"
            % (tokens // 1000, handoff_brief(handoff_path))
        )
        message = "Context budget stage 1/3: %dk tokens - compact at the next sensible break" % (tokens // 1000)

    if new_stage:
        state["hints_fired"].append(stage)
    state["last_hint_tokens"] = tokens
    save_state(event, state)
    emit(event.get("hook_event_name", "PostToolUse"), context=context, message=message)


def handle_pre_compact(event):
    """Gate: hold the compaction back until the model picked the cut."""
    state = load_state(event)
    handoff_path = state["handoff_path"]
    tokens = context_tokens(event.get("transcript_path"))

    if event.get("trigger") == "manual":
        passthrough()

    if os.path.exists(handoff_path) and os.path.getsize(handoff_path) > 0:
        state["blocked_attempts"] = 0
        save_state(event, state)
        emit("PreCompact", message="Compacting at the model's own break (%dk tokens, handoff present)" % (tokens // 1000))

    growth = max(0, tokens - state["last_seen_tokens"]) if PROJECT_AHEAD else 0
    state["last_seen_tokens"] = tokens

    if tokens + growth >= HARD_TOKENS or state["blocked_attempts"] >= MAX_BLOCKED_ATTEMPTS:
        save_state(event, state)
        emit("PreCompact", message="Context budget stage 3/3: hard cut at %dk tokens - compaction forced, no handoff" % (tokens // 1000))

    state["blocked_attempts"] += 1
    save_state(event, state)
    sys.stderr.write(
        "[context-budget] Held back the automatic compaction (%dk tokens, "
        "attempt %d of %d).\n"
        "The model picks the moment: once it is at a clean break it writes the "
        "handoff to %s and the compaction runs by itself. Failing that, it is "
        "forced at %dk tokens and nothing steers what survives.\n"
        % (
            tokens // 1000,
            state["blocked_attempts"],
            MAX_BLOCKED_ATTEMPTS,
            handoff_path,
            HARD_TOKENS // 1000,
        )
    )
    sys.exit(2)


def auto_compact_window_configured(event):
    """Is the gate even reachable? A plugin cannot set the window itself."""
    if os.environ.get(WINDOW_ENV):
        return True
    candidates = [
        os.path.expanduser("~/.claude/settings.json"),
        os.path.join(event.get("cwd", ""), ".claude", "settings.json"),
        os.path.join(event.get("cwd", ""), ".claude", "settings.local.json"),
    ]
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as fh:
                if json.load(fh).get(WINDOW_SETTING):
                    return True
        except (OSError, ValueError):
            continue
    return False


def handle_setup_check(event):
    """Once per session: tell the user when the window is still unset."""
    if auto_compact_window_configured(event):
        passthrough()
    emit(
        "SessionStart",
        message=(
            "context-budget is inactive: %s is not set, so Claude Code only "
            "compacts near the model's limit and the staging never engages. "
            "Run once: /autocompact %dk"
            % (WINDOW_SETTING, (SOFT_TOKENS + 10_000) // 1000)
        ),
    )


def handle_session_start(event):
    """After a compaction: hand the model back the context it chose itself."""
    if event.get("source") != "compact":
        handle_setup_check(event)

    state = load_state(event)
    handoff_path = state["handoff_path"]

    if not os.path.exists(handoff_path):
        passthrough()

    try:
        with open(handoff_path, encoding="utf-8") as fh:
            handoff = fh.read().strip()
    except OSError:
        passthrough()

    if not handoff:
        passthrough()

    os.replace(handoff_path, handoff_path + ".consumed")
    state["hints_fired"] = []
    state["blocked_attempts"] = 0
    save_state(event, state)

    emit(
        "SessionStart",
        context=(
            "[Handoff from before the compaction - written by you, takes "
            "precedence over the automatic summary]\n\n%s" % handoff
        ),
        message="Handoff restored after compaction",
    )


def main():
    event = read_event()
    name = event.get("hook_event_name")
    if name == "PreCompact":
        handle_pre_compact(event)
    elif name == "SessionStart":
        handle_session_start(event)
    elif name in ("PostToolUse", "Stop"):
        handle_threshold_hint(event)
    passthrough()


if __name__ == "__main__":
    main()
