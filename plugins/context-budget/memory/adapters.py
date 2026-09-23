"""Normalize visible user/assistant text from Codex and Claude JSONL transcripts.

Both transcript formats are host internals. Unknown records are skipped so a
host update fails without injecting unrelated tool or developer content.
"""

import json
from pathlib import Path


TEXT_TYPES = {"input_text", "output_text", "text"}


def _text(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        part.get("text", "")
        for part in content
        if isinstance(part, dict)
        and part.get("type") in TEXT_TYPES
        and isinstance(part.get("text"), str)
    )


def read_messages(transcript_path, host, *, max_message_chars=4000):
    """Return ordered ``{id, text}`` messages from one host transcript.

    Deliberately excludes tool results, reasoning, developer/system prompts,
    thinking blocks, and non-text payloads. A missing transcript gives [].
    """
    if host not in {"codex", "claude"} or not transcript_path:
        return []
    messages = []
    seen_turn_context = False
    try:
        with Path(transcript_path).open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream):
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict):
                    continue
                if host == "codex":
                    if entry.get("type") == "turn_context":
                        # Codex may serialize startup instructions as user messages
                        # before the first real turn. Keep only turn content.
                        if not seen_turn_context:
                            messages.clear()
                            seen_turn_context = True
                        continue
                    if entry.get("type") != "response_item":
                        continue
                    payload = entry.get("payload")
                    if not isinstance(payload, dict) or payload.get("type") != "message":
                        continue
                    role = payload.get("role")
                    content = payload.get("content")
                    identifier = entry.get("ordinal", line_number)
                else:
                    if entry.get("type") not in {"user", "assistant"} or entry.get("isSidechain"):
                        continue
                    message = entry.get("message")
                    if not isinstance(message, dict):
                        continue
                    role = message.get("role")
                    content = message.get("content")
                    identifier = entry.get("uuid") or line_number
                if role not in {"user", "assistant"}:
                    continue
                text = _text(content)
                if text.strip():
                    messages.append({"id": f"{host}:{identifier}", "text": text if max_message_chars is None else text[:max_message_chars]})
    except OSError:
        return []
    return messages
