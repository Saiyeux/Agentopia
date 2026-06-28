from __future__ import annotations

import sqlite3
from typing import Any

from .db import dumps, loads, set_character_attribute, value_from_storage


DEFAULT_SCENE_BUDGET = 24
MAX_SCENE_GROUP_SIZE = 3
GROUP_ROTATE_TURNS = 5


def current_or_open_scene(conn: sqlite3.Connection, location_id: str = "main_floor") -> dict[str, Any]:
    _ensure_open_scenes_for_occupied_locations(conn, location_id)
    scenes = list_open_scenes(conn)
    if scenes:
        return scenes[0]
    return open_scene(conn, location_id)


def list_open_scenes(conn: sqlite3.Connection, location_id: str = "main_floor") -> list[dict[str, Any]]:
    _ensure_open_scenes_for_occupied_locations(conn, location_id)
    rows = conn.execute(
        """
        SELECT *
        FROM scenes
        WHERE status = 'open'
        ORDER BY id ASC
        """
    ).fetchall()
    scenes = [_scene_from_row(row) for row in rows]
    return sorted(scenes, key=lambda scene: _scene_focus_score(conn, scene), reverse=True)


def open_scene(
    conn: sqlite3.Connection,
    location_id: str = "main_floor",
    participants: list[str] | None = None,
) -> dict[str, Any]:
    location = conn.execute("SELECT * FROM locations WHERE id = ?", (location_id,)).fetchone()
    if location is None:
        raise ValueError(f"Location not found: {location_id}")
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    participants = list(participants) if participants is not None else _character_ids_at_location(conn, location_id)[:MAX_SCENE_GROUP_SIZE]
    if not participants:
        participants = [row["id"] for row in conn.execute("SELECT id FROM characters ORDER BY rowid").fetchall()]
        participants = participants[:MAX_SCENE_GROUP_SIZE]
    cursor = conn.execute(
        """
        INSERT INTO scenes
          (location_id, title, purpose, start_tick, participants, turn_budget, state)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            location_id,
            location["name"],
            "让当前地点的小组自然发生角色互动、移动和世界事件。",
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
    scene = reconcile_scene_participants(conn, int(scene["id"]))
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
    scene = reconcile_scene_participants(conn, scene_id)
    if not scene["participants"]:
        world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
        tick = int(world["sim_tick"]) if world else 0
        conn.execute(
            "UPDATE scenes SET status = 'closed', end_tick = ? WHERE id = ?",
            (tick, scene_id),
        )
        return current_scene_by_id(conn, scene_id)
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
            "connected": loads(location["connected"], []) if location else [],
        },
        "available_locations": _available_locations(conn, scene["location_id"]),
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


def apply_local_action_effects(
    conn: sqlite3.Connection,
    *,
    scene: dict[str, Any],
    actor_id: str,
    action: dict[str, Any],
) -> dict[str, Any] | None:
    proposed_action = action.get("action")
    if not isinstance(proposed_action, dict) or proposed_action.get("type") != "move":
        return None
    target_location_id = proposed_action.get("target")
    if not isinstance(target_location_id, str):
        return None
    if not _can_move(conn, scene["location_id"], target_location_id):
        return {
            "type": "move",
            "status": "rejected",
            "from": scene["location_id"],
            "to": target_location_id,
            "reason": "location is not connected",
        }

    from_location = scene["location_id"]
    if from_location == target_location_id:
        return {
            "type": "move",
            "status": "ignored",
            "from": from_location,
            "to": target_location_id,
            "reason": "already there",
        }

    set_character_attribute(conn, actor_id, "location", target_location_id)
    source_scene = reconcile_scene_participants(conn, int(scene["id"]))
    target_scene = _scene_with_capacity_at_location(conn, target_location_id) or open_scene(
        conn,
        target_location_id,
        [actor_id],
    )
    if actor_id not in target_scene["participants"]:
        participants = [*target_scene["participants"], actor_id]
        conn.execute(
            "UPDATE scenes SET participants = ? WHERE id = ?",
            (dumps(participants[:MAX_SCENE_GROUP_SIZE]), target_scene["id"]),
        )
        target_scene["participants"] = participants[:MAX_SCENE_GROUP_SIZE]
    target_scene = reconcile_scene_participants(conn, int(target_scene["id"]))
    target = conn.execute("SELECT name FROM locations WHERE id = ?", (target_location_id,)).fetchone()
    actor = conn.execute("SELECT name FROM characters WHERE id = ?", (actor_id,)).fetchone()
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    content = f"{actor['name'] if actor else actor_id} 转移到 {target['name'] if target else target_location_id}。"
    conn.execute(
        """
        INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
        VALUES (?, ?, ?, 'move', ?, ?, 'all')
        """,
        (
            target_scene["id"],
            tick,
            actor_id,
            content,
            dumps({"from": from_location, "to": target_location_id, "source_scene_id": source_scene["id"]}),
        ),
    )
    return {
        "type": "move",
        "status": "applied",
        "from": from_location,
        "to": target_location_id,
        "scene_id": target_scene["id"],
    }


def reconcile_scene_participants(conn: sqlite3.Connection, scene_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)).fetchone()
    if row is None:
        raise ValueError(f"Scene not found: {scene_id}")
    scene = _scene_from_row(row)
    present = set(_character_ids_at_location(conn, scene["location_id"]))
    participants = [char_id for char_id in scene["participants"] if char_id in present]
    if participants != scene["participants"]:
        conn.execute(
            "UPDATE scenes SET participants = ? WHERE id = ?",
            (dumps(participants), scene_id),
        )
        scene["participants"] = participants
    return scene


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


def _ensure_open_scenes_for_occupied_locations(conn: sqlite3.Connection, fallback_location_id: str) -> None:
    occupied = _occupied_location_ids(conn)
    if not occupied:
        occupied = [fallback_location_id]
    for row in conn.execute("SELECT id FROM scenes WHERE status = 'open'").fetchall():
        scene = reconcile_scene_participants(conn, int(row["id"]))
        if not scene["participants"]:
            world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
            tick = int(world["sim_tick"]) if world else 0
            conn.execute(
                "UPDATE scenes SET status = 'closed', end_tick = ? WHERE id = ?",
                (tick, scene["id"]),
            )
        elif len(scene["participants"]) > MAX_SCENE_GROUP_SIZE:
            kept = scene["participants"][:MAX_SCENE_GROUP_SIZE]
            overflow = scene["participants"][MAX_SCENE_GROUP_SIZE:]
            conn.execute(
                "UPDATE scenes SET participants = ? WHERE id = ?",
                (dumps(kept), scene["id"]),
            )
            for group in _chunks(overflow, MAX_SCENE_GROUP_SIZE):
                open_scene(conn, scene["location_id"], group)
    for loc_id in occupied:
        _rotate_stale_groups(conn, loc_id)
        present = _character_ids_at_location(conn, loc_id)
        open_scenes = _open_scenes_at_location(conn, loc_id)
        assigned = {
            char_id
            for scene in open_scenes
            for char_id in scene["participants"]
            if char_id in present
        }
        unassigned = [char_id for char_id in present if char_id not in assigned]
        for scene in open_scenes:
            if not unassigned:
                break
            room = MAX_SCENE_GROUP_SIZE - len(scene["participants"])
            if room <= 0:
                continue
            added = unassigned[:room]
            unassigned = unassigned[room:]
            participants = [*scene["participants"], *added]
            conn.execute(
                "UPDATE scenes SET participants = ? WHERE id = ?",
                (dumps(participants), scene["id"]),
            )
        for group in _chunks(unassigned, MAX_SCENE_GROUP_SIZE):
            open_scene(conn, loc_id, group)


def _occupied_location_ids(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT ca.value AS location_id
        FROM character_attributes ca
        JOIN locations l ON l.id = ca.value
        WHERE ca.attr_id = 'location'
        ORDER BY ca.value
        """
    ).fetchall()
    return [row["location_id"] for row in rows]


def _open_scene_at_location(conn: sqlite3.Connection, location_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM scenes
        WHERE status = 'open' AND location_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (location_id,),
    ).fetchone()
    return _scene_from_row(row) if row is not None else None


def _open_scenes_at_location(conn: sqlite3.Connection, location_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT *
        FROM scenes
        WHERE status = 'open' AND location_id = ?
        ORDER BY id ASC
        """,
        (location_id,),
    ).fetchall()
    return [_scene_from_row(row) for row in rows]


def _scene_with_capacity_at_location(conn: sqlite3.Connection, location_id: str) -> dict[str, Any] | None:
    candidates = [
        scene
        for scene in _open_scenes_at_location(conn, location_id)
        if len(scene["participants"]) < MAX_SCENE_GROUP_SIZE
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda scene: (len(scene["participants"]), scene["turn_count"], scene["id"]))[0]


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


def _available_locations(conn: sqlite3.Connection, location_id: str) -> list[dict[str, Any]]:
    current = conn.execute("SELECT connected FROM locations WHERE id = ?", (location_id,)).fetchone()
    ids = [location_id, *loads(current["connected"], [])] if current is not None else [location_id]
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, name, description FROM locations WHERE id IN ({placeholders}) ORDER BY name",
        ids,
    ).fetchall()
    return [dict(row) for row in rows]


def _can_move(conn: sqlite3.Connection, from_location_id: str, to_location_id: str) -> bool:
    if from_location_id == to_location_id:
        return True
    row = conn.execute("SELECT connected FROM locations WHERE id = ?", (from_location_id,)).fetchone()
    if row is None:
        return False
    return to_location_id in loads(row["connected"], [])


def _rotate_stale_groups(conn: sqlite3.Connection, location_id: str) -> None:
    scenes = _open_scenes_at_location(conn, location_id)
    if len(scenes) <= 1:
        return
    if any(_has_active_thread(conn, int(scene["id"])) for scene in scenes):
        return
    if max(int(scene["turn_count"]) for scene in scenes) < GROUP_ROTATE_TURNS:
        return

    present = _character_ids_at_location(conn, location_id)
    if len(present) <= MAX_SCENE_GROUP_SIZE:
        return

    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    ordered = _rotated_order(present, tick)
    for scene in scenes:
        conn.execute(
            "UPDATE scenes SET status = 'closed', end_tick = ? WHERE id = ?",
            (tick, scene["id"]),
        )
    for group in _alternating_groups(ordered):
        open_scene(conn, location_id, group)


def _has_active_thread(conn: sqlite3.Connection, scene_id: int) -> bool:
    row = conn.execute(
        "SELECT 1 FROM story_threads WHERE scene_id = ? AND status = 'active' LIMIT 1",
        (scene_id,),
    ).fetchone()
    return row is not None


def _rotated_order(values: list[str], tick: int) -> list[str]:
    if not values:
        return []
    offset = (tick // GROUP_ROTATE_TURNS) % len(values)
    rotated = values[offset:] + values[:offset]
    if (tick // GROUP_ROTATE_TURNS) % 2:
        head = rotated[:1]
        tail = list(reversed(rotated[1:]))
        return head + tail
    return rotated


def _alternating_groups(values: list[str]) -> list[list[str]]:
    if len(values) <= MAX_SCENE_GROUP_SIZE:
        return [values]
    if len(values) == 4:
        return [values[:2], values[2:]]
    if len(values) == 5:
        return [values[:3], values[3:]]
    return _chunks(values, MAX_SCENE_GROUP_SIZE)


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size) if values[index : index + size]]


def _scene_focus_score(conn: sqlite3.Connection, scene: dict[str, Any]) -> float:
    world = conn.execute("SELECT drama FROM world_state WHERE id = 1").fetchone()
    drama = int(world["drama"]) if world and world["drama"] is not None else 30
    fairness = -float(scene["turn_count"]) * max(1, 110 - drama)
    heat = _scene_heat(conn, scene)
    return fairness + heat * max(1, drama) - float(scene["id"]) * 0.001


def _scene_heat(conn: sqlite3.Connection, scene: dict[str, Any]) -> float:
    heat = len(scene["participants"]) * 2.0
    thread_rows = conn.execute(
        """
        SELECT priority, stage, stalled_turns
        FROM story_threads
        WHERE scene_id = ? AND status = 'active'
        """,
        (scene["id"],),
    ).fetchall()
    for row in thread_rows:
        heat += float(row["priority"]) / 10.0
        if row["stage"] in {"committed", "executing"}:
            heat += 8.0
        if int(row["stalled_turns"]) > 0:
            heat -= min(6.0, float(row["stalled_turns"]) * 2.0)
    recent_rows = conn.execute(
        """
        SELECT type
        FROM scene_log
        WHERE scene_id = ?
        ORDER BY id DESC
        LIMIT 6
        """,
        (scene["id"],),
    ).fetchall()
    for row in recent_rows:
        if row["type"] == "event":
            heat += 8.0
        elif row["type"] == "move":
            heat += 5.0
        elif row["type"] == "speech":
            heat += 2.0
    if _last_addressed_actor(conn, int(scene["id"]), set(scene["participants"])) is not None:
        heat += 5.0
    return heat


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
