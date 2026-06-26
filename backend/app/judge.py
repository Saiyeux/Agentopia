from __future__ import annotations

import sqlite3
from typing import Any

from .db import value_from_storage
from .executor import Delta
from .filtering import sanitize_display_text
from .llm import chat_json_schema
from .prompts import build_judge_prompt
from .scheduler import scene_prompt_slice


ALLOWED_CHARACTER_FIELDS = {"mood", "energy", "health", "money"}
ALLOWED_OPS = {"add", "set"}


def verdict_schema(character_ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean"},
            "narration": {"type": "string"},
            "deltas": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "enum": ["character_attribute", "relationship"]},
                        "source": {"type": "string", "enum": character_ids},
                        "ref": {"type": "string", "enum": character_ids},
                        "field": {
                            "type": "string",
                            "enum": ["mood", "energy", "health", "money"],
                        },
                        "op": {"type": "string", "enum": ["add", "set"]},
                        "value": {"type": "integer", "minimum": -5, "maximum": 5},
                        "reason": {"type": "string"},
                    },
                    "required": ["target", "ref", "field", "op", "value", "reason"],
                    "additionalProperties": False,
                },
                "maxItems": 5,
            },
        },
        "required": ["success", "narration", "deltas"],
        "additionalProperties": False,
    }


def adjudicate_action(
    conn: sqlite3.Connection,
    *,
    actor_id: str,
    action: dict[str, Any],
    scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    actor = conn.execute(
        "SELECT id, name, summary FROM characters WHERE id = ?",
        (actor_id,),
    ).fetchone()
    if actor is None:
        raise ValueError("Actor not found")

    present_ids = list(scene["participants"]) if scene is not None else _all_character_ids(conn)
    if actor_id not in present_ids:
        present_ids.append(actor_id)
    scene_slice = scene_prompt_slice(conn, scene) if scene is not None else None
    present_characters = _present_character_slices(conn, present_ids)
    system, user = build_judge_prompt(
        actor={
            "id": actor["id"],
            "name": actor["name"],
            "summary": actor["summary"],
            "attributes": _character_attributes(conn, actor_id),
        },
        turn=action,
        scene=scene_slice,
        present_characters=present_characters,
        recent_scene_log=_recent_log(conn, int(scene["id"]) if scene is not None else None),
    )
    try:
        result = chat_json_schema(
            schema_name="agentopia_verdict",
            schema=verdict_schema(present_ids),
            system=system,
            user=user,
            temperature=0.2,
            max_tokens=512,
        )
    except Exception as exc:
        return {
            "success": False,
            "narration": "裁定完成，本拍无数值变化。",
            "deltas": [],
            "raw_deltas": [],
            "usage": {},
        }
    verdict = result["parsed"]
    direct_targets = _direct_targets(action, set(present_ids))
    deltas = [
        delta
        for item in verdict.get("deltas", [])
        if (delta := _to_delta(item, set(present_ids), actor_id, direct_targets)) is not None
    ]
    return {
        "success": bool(verdict.get("success")),
        "narration": _safe_narration(str(verdict.get("narration") or "")),
        "deltas": deltas,
        "raw_deltas": verdict.get("deltas", []),
        "usage": result["usage"],
    }


def _to_delta(
    item: dict[str, Any],
    present_ids: set[str],
    actor_id: str,
    direct_targets: set[str],
) -> Delta | None:
    target = item.get("target")
    ref = item.get("ref")
    field = item.get("field")
    op = item.get("op")
    if op not in ALLOWED_OPS:
        return None
    if target == "character_attribute":
        if ref not in present_ids or field not in ALLOWED_CHARACTER_FIELDS:
            return None
    elif target == "relationship":
        source = item.get("source")
        if source != actor_id or ref not in direct_targets:
            return None
        if field not in {"affection", "trust", "respect", "familiarity"}:
            return None
    else:
        return None
    return Delta(
        target=target,
        ref=ref,
        field=field,
        op=op,
        value=item["value"],
        reason=item.get("reason", ""),
        source=item.get("source") if target == "relationship" else None,
    )


def _direct_targets(action: dict[str, Any], present_ids: set[str]) -> set[str]:
    targets: set[str] = set()
    spoken_to = action.get("to")
    if spoken_to in present_ids:
        targets.add(spoken_to)
    proposed = action.get("action")
    if isinstance(proposed, dict):
        target = proposed.get("target")
        if target in present_ids:
            targets.add(target)
    return targets


def _safe_narration(text: str) -> str:
    return sanitize_display_text(text, "裁定完成，本拍无数值变化。")


def _character_attributes(conn: sqlite3.Connection, char_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT ad.id, ad.value_type, ca.value
        FROM character_attributes ca
        JOIN attribute_defs ad ON ad.id = ca.attr_id
        WHERE ca.char_id = ?
        ORDER BY ad.id
        """,
        (char_id,),
    ).fetchall()
    return {row["id"]: value_from_storage(row["value_type"], row["value"]) for row in rows}


def _all_character_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT id FROM characters ORDER BY rowid").fetchall()
    return [row["id"] for row in rows]


def _present_character_slices(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT id, name, summary
        FROM characters
        WHERE id IN ({placeholders})
        ORDER BY rowid
        """,
        ids,
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "summary": row["summary"],
            "attributes": _character_attributes(conn, row["id"]),
        }
        for row in rows
    ]


def _recent_log(conn: sqlite3.Connection, scene_id: int | None = None) -> list[dict[str, Any]]:
    if scene_id is None:
        rows = conn.execute(
            """
            SELECT tick, actor_id, type, content
            FROM scene_log
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT tick, actor_id, type, content
            FROM scene_log
            WHERE scene_id = ? OR scene_id IS NULL
            ORDER BY id DESC
            LIMIT 8
            """,
            (scene_id,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]
