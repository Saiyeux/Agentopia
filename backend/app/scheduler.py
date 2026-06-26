from __future__ import annotations

import sqlite3
from typing import Any

from .db import dumps, loads, set_character_attribute, value_from_storage


DEFAULT_SCENE_BUDGET = 24


def current_or_open_scene(conn: sqlite3.Connection, location_id: str = "hall") -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT *
        FROM scenes
        WHERE status = 'open'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()
    if row is not None:
        return _scene_from_row(row)
    return open_scene(conn, location_id)


def open_scene(conn: sqlite3.Connection, location_id: str = "hall") -> dict[str, Any]:
    location = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    if location is None:
        raise ValueError(f"Location not found: {location_id}")
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    participants = _character_ids_at_location(conn, location_id)
    if not participants:
        participants = [row["id"] for row in conn.execute("SELECT id FROM characters ORDER BY rowid").fetchall()]
    cursor = conn.execute(
        """
        INSERT INTO scenes
          (location_id, title, purpose, start_tick, participants, turn_budget, state)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            location_id,
            location["name"],
            "让当前地点自然发生角色互动与世界事件。",
            tick,
            dumps(participants),
            DEFAULT_SCENE_BUDGET,
            location["state"],
        ),
    )
    scene_id = int(cursor.lastrowid)
    _seed_scene_attributes(conn, scene_id, loads(location["state"], {}))
    conn.execute("INSERT OR IGNORE INTO scene_conversations (scene_id) VALUES (?)", (scene_id,))
    for char_id in participants:
        set_character_attribute(conn, char_id, "location", location_id)
    return current_scene_by_id(conn, scene_id)


def current_scene_by_id(conn: sqlite3.Connection, scene_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)).fetchone()
    if row is None:
        raise ValueError(f"Scene not found: {scene_id}")
    return _scene_from_row(row)


def pick_next_actor(conn: sqlite3.Connection, scene: dict[str, Any]) -> str:
    participants = list(scene["participants"])
    if not participants:
        raise ValueError("No characters are available in the current scene")
    addressed = _last_addressed_actor(conn, int(scene["id"]), set(participants))
    if addressed is not None:
        return addressed
    index = int(scene["turn_count"]) % len(participants)
    return participants[index]


def finish_turn(conn: sqlite3.Connection, scene_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)).fetchone()
    if row is None:
        raise ValueError(f"Scene not found: {scene_id}")
    next_count = int(row["turn_count"]) + 1
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    if next_count >= int(row["turn_budget"]):
        conn.execute(
            "UPDATE scenes SET turn_count = ?, status = 'closed', end_tick = ? WHERE id = ?",
            (next_count, tick, scene_id),
        )
    else:
        conn.execute("UPDATE scenes SET turn_count = ? WHERE id = ?", (next_count, scene_id))
    return current_scene_by_id(conn, scene_id)


def scene_prompt_slice(conn: sqlite3.Connection, scene: dict[str, Any]) -> dict[str, Any]:
    location = conn.execute("SELECT * FROM locations WHERE id = ?", (scene["location_id"],)).fetchone()
    attrs = conn.execute(
        """
        SELECT sad.id, sad.label, sad.value_type, sa.value
        FROM scene_attributes sa
        JOIN scene_attribute_defs sad ON sad.id = sa.attr_id
        WHERE sa.scene_id = ?
        ORDER BY sad.category, sad.id
        """,
        (scene["id"],),
    ).fetchall()
    return {
        "scene_id": scene["id"],
        "title": scene["title"],
        "purpose": scene["purpose"],
        "turn": scene["turn_count"],
        "turn_budget": scene["turn_budget"],
        "location": {
            "id": location["id"] if location else scene["location_id"],
            "name": location["name"] if location else scene["location_id"],
            "description": location["description"] if location else "",
        },
        "attributes": {
            row["id"]: {
                "label": row["label"],
                "value": value_from_storage(row["value_type"], row["value"]),
            }
            for row in attrs
        },
        "participants": scene["participants"],
    }


def conversation_prompt_slice(conn: sqlite3.Connection, scene: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT topic, last_speaker_id, last_recipient_id, last_content, heard_by, updated_tick
        FROM scene_conversations
        WHERE scene_id = ?
        """,
        (scene["id"],),
    ).fetchone()
    if row is None:
        return {"topic": "", "last_turn": None, "heard_by": []}
    return {
        "topic": row["topic"],
        "last_turn": {
            "speaker_id": row["last_speaker_id"],
            "recipient_id": row["last_recipient_id"],
            "content": row["last_content"],
            "tick": row["updated_tick"],
        }
        if row["last_speaker_id"]
        else None,
        "heard_by": loads(row["heard_by"], []),
    }


def record_scene_turn(
    conn: sqlite3.Connection,
    *,
    scene: dict[str, Any],
    actor_id: str,
    content: str,
    action: dict[str, Any],
) -> None:
    recipient = action.get("to")
    proposed_action = action.get("action")
    if recipient is None and isinstance(proposed_action, dict):
        target = proposed_action.get("target")
        if target in scene["participants"]:
            recipient = target
    if recipient in scene["participants"] and recipient != actor_id:
        _register_direct_encounter(conn, actor_id, recipient)
    topic = str(action.get("topic") or "").strip()[:40]
    previous = conn.execute(
        "SELECT topic FROM scene_conversations WHERE scene_id = ?",
        (scene["id"],),
    ).fetchone()
    if not topic and previous is not None:
        topic = previous["topic"]
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    conn.execute(
        """
        INSERT INTO scene_conversations
          (scene_id, topic, last_speaker_id, last_recipient_id, last_content, heard_by, updated_tick)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(scene_id) DO UPDATE SET
          topic = excluded.topic,
          last_speaker_id = excluded.last_speaker_id,
          last_recipient_id = excluded.last_recipient_id,
          last_content = excluded.last_content,
          heard_by = excluded.heard_by,
          updated_tick = excluded.updated_tick
        """,
        (scene["id"], topic, actor_id, recipient, content[:180], dumps(scene["participants"]), tick),
    )


def _register_direct_encounter(conn: sqlite3.Connection, first_id: str, second_id: str) -> None:
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    for source, target in ((first_id, second_id), (second_id, first_id)):
        row = conn.execute(
            "SELECT familiarity FROM relationships WHERE from_id = ? AND to_id = ?",
            (source, target),
        ).fetchone()
        if row is None:
            conn.execute(
                """
                INSERT INTO relationships (from_id, to_id, familiarity, updated_tick)
                VALUES (?, ?, 5, ?)
                """,
                (source, target, tick),
            )
        elif int(row["familiarity"]) < 20:
            conn.execute(
                """
                UPDATE relationships
                SET familiarity = MIN(20, familiarity + 1), updated_tick = ?
                WHERE from_id = ? AND to_id = ?
                """,
                (tick, source, target),
            )


def _scene_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "location_id": row["location_id"],
        "title": row["title"],
        "purpose": row["purpose"],
        "start_tick": row["start_tick"],
        "end_tick": row["end_tick"],
        "participants": loads(row["participants"], []),
        "turn_budget": row["turn_budget"],
        "turn_count": row["turn_count"],
        "status": row["status"],
        "state": loads(row["state"], {}),
    }


def _character_ids_at_location(conn: sqlite3.Connection, location_id: str) -> list[str]:
    rows = conn.execute(
        """
        SELECT c.id
        FROM characters c
        JOIN character_attributes ca ON ca.char_id = c.id
        WHERE ca.attr_id = 'location' AND ca.value = ?
        ORDER BY c.rowid
        """,
        (location_id,),
    ).fetchall()
    return [row["id"] for row in rows]


def _seed_scene_attributes(conn: sqlite3.Connection, scene_id: int, location_state: dict[str, Any]) -> None:
    rows = conn.execute("SELECT * FROM scene_attribute_defs ORDER BY id").fetchall()
    for row in rows:
        value = location_state.get(row["id"], row["default_value"] or "")
        conn.execute(
            """
            INSERT OR IGNORE INTO scene_attributes (scene_id, attr_id, value)
            VALUES (?, ?, ?)
            """,
            (scene_id, row["id"], str(value)),
        )


def _last_addressed_actor(conn: sqlite3.Connection, scene_id: int, participants: set[str]) -> str | None:
    row = conn.execute(
        """
        SELECT data
        FROM scene_log
        WHERE scene_id = ? AND type = 'speech'
        ORDER BY id DESC
        LIMIT 1
        """,
        (scene_id,),
    ).fetchone()
    if row is None:
        return None
    action = loads(row["data"], {}).get("action", {})
    target = action.get("to") if isinstance(action, dict) else None
    if isinstance(target, str) and target in participants:
        return target
    return None
