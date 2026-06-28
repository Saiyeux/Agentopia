from __future__ import annotations

import ast
import random
import sqlite3
from typing import Any

from .db import dumps, loads
from .filtering import sanitize_display_text
from .llm import chat_json_schema
from .prompts import build_world_event_prompt
from .scheduler import scene_prompt_slice
from .threads import active_thread_count, create_thread_from_event, should_create_thread


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
    present_needs = _present_needs(conn, scene)
    drama = int(world.get("drama", 30))
    scene_id = int(scene["id"]) if scene is not None else None
    open_threads = active_thread_count(conn, scene_id)

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
        if open_threads > 0 and should_create_thread(row):
            continue

        # drama 旋钮：影响 weighted 事件的有效权重
        if row["tier"] == "weighted":
            effective_weight = _calculate_effective_weight(
                base_weight=int(row["weight"]),
                target_need=row["target_need"],
                present_needs=present_needs,
                drama=drama,
            )
            if not _weighted_hit(effective_weight):
                continue

        event = _fire_event(conn, row, world, scene)
        fired.append(event)
        if event.get("thread_id") is not None:
            open_threads += 1
            break
        if row["tier"] in {"scheduled", "emergent"}:
            break
    return fired


def _world_context(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT sim_tick, day, chapter, period, weather, tension, economy_index, drama
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
            "drama": 30,
        }
    return dict(row)


def _present_needs(conn: sqlite3.Connection, scene: dict[str, Any] | None) -> set[str]:
    """
    判断在场角色的匮乏情况，返回命中的 need 标签集合。
    阈值: money < 20, mood < 35, health < 60, energy < 40
    """
    needs = set()
    if scene is None:
        return needs

    participants = scene.get("participants", [])
    if not participants:
        return needs

    for char_id in participants:
        rows = conn.execute(
            "SELECT attr_id, value FROM character_attributes WHERE char_id = ?",
            (char_id,),
        ).fetchall()
        attrs = {r["attr_id"]: r["value"] for r in rows}

        try:
            money = int(attrs.get("money", "0"))
            mood = int(attrs.get("mood", "50"))
            health = int(attrs.get("health", "100"))
            energy = int(attrs.get("energy", "100"))

            if money < 20:
                needs.add("money_low")
            if mood < 35:
                needs.add("mood_low")
            if health < 60:
                needs.add("health_low")
            if energy < 40:
                needs.add("energy_low")
        except (ValueError, TypeError):
            continue

    return needs


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


def _calculate_effective_weight(
    base_weight: int,
    target_need: str | None,
    present_needs: set[str],
    drama: int,
) -> int:
    """
    计算 weighted 事件的有效权重。
    - 环境事件(target_need=None): 权重不变
    - 针对性事件命中匮乏: 权重 += drama * 0.8
    - 针对性事件未命中匮乏: 权重 -= 30
    """
    K = 0.8  # drama 影响系数
    if target_need is None:
        # 环境事件，不受 drama 影响
        return base_weight

    if target_need in present_needs:
        # 针对性事件命中在场匮乏，drama 越高权重越大
        boost = int(drama * K)
        return max(0, min(100, base_weight + boost))
    else:
        # 针对性事件未命中匮乏，压低权重
        return max(0, base_weight - 30)


def _weighted_hit(weight: int) -> bool:
    clamped = max(0, min(100, int(weight)))
    return random.randint(1, 100) <= clamped


def _fire_event(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    world: dict[str, Any],
    scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    narration = _generate_event_narration(conn, row, world, scene)
    tick = int(world["sim_tick"])
    scene_id = int(scene["id"]) if scene is not None else None
    location_id = str(scene["location_id"]) if scene is not None else "main_floor"
    payload = {
        "def_id": row["id"],
        "title": row["title"],
        "tier": row["tier"],
        "guidance": row["guidance"],
        "generated_by": "world_llm",
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
    thread_id = create_thread_from_event(
        conn,
        event_id=instance_id,
        event_def=row,
        narration=narration,
        scene=scene,
        tick=tick,
    )
    if thread_id is not None:
        payload["thread_id"] = thread_id
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
        "thread_id": thread_id,
    }


def _generate_event_narration(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    world: dict[str, Any],
    scene: dict[str, Any] | None,
) -> str:
    schema = {
        "type": "object",
        "properties": {"narration": {"type": "string"}},
        "required": ["narration"],
        "additionalProperties": False,
    }
    character_rows = conn.execute("SELECT id, name FROM characters").fetchall()
    character_refs = {item for row in character_rows for item in (row["id"], row["name"]) if item}
    source_texts = [str(row["narration"] or ""), str(row["guidance"] or ""), *character_refs]
    event_def = dict(row)
    event_def["effects"] = loads(row["effects"], [])
    scene_slice = scene_prompt_slice(conn, scene) if scene is not None else None
    recent = _recent_scene_log(conn, int(scene["id"]) if scene is not None else None)
    system, user = build_world_event_prompt(
        world=world,
        event_def=event_def,
        scene=scene,
        scene_slice=scene_slice,
        recent_scene_log=recent,
    )
    attempts = [
        user,
        user + "\n上一轮不合格。请重新生成，不要分析，不要复述输入，只返回 JSON。",
    ]
    errors: list[str] = []
    for index, prompt in enumerate(attempts):
        try:
            result = chat_json_schema(
                schema_name="agentopia_world_event",
                schema=schema,
                system=system,
                user=prompt,
                temperature=0.78 if index == 0 else 0.62,
                max_tokens=180,
            )
            parsed = result.get("parsed", {})
            raw = str(parsed.get("narration") or result.get("content", ""))
            return _normalize_event_narration(raw, source_texts=source_texts, character_refs=character_refs)
        except Exception as exc:
            errors.append(str(exc))
    raise ValueError("世界事件生成失败: " + "; ".join(errors[-2:]))


def _normalize_event_narration(raw_content: str, source_texts: list[str], character_refs: set[str]) -> str:
    content = raw_content.strip().strip('"\'`')
    if not content:
        raise ValueError("世界模型未返回事件内容")
    cleaned = sanitize_display_text(content, "")
    if not cleaned:
        raise ValueError(f"世界事件内容不可用: {raw_content[:160]}")
    if not _is_valid_event_narration(cleaned, source_texts, character_refs):
        raise ValueError(f"世界事件内容不合格: {raw_content[:160]}")
    return cleaned


def _is_valid_event_narration(text: str, source_texts: list[str], character_refs: set[str]) -> bool:
    if not 8 <= len(text) <= 140:
        return False
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    ascii_letters = sum(1 for char in text if char.isascii() and char.isalpha())
    if cjk_count < 8 or ascii_letters > 12:
        return False
    blocked = ("任务", "要求", "输出", "JSON", "event_def", "guidance", "narration", "不要", "根据", "char_")
    if any(marker in text for marker in blocked):
        return False
    if any(ref and ref in text for ref in character_refs):
        return False
    return not _copies_source_text(text, source_texts)


def _copies_source_text(text: str, source_texts: list[str]) -> bool:
    compact = _compact_cjk(text)
    if len(compact) < 10:
        return False
    for source in source_texts:
        source_compact = _compact_cjk(source)
        if not source_compact:
            continue
        if compact in source_compact:
            return True
        window = 10
        chunks = {
            compact[index : index + window]
            for index in range(0, max(1, len(compact) - window + 1), window)
        }
        hits = sum(1 for chunk in chunks if len(chunk) == window and chunk in source_compact)
        if hits >= 2:
            return True
    return False


def _compact_cjk(text: str) -> str:
    return "".join(char for char in text if "\u4e00" <= char <= "\u9fff")


def _recent_scene_log(conn: sqlite3.Connection, scene_id: int | None) -> list[dict[str, Any]]:
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
            WHERE scene_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (scene_id,),
        ).fetchall()
    return [dict(row) for row in reversed(rows)]
