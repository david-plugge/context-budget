#!/usr/bin/env python3
"""Codex desktop/CLI context budget hook. No third-party dependencies."""

import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memory.bridge import capture as capture_memory, context as memory_context
from memory.history import capture as capture_history, context as history_context, capture_lookup_event


def setting(name, default):
    try:
        value = int(os.environ[f"CONTEXT_BUDGET_{name}"])
        return value if value > 0 else default
    except (KeyError, ValueError):
        return default


DATA = Path(os.environ.get("PLUGIN_DATA") or Path.home() / ".codex" / "context-budget")
HANDOFF_DIR = Path(tempfile.gettempdir()) / "codex-context-budget"
MAX_BLOCKS = 4
REMIND_EVERY = 15_000


def transcript_usage(path):
    """Read latest Codex usage sample; rollout JSONL is useful but not a stable API."""
    if not path:
        return 0, 0
    tokens = window = 0
    try:
        with open(path, encoding="utf-8") as stream:
            for line in stream:
                try:
                    item = json.loads(line)
                    if item.get("type") != "event_msg":
                        continue
                    payload = item.get("payload") or {}
                    if payload.get("type") != "token_count":
                        continue
                    info = payload.get("info") or {}
                    usage = info.get("last_token_usage") or {}
                    tokens = int(usage.get("input_tokens") or 0) + int(
                        usage.get("output_tokens") or 0
                    )
                    window = int(info.get("model_context_window") or 0)
                except (ValueError, TypeError, AttributeError):
                    continue
    except OSError:
        pass
    return tokens, window


def paths(event):
    session = re.sub(r"[^A-Za-z0-9_-]", "_", str(event.get("session_id") or "unknown"))
    return DATA / f"{session}.json", HANDOFF_DIR / f"{session}.handoff.md"


def read_state(path):
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, ValueError):
        return {}


def save_state(path, state):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(path)


def emit(event, context=None, **fields):
    if context:
        fields["hookSpecificOutput"] = {
            "hookEventName": event,
            "additionalContext": context,
        }
    print(json.dumps(fields))


def thresholds(window):
    # Defaults scale down for smaller model windows. Explicit values remain exact.
    if not window:
        window = 250_000
    soft = setting("SOFT", min(100_000, int(window * 0.40)))
    firm = setting("FIRM", min(150_000, int(window * 0.60)))
    hard = setting("HARD", min(200_000, int(window * 0.80)))
    return soft, firm, hard


def hint(event, state_path, handoff):
    tokens, window = transcript_usage(event.get("transcript_path"))
    soft, firm, _ = thresholds(window)
    if tokens < soft or (handoff.exists() and handoff.stat().st_size):
        return emit(event["hook_event_name"])
    state = read_state(state_path)
    stage = "firm" if tokens >= firm else "soft"
    if (
        stage == state.get("hint_stage")
        and tokens - state.get("hint_tokens", 0) < REMIND_EVERY
    ):
        return emit(event["hook_event_name"])
    state.update(hint_stage=stage, hint_tokens=tokens)
    save_state(state_path, state)
    urgency = (
        "Choose the next safe break, then write the handoff soon."
        if stage == "firm"
        else "At the next clean break, choose whether to prepare a compact."
    )
    message = (
        f"[Context budget: about {tokens:,} of {window:,} tokens. {urgency}] "
        f"Write a concise handoff to {handoff} with the goal, completed work, "
        "verified facts, decisions, next steps, and unresolved risks. "
        "The next automatic compact can proceed once that file is nonempty. "
        "Do not treat this reminder as a request to stop the user's task."
    )
    return emit(event["hook_event_name"], context=message)


def precompact(event, state_path, handoff):
    tokens, window = transcript_usage(event.get("transcript_path"))
    _, _, hard = thresholds(window)
    state = read_state(state_path)
    if handoff.exists() and handoff.stat().st_size:
        state["blocks"] = 0
        save_state(state_path, state)
        return emit("PreCompact")
    blocks = state.get("blocks", 0)
    if not tokens or tokens >= hard or blocks >= MAX_BLOCKS:
        return emit("PreCompact")
    state["blocks"] = blocks + 1
    save_state(state_path, state)
    return emit(
        "PreCompact",
        **{"continue": False},
        stopReason="Waiting for the agent's context handoff",
        systemMessage=(
            f"Context budget deferred automatic compaction ({tokens:,} tokens, "
            f"attempt {blocks + 1}/{MAX_BLOCKS}). Handoff: {handoff}"
        ),
    )


def restore(event, state_path, handoff):
    try:
        content = handoff.read_text(encoding="utf-8").strip()
    except OSError:
        content = ""
    memory = memory_context(event, "codex")
    history = history_context(event, "codex")
    if not content and not memory and not history:
        return emit("SessionStart")
    if content:
        handoff.replace(handoff.with_suffix(".consumed"))
    save_state(state_path, {})
    return emit(
        "SessionStart",
        context="\n\n".join(
            part for part in (
                "[Agent-written handoff from before compaction]\n\n" + content if content else "",
                memory,
                history,
            ) if part
        ),
    )


def main():
    try:
        event = json.load(sys.stdin)
    except (ValueError, TypeError):
        event = {}
    name = event.get("hook_event_name")
    if name == "PostToolUse":
        try:
            capture_lookup_event(event, "codex")
        except (OSError, ValueError, TypeError):
            pass  # Telemetry must never interrupt the budget hook.
    if name in ("Stop", "PreCompact"):
        try:
            capture_history(event, "codex")
        except (OSError, ValueError, TypeError):
            pass  # History lookup must not interrupt compaction.
        try:
            capture_memory(event, "codex")
        except (OSError, ValueError, TypeError):
            pass  # Memory is experimental; never interrupt the budget gate.
    state_path, handoff = paths(event)
    if name in ("PostToolUse", "Stop"):
        hint(event, state_path, handoff)
    elif name == "PreCompact":
        precompact(event, state_path, handoff)
    elif name == "SessionStart" and event.get("source") == "compact":
        restore(event, state_path, handoff)
    else:
        emit(name or "SessionStart")


if __name__ == "__main__":
    main()
