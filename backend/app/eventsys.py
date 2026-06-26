from __future__ import annotations

import ast
import random
import sqlite3
from typing import Any

from .db import dumps
from .filtering import sanitize_display_text


SAFE_NODES = {
    ast.Expression,
    ast.BoolOp,
    ast.UnaryOp,
    ast.BinOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Mod,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
}


def scan_and_fire(conn: sqlite3.Connection, scene: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    world = _world_context(conn)
    rows = conn.execute(
        """
        SELECT *
        FROM event_defs
        WHERE enabled = 1
        ORDER BY
          CASE tier
            WHEN 'scheduled' THEN 0
            WHEN 'emergent' THEN 1
            ELSE 2
          END,
          id
        """
    ).fetchall()
    fired: list[dict[str, Any]] = []
    for row in rows:
        if _on_cooldown(row, world["sim_tick"]):
            continue
        try:
            condition_matches = _condition_matches(row["condition"], world)
        except ValueError:
            continue
        if not condition_matches:
            continue
        if row["tier"] == "weighted" and not _weighted_hit(row["weight"]):
            continue
        fired.append(_fire_event(conn, row, world, scene))
        if row["tier"] in {"scheduled", "emergent"}:
            break
    return fired


def _world_context(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT sim_tick, day, chapter, period, weather, tension, economy_index
        FROM world_state
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return {
            "sim_tick": 0,
            "day": 0,
            "chapter": 0,
            "period": "morning",
            "weather": "clear",
            "tension": 0,
            "economy_index": 50,
        }
    return dict(row)


def _on_cooldown(row: sqlite3.Row, tick: int) -> bool:
    cooldown = int(row["cooldown"] or 0)
    last_fired = int(row["last_fired_tick"])
    return cooldown > 0 and tick - last_fired < cooldown


def _condition_matches(condition: str, world: dict[str, Any]) -> bool:
    if not condition.strip():
        return True
    tree = ast.parse(condition, mode="eval")
    for node in ast.walk(tree):
        if type(node) not in SAFE_NODES:
            raise ValueError(f"Unsafe event condition node: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in world:
            raise ValueError(f"Unknown event condition name: {node.id}")
    return bool(_eval_node(tree.body, world))


def _eval_node(node: ast.AST, world: dict[str, Any]) -> Any:
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(value, world) for value in node.values]
        if isinstance(node.op, ast.And):
            return all(values)
        if isinstance(node.op, ast.Or):
            return any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, world)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
        return _eval_node(node.left, world) % _eval_node(node.right, world)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, world)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, world)
            if not _compare(left, op, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return world[node.id]
    if isinstance(node, ast.Constant):
        return node.value
    raise ValueError(f"Unsupported event condition: {type(node).__name__}")


def _compare(left: Any, op: ast.cmpop, right: Any) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise ValueError(f"Unsupported comparison: {type(op).__name__}")


def _weighted_hit(weight: int) -> bool:
    clamped = max(0, min(100, int(weight)))
    return random.randint(1, 100) <= clamped


def _fire_event(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    world: dict[str, Any],
    scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    narration = sanitize_display_text(row["narration"], "世界轻轻偏转了一下。")
    tick = int(world["sim_tick"])
    scene_id = int(scene["id"]) if scene is not None else None
    location_id = str(scene["location_id"]) if scene is not None else "hall"
    payload = {
        "def_id": row["id"],
        "title": row["title"],
        "tier": row["tier"],
        "guidance": row["guidance"],
        "scene_id": scene_id,
        "location_id": location_id,
    }
    cursor = conn.execute(
        """
        INSERT INTO event_instances
          (def_id, scene_id, tier, fired_tick, location_id, narration, payload, visibility, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'all', 'active')
        """,
        (row["id"], scene_id, row["tier"], tick, location_id, narration, dumps(payload)),
    )
    instance_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
        VALUES (?, ?, 'WORLD', 'event', ?, ?, 'all')
        """,
        (scene_id, tick, narration, dumps({"event_instance_id": instance_id, **payload})),
    )
    conn.execute(
        "UPDATE event_defs SET last_fired_tick = ? WHERE id = ?",
        (tick, row["id"]),
    )
    return {
        "id": instance_id,
        "def_id": row["id"],
        "title": row["title"],
        "tier": row["tier"],
        "scene_id": scene_id,
        "location_id": location_id,
        "tick": tick,
        "narration": narration,
    }
