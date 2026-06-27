from __future__ import annotations

from typing import Any


ALLOWED_ACTION_TYPES = {"buy", "give", "attack", "work", "move", "exit", "custom"}
BAD_TEXT_MARKERS = (
    "<start>",
    "analysis",
    "message",
    "JSON",
    '"inner"',
    "'inner'",
    '"action"',
    "'action'",
    "{",
    "}",
    "\\n",
)


def filter_action(action: dict[str, Any], present_ids: set[str]) -> tuple[dict[str, Any], list[str]]:
    """Clamp model ACTION JSON to the current scene's known entities."""
    warnings: list[str] = []
    filtered = {
        "speech": sanitize_display_text(str(action.get("speech") or ""), ""),
        "inner": sanitize_short_text(str(action.get("inner") or "")),
        "action": action.get("action"),
        "to": action.get("to"),
        "topic": sanitize_short_text(str(action.get("topic") or "")),
    }
    if not filtered["speech"]:
        raise ValueError("角色模型没有返回可用 speech")

    if filtered["to"] not in present_ids:
        if filtered["to"] is not None:
            warnings.append(f"invalid to={filtered['to']} cleared")
        filtered["to"] = None

    proposed_action = filtered["action"]
    if proposed_action is None:
        return filtered, warnings

    if not isinstance(proposed_action, dict):
        warnings.append("non-object action cleared")
        filtered["action"] = None
        return filtered, warnings

    action_type = str(proposed_action.get("type") or "custom")
    if action_type not in ALLOWED_ACTION_TYPES:
        warnings.append(f"unknown action type={action_type} converted to custom")
        action_type = "custom"

    target = proposed_action.get("target")
    if target is not None and target not in present_ids:
        warnings.append(f"invalid target={target} cleared")
        target = None

    filtered["action"] = {
        "type": action_type,
        "target": target,
        "detail": sanitize_display_text(str(proposed_action.get("detail") or ""), ""),
    }
    return filtered, warnings


def sanitize_short_text(text: str) -> str:
    return sanitize_display_text(text, "")[:24]


def sanitize_display_text(text: str, fallback: str) -> str:
    cleaned = " ".join(text.replace("\u2028", " ").replace("\u00a0", " ").split())
    for prefix in ("任意：", "你说：", "他说：", "她说："):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
    if not cleaned:
        return fallback
    if cleaned.strip(".。…?!？！ ") == "":
        return fallback
    if any(marker in cleaned for marker in BAD_TEXT_MARKERS):
        return fallback
    weird_chars = sum(1 for char in cleaned if char in {"…", "?", "*", "`", "\\", "|", "<", ">"})
    replacement_chars = cleaned.count("�")
    ascii_letters = sum(1 for char in cleaned if char.isascii() and char.isalpha())
    if replacement_chars > 0:
        return fallback
    if weird_chars > max(2, len(cleaned) // 5):
        return fallback
    if ascii_letters > 18 and len(cleaned) < 80:
        return fallback
    return cleaned[:180]
