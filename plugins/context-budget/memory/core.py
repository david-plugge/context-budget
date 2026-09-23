"""Host-neutral local observational memory for normalized message events.

The core performs no parsing, network access, or model calls. Pass normalized
``{id, text}`` messages from a host adapter; persistent IDs make replay safe.
"""

import hashlib
import json
import os
import tempfile
from pathlib import Path


def _state_path(data_dir, session_id):
    key = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()
    return Path(data_dir) / (key + ".json")


def _load(data_dir, session_id):
    path = _state_path(data_dir, session_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("session_id") == str(session_id):
            return state
    except (OSError, ValueError):
        pass
    return {"session_id": str(session_id), "observations": [], "message_ids": []}


def _save(data_dir, session_id, state):
    path = _state_path(data_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # This directory contains local transcript-derived text and IDs.
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".memory-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def ingest(data_dir, session_id, messages, *, observer=None, max_observations=64):
    """Persist a batch of normalized ``{id, text}`` messages.

    ``messages`` may be the full readable session history on every call. IDs
    are remembered and deduplicated across calls. An optional observer receives
    only the newly accepted chunk and may return one observation string; without
    it, accepted message text is retained verbatim as a prototype observation.
    Returns the number of newly accepted messages.
    """
    state = _load(data_dir, session_id)
    ordered_ids = list(state.get("message_ids") or [])
    known = set(ordered_ids)
    new_messages = []
    for message in messages:
        if not isinstance(message, dict) or message.get("id") is None:
            continue
        message_id = str(message["id"])
        text = message.get("text")
        if message_id in known or not isinstance(text, str) or not text.strip():
            continue
        known.add(message_id)
        ordered_ids.append(message_id)
        new_messages.append({"id": message_id, "text": " ".join(text.split())})

    observations = state.get("observations") or []
    if new_messages:
        if observer is None:
            observations.extend({"text": m["text"]} for m in new_messages)
        else:
            result = observer(new_messages)
            if isinstance(result, str) and result.strip():
                observations.append({"text": " ".join(result.split())})
    # Keep every ID for this prototype: a later full-history replay must never
    # re-add old messages just because a retention window rolled over.
    state["message_ids"] = ordered_ids
    state["observations"] = observations[-max(1, int(max_observations)):]
    _save(data_dir, session_id, state)
    return len(new_messages)


def record_observation(data_dir, session_id, text, *, max_observations=64):
    """Append an externally produced observation, e.g. from a later observer."""
    if not isinstance(text, str) or not text.strip():
        return False
    state = _load(data_dir, session_id)
    state["observations"] = state.get("observations") or []
    state["observations"].append({"text": " ".join(text.split())})
    state["observations"] = state["observations"][-max(1, int(max_observations)):]
    _save(data_dir, session_id, state)
    return True


def render(data_dir, session_id, max_chars=4000):
    """Render newest observations, never exceeding max_chars."""
    if max_chars <= 0:
        return ""
    state = _load(data_dir, session_id)
    rows, used = [], 0
    for item in reversed(state.get("observations") or []):
        row = item.get("text", "")
        remaining = max_chars - used
        if not remaining:
            break
        if len(row) > remaining:
            if not rows:
                rows.append(row[:remaining])
            break
        rows.append(row)
        used += len(row) + 1
    return "\n".join(rows)
