from __future__ import annotations

import sqlite3
from typing import Any

from .db import dumps, loads, value_from_storage
from .eventsys import scan_and_fire
from .executor import advance_tick, append_verdict_log, apply_deltas
from .filtering import filter_action, sanitize_display_text
from .judge import adjudicate_action
from .llm import chat_json
from .prompts import build_character_prompt
from .scheduler import (
    conversation_prompt_slice,
    current_or_open_scene,
    finish_turn,
    pick_next_actor,
    record_scene_turn,
    scene_prompt_slice,
)
from .threads import active_thread_slice, record_thread_turn, thread_situation


def list_present_character_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT id FROM characters ORDER BY rowid").fetchall()
    return [row["id"] for row in rows]


def run_character_action(
    conn: sqlite3.Connection,
    char_id: str,
    situation: str,
    scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    character = conn.execute(
        "SELECT id, name, summary FROM characters WHERE id = ?",
        (char_id,),
    ).fetchone()
    if character is None:
        raise ValueError("Character not found")

    attrs = _character_attributes(conn, char_id)
    traits = _character_traits(conn, char_id)
    scene_slice = scene_prompt_slice(conn, scene) if scene is not None else None
    conversation_state = conversation_prompt_slice(conn, scene) if scene is not None else {"topic": "", "last_turn": None}
    present = list(scene["participants"]) if scene is not None else list_present_character_ids(conn)
    present_characters = _present_characters(conn, present)
    actor_relationships = _actor_relationships(conn, char_id, present)
    recent = _recent_log(conn, int(scene["id"]) if scene is not None else None)
    active_threads = active_thread_slice(conn, scene, actor_id=char_id)
    system, user = build_character_prompt(
        actor=dict(character),
        actor_attributes=attrs,
        actor_traits=traits,
        actor_relationships=actor_relationships,
        scene=scene_slice,
        conversation_state=conversation_state,
        present_characters=present_characters,
        recent_scene_log=recent,
        active_story_threads=active_threads,
        situation=situation,
    )
    result = chat_json(system=system, user=user, temperature=0.85, max_tokens=1024)
    action, warnings = filter_action(result["parsed"], set(present))
    usage = result["usage"]
    log_id = append_action_log(
        conn,
        char_id,
        character["name"],
        action,
        usage,
        warnings,
        int(scene["id"]) if scene is not None else None,
    )
    thread_update = None
    if scene is not None:
        thread_update = record_thread_turn(
            conn,
            scene=scene,
            actor_id=char_id,
            action=action,
            log_id=log_id,
        )
    if scene is not None:
        record_scene_turn(
            conn,
            scene=scene,
            actor_id=char_id,
            content=str(action["speech"]),
            action=action,
        )
    return {
        "log_id": log_id,
        "actor_id": char_id,
        "actor_name": character["name"],
        "action": action,
        "warnings": warnings,
        "usage": usage,
        "thread_update": thread_update,
    }

def step_once(conn: sqlite3.Connection, situation: str | None = None) -> dict[str, Any]:
    tick = advance_tick(conn)
    scene = current_or_open_scene(conn)
    events = scan_and_fire(conn, scene)
    actor_id = pick_next_actor(conn, scene)
    active_threads = active_thread_slice(conn, scene, actor_id=actor_id, limit=1)
    result = run_character_action(
        conn,
        actor_id,
        situation or _turn_situation(events, active_threads) or "来生酒吧进入夜间高峰，每个人都在等待机会或者躲避麻烦。是时候采取行动了。",
        scene,
    )
    verdict = adjudicate_action(conn, actor_id=actor_id, action=result["action"], scene=scene)
    applied = apply_deltas(conn, verdict["deltas"])
    verdict_log_id = append_verdict_log(
        conn,
        "JUDGE",
        summarize_applied(applied, conn),
        applied,
        int(scene["id"]),
    )
    verdict = {
        "log_id": verdict_log_id,
        "success": verdict["success"],
        "narration": verdict["narration"],
        "applied": applied,
        "usage": verdict["usage"],
    }
    updated_scene = finish_turn(conn, int(scene["id"]))
    return {"tick": tick, "scene": updated_scene, "events": events, **result, "verdict": verdict}


def _event_situation(events: list[dict[str, Any]]) -> str | None:
    if not events:
        return None
    latest = events[-1]
    return f"刚发生事件：{latest['narration']} 轮到你自然回应一句或做一个动作。"


def _turn_situation(events: list[dict[str, Any]], active_threads: list[dict[str, Any]]) -> str | None:
    thread_text = thread_situation(active_threads)
    if thread_text:
        ambient = _ambient_event_situation(events)
        if ambient:
            return f"{thread_text} 同时发生了环境小事：{ambient} 可以轻轻带过，但不要打断当前剧情线。"
        return thread_text
    return _event_situation(events)


def _ambient_event_situation(events: list[dict[str, Any]]) -> str | None:
    if not events:
        return None
    latest = events[-1]
    if latest.get("thread_id") is not None:
        return None
    return str(latest.get("narration") or "")


def summarize_applied(applied: list[dict[str, Any]], conn: sqlite3.Connection | None = None) -> str:
    successful = [
        item
        for item in applied
        if item.get("status") == "applied" and item.get("old") != item.get("new")
    ]
    if not successful:
        return "裁定完成，本拍无数值变化。"
    parts: list[str] = []
    name_cache = _character_name_map(conn) if conn is not None else {}
    for item in successful[:3]:
        field = _field_label(str(item.get("field", "")))
        ref = str(item.get("ref", ""))
        owner = name_cache.get(ref, ref)
        old = item.get("old")
        new = item.get("new")
        parts.append(f"{owner}{field} {old}→{new}")
    return "裁定生效：" + "，".join(parts) + "。"


def _character_name_map(conn: sqlite3.Connection | None) -> dict[str, str]:
    if conn is None:
        return {}
    rows = conn.execute("SELECT id, name FROM characters").fetchall()
    return {row["id"]: row["name"] for row in rows}


def _field_label(field: str) -> str:
    return {
        "mood": "心情",
        "energy": "精力",
        "health": "健康",
        "money": "金钱",
        "tension": "紧张度",
        "economy_index": "经济",
    }.get(field, field)


def append_action_log(
    conn: sqlite3.Connection,
    char_id: str,
    char_name: str,
    action: dict[str, Any],
    usage: dict[str, Any],
    warnings: list[str],
    scene_id: int | None = None,
) -> int:
    speech = str(action.get("speech") or "")
    content = sanitize_display_text(speech, "")
    if not content:
        raise ValueError(f"角色模型没有返回可用台词: {char_name}")
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = world["sim_tick"] if world else 0
    cursor = conn.execute(
        """
        INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
        VALUES (?, ?, ?, 'speech', ?, ?, 'all')
        """,
        (
            scene_id,
            tick,
            char_id,
            content,
            dumps(
                {
                    "action": action,
                    "usage": usage,
                    "warnings": warnings,
                    "heard_by": _scene_participants(conn, scene_id),
                }
            ),
        ),
    )
    return int(cursor.lastrowid)


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


def _character_traits(conn: sqlite3.Connection, char_id: str) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT td.label, ct.score
        FROM character_traits ct
        JOIN trait_defs td ON td.id = ct.trait_id
        WHERE ct.char_id = ?
        ORDER BY td.id
        """,
        (char_id,),
    ).fetchall()
    return {row["label"]: row["score"] for row in rows}


def _actor_relationships(
    conn: sqlite3.Connection,
    actor_id: str,
    present_ids: list[str],
) -> list[dict[str, Any]]:
    target_ids = [char_id for char_id in present_ids if char_id != actor_id]
    if not target_ids:
        return []
    placeholders = ",".join("?" for _ in target_ids)
    rows = conn.execute(
        f"""
        SELECT c.id, c.name, r.affection, r.trust, r.respect, r.familiarity
        FROM characters c
        JOIN relationships r ON r.from_id = ? AND r.to_id = c.id
        WHERE c.id IN ({placeholders})
        ORDER BY c.rowid
        """,
        [actor_id, *target_ids],
    ).fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "affection": row["affection"] or 0,
            "trust": row["trust"] or 0,
            "respect": row["respect"] or 0,
            "familiarity": row["familiarity"] or 0,
        }
        for row in rows
    ]


def _present_characters(conn: sqlite3.Connection, ids: list[str]) -> list[dict[str, Any]]:
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
    return [dict(row) for row in rows]


def _recent_log(conn: sqlite3.Connection, scene_id: int | None = None) -> list[dict[str, Any]]:
    if scene_id is None:
        rows = conn.execute(
            """
            SELECT tick, actor_id, type, content, data
            FROM scene_log
            ORDER BY id DESC
            LIMIT 8
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT tick, actor_id, type, content, data
            FROM scene_log
            WHERE scene_id = ? OR scene_id IS NULL
            ORDER BY id DESC
            LIMIT 8
            """,
            (scene_id,),
        ).fetchall()
    return [_prompt_log_entry(row) for row in reversed(rows)]


def _scene_participants(conn: sqlite3.Connection, scene_id: int | None) -> list[str]:
    if scene_id is None:
        return []
    row = conn.execute("SELECT participants FROM scenes WHERE id = ?", (scene_id,)).fetchone()
    return loads(row["participants"], []) if row is not None else []


def _prompt_log_entry(row: sqlite3.Row) -> dict[str, Any]:
    data = loads(row["data"], {})
    action = data.get("action") if isinstance(data, dict) else {}
    if not isinstance(action, dict):
        action = {}
    proposed = action.get("action")
    return {
        "tick": row["tick"],
        "actor_id": row["actor_id"],
        "type": row["type"],
        "content": row["content"],
        "to": action.get("to"),
        "topic": action.get("topic", ""),
        "action_target": proposed.get("target") if isinstance(proposed, dict) else None,
        "heard_by": data.get("heard_by", []) if isinstance(data, dict) else [],
    }
