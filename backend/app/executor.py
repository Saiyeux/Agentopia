from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

from .db import coerce_for_storage, dumps, value_from_storage


DeltaTarget = Literal["character_attribute", "relationship", "world_state"]
DeltaOp = Literal["add", "set"]


@dataclass(frozen=True)
class Delta:
    target: DeltaTarget
    ref: str
    field: str
    op: DeltaOp
    value: Any
    reason: str = ""
    source: str | None = None


ATTRIBUTE_DELTA_ALLOWLIST = {
    "mood",
    "energy",
    "health",
    "money",
}

WORLD_DELTA_ALLOWLIST = {
    "tension": (0, 100),
    "economy_index": (0, 100),
}

RELATIONSHIP_DELTA_ALLOWLIST = {"affection", "trust", "respect", "familiarity"}


def apply_deltas(conn: sqlite3.Connection, deltas: list[Delta]) -> list[dict[str, Any]]:
    applied: list[dict[str, Any]] = []
    for delta in deltas:
        if delta.target == "character_attribute":
            applied.append(_apply_character_attribute_delta(conn, delta))
        elif delta.target == "relationship":
            applied.append(_apply_relationship_delta(conn, delta))
        elif delta.target == "world_state":
            applied.append(_apply_world_state_delta(conn, delta))
        else:
            applied.append(_reject(delta, "unsupported target"))
    return applied


def _apply_character_attribute_delta(conn: sqlite3.Connection, delta: Delta) -> dict[str, Any]:
    if delta.field not in ATTRIBUTE_DELTA_ALLOWLIST:
        return _reject(delta, "field is not in character attribute allowlist")

    defn = conn.execute("SELECT * FROM attribute_defs WHERE id = ?", (delta.field,)).fetchone()
    if defn is None:
        return _reject(delta, "attribute definition does not exist")
    if not defn["mutable"]:
        return _reject(delta, "attribute is immutable")
    if defn["value_type"] not in {"int", "float"}:
        return _reject(delta, "only numeric attributes support deltas")

    row = conn.execute(
        """
        SELECT ca.value
        FROM character_attributes ca
        WHERE ca.char_id = ? AND ca.attr_id = ?
        """,
        (delta.ref, delta.field),
    ).fetchone()
    if row is None:
        return _reject(delta, "character attribute value does not exist")

    old_value = value_from_storage(defn["value_type"], row["value"])
    if delta.op == "add":
        new_value = old_value + delta.value
    elif delta.op == "set":
        new_value = delta.value
    else:
        return _reject(delta, "unsupported op")

    stored = coerce_for_storage(defn, new_value)
    final_value = value_from_storage(defn["value_type"], stored)
    conn.execute(
        """
        UPDATE character_attributes
        SET value = ?, updated_tick = (SELECT sim_tick FROM world_state WHERE id = 1)
        WHERE char_id = ? AND attr_id = ?
        """,
        (stored, delta.ref, delta.field),
    )
    return _accept(delta, old_value, final_value)


def _apply_relationship_delta(conn: sqlite3.Connection, delta: Delta) -> dict[str, Any]:
    if delta.field not in RELATIONSHIP_DELTA_ALLOWLIST:
        return _reject(delta, "field is not in relationship allowlist")
    if not delta.source or delta.source == delta.ref:
        return _reject(delta, "relationship requires distinct source and ref")
    exists = conn.execute(
        "SELECT id FROM characters WHERE id IN (?, ?)",
        (delta.source, delta.ref),
    ).fetchall()
    if len(exists) != 2:
        return _reject(delta, "relationship character does not exist")

    conn.execute(
        """
        INSERT OR IGNORE INTO relationships (from_id, to_id)
        VALUES (?, ?)
        """,
        (delta.source, delta.ref),
    )
    row = conn.execute(
        f"SELECT {delta.field} FROM relationships WHERE from_id = ? AND to_id = ?",
        (delta.source, delta.ref),
    ).fetchone()
    if row is None:
        return _reject(delta, "relationship value does not exist")

    old_value = int(row[delta.field])
    if delta.op == "add":
        raw_value = old_value + int(delta.value)
    elif delta.op == "set":
        raw_value = int(delta.value)
    else:
        return _reject(delta, "unsupported op")
    new_value = max(0, min(100, raw_value))
    conn.execute(
        f"""
        UPDATE relationships
        SET {delta.field} = ?, updated_tick = (SELECT sim_tick FROM world_state WHERE id = 1)
        WHERE from_id = ? AND to_id = ?
        """,
        (new_value, delta.source, delta.ref),
    )
    return _accept(delta, old_value, new_value)


def _apply_world_state_delta(conn: sqlite3.Connection, delta: Delta) -> dict[str, Any]:
    bounds = WORLD_DELTA_ALLOWLIST.get(delta.field)
    if bounds is None:
        return _reject(delta, "field is not in world allowlist")
    if delta.ref != "world":
        return _reject(delta, "world deltas must use ref='world'")

    row = conn.execute(f"SELECT {delta.field} FROM world_state WHERE id = 1").fetchone()
    if row is None:
        return _reject(delta, "world state does not exist")

    old_value = row[delta.field]
    if delta.op == "add":
        raw_value = old_value + delta.value
    elif delta.op == "set":
        raw_value = delta.value
    else:
        return _reject(delta, "unsupported op")

    min_value, max_value = bounds
    new_value = max(min_value, min(max_value, int(raw_value)))
    conn.execute(f"UPDATE world_state SET {delta.field} = ? WHERE id = 1", (new_value,))
    return _accept(delta, old_value, new_value)


def append_verdict_log(
    conn: sqlite3.Connection,
    actor_id: str,
    narration: str,
    applied: list[dict[str, Any]],
    scene_id: int | None = None,
) -> int:
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = world["sim_tick"] if world else 0
    cursor = conn.execute(
        """
        INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
        VALUES (?, ?, ?, 'verdict', ?, ?, 'all')
        """,
        (scene_id, tick, actor_id, narration, dumps({"applied": applied})),
    )
    return int(cursor.lastrowid)


def advance_tick(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    next_tick = int(row["sim_tick"]) + 1 if row else 1
    conn.execute("UPDATE world_state SET sim_tick = ? WHERE id = 1", (next_tick,))
    return next_tick


def _accept(delta: Delta, old_value: Any, new_value: Any) -> dict[str, Any]:
    return {
        "status": "applied",
        "target": delta.target,
        "ref": delta.ref,
        "field": delta.field,
        "op": delta.op,
        "requested": delta.value,
        "old": old_value,
        "new": new_value,
        "reason": delta.reason,
        "source": delta.source,
    }


def _reject(delta: Delta, message: str) -> dict[str, Any]:
    return {
        "status": "rejected",
        "target": delta.target,
        "ref": delta.ref,
        "field": delta.field,
        "op": delta.op,
        "requested": delta.value,
        "message": message,
        "reason": delta.reason,
        "source": delta.source,
    }
