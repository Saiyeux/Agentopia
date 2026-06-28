from __future__ import annotations

import sqlite3
from typing import Any

from .db import dumps, loads


PARK_AFTER = 3


THREAD_EVENT_IDS = {
    "weighted_gig_offer",
    "weighted_synth_drink",
    "weighted_medkit_offer",
    "weighted_quiet_booth",
    "emergent_corp_intel",
    "emergent_corp_tracker",
    "weighted_netrunner_job",
    "emergent_debt_collector",
    "weighted_cyberware_emergency",
    "emergent_gang_confrontation",
    "weighted_legendary_gig",
    "weighted_pro_recommendation",
    "weighted_partner_prospect",
}

AMBIENT_EVENT_IDS = {
    "scheduled_crowd_shift",
    "weighted_hologram_glitch",
    "weighted_acid_rain",
    "emergent_tension_spike",
}

STAGE_LABELS = {
    "introduced": "刚出现",
    "discussing": "正在讨论",
    "negotiating": "正在确认条件",
    "committed": "已经接下",
    "executing": "正在执行",
    "resolved": "已收束",
}


def should_create_thread(row: sqlite3.Row) -> bool:
    return str(row["id"]) in THREAD_EVENT_IDS or row["target_need"] is not None


def active_thread_count(conn: sqlite3.Connection, scene_id: int | None) -> int:
    if scene_id is None:
        return 0
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM story_threads WHERE scene_id = ? AND status = 'active'",
        (scene_id,),
    ).fetchone()
    return int(row["count"]) if row else 0


def create_thread_from_event(
    conn: sqlite3.Connection,
    *,
    event_id: int,
    event_def: sqlite3.Row,
    narration: str,
    scene: dict[str, Any] | None,
    tick: int,
) -> int | None:
    if not should_create_thread(event_def) or scene is None:
        return None
    participants = list(scene.get("participants", []))
    stakes = {
        "event_def_id": event_def["id"],
        "target_need": event_def["target_need"],
        "guidance": event_def["guidance"],
    }
    cursor = conn.execute(
        """
        INSERT INTO story_threads
          (scene_id, source_event_id, title, status, stage, summary, participants, stakes, priority, created_tick, updated_tick)
        VALUES (?, ?, ?, 'active', 'introduced', ?, ?, ?, ?, ?, ?)
        """,
        (
            int(scene["id"]),
            event_id,
            event_def["title"],
            narration[:240],
            dumps(participants),
            dumps(stakes),
            _priority_for_event(event_def),
            tick,
            tick,
        ),
    )
    thread_id = int(cursor.lastrowid)
    conn.execute(
        """
        INSERT INTO story_beats (thread_id, tick, actor_id, beat_type, content, data)
        VALUES (?, ?, 'WORLD', 'introduced', ?, ?)
        """,
        (thread_id, tick, narration, dumps(stakes)),
    )
    return thread_id


def active_thread_slice(
    conn: sqlite3.Connection,
    scene: dict[str, Any] | None,
    actor_id: str | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    if scene is None:
        return []
    rows = conn.execute(
        """
        SELECT *
        FROM story_threads
        WHERE scene_id = ? AND status = 'active'
        ORDER BY priority DESC, updated_tick DESC, id DESC
        LIMIT 8
        """,
        (int(scene["id"]),),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        participants = loads(row["participants"], [])
        if actor_id is not None and participants and actor_id not in participants:
            continue
        beats = conn.execute(
            """
            SELECT tick, actor_id, beat_type, content
            FROM story_beats
            WHERE thread_id = ?
            ORDER BY id DESC
            LIMIT 4
            """,
            (row["id"],),
        ).fetchall()
        items.append(
            {
                "id": row["id"],
                "title": row["title"],
                "status": row["status"],
                "stage": row["stage"],
                "stage_label": STAGE_LABELS.get(row["stage"], row["stage"]),
                "summary": row["summary"],
                "participants": participants,
                "stakes": loads(row["stakes"], {}),
                "beat_count": row["beat_count"],
                "stalled_turns": row["stalled_turns"],
                "stale": int(row["stalled_turns"]) >= PARK_AFTER,
                "recent_beats": [dict(beat) for beat in reversed(beats)],
            }
        )
        if len(items) >= limit:
            break
    return items


def thread_situation(threads: list[dict[str, Any]]) -> str | None:
    if not threads:
        return None
    thread = threads[0]
    if thread.get("stale"):
        return (
            f"当前剧情线程《{thread['title']}》已经在{thread['stage_label']}阶段停滞。"
            "这条线索可以推进、收个尾、或先放一放，转向别的人、话题或小事。"
        )
    return (
        f"当前剧情线程《{thread['title']}》处于{thread['stage_label']}阶段。"
        "可以承接它继续问清条件、提出方案、分工、执行一步或给出具体结果；"
        "也可以在与你无关时简短旁观，把注意力转向更符合你目标或性格的事。"
    )


def record_thread_turn(
    conn: sqlite3.Connection,
    *,
    scene: dict[str, Any] | None,
    actor_id: str,
    action: dict[str, Any],
    log_id: int,
) -> dict[str, Any] | None:
    threads = active_thread_slice(conn, scene, actor_id=actor_id, limit=1)
    if not threads:
        return None
    thread = threads[0]
    world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
    tick = int(world["sim_tick"]) if world else 0
    speech = str(action.get("speech") or "")
    proposed = action.get("action")
    data = {"action": action, "speech_log_id": log_id}
    previous_stage = str(thread["stage"])
    current_stalled = int(thread.get("stalled_turns") or 0)
    new_stage = _next_stage(previous_stage, speech, proposed, int(thread["beat_count"]), current_stalled)
    progressed = new_stage != previous_stage
    next_stalled = 0 if progressed else current_stalled + 1
    if new_stage == "resolved":
        new_status = "resolved"
    elif next_stalled >= PARK_AFTER:
        new_status = "parked"
    else:
        new_status = "active"
    conn.execute(
        """
        INSERT INTO story_beats (thread_id, tick, actor_id, beat_type, content, data)
        VALUES (?, ?, ?, 'turn', ?, ?)
        """,
        (thread["id"], tick, actor_id, speech[:240], dumps(data)),
    )
    conn.execute(
        """
        UPDATE story_threads
        SET stage = ?,
            status = ?,
            summary = ?,
            beat_count = beat_count + 1,
            stalled_turns = ?,
            updated_tick = ?
        WHERE id = ?
        """,
        (new_stage, new_status, _updated_summary(thread, speech, new_stage), next_stalled, tick, thread["id"]),
    )
    if new_status in {"resolved", "parked"}:
        verb = "暂时收束" if new_status == "resolved" else "暂时搁置"
        conn.execute(
            """
            INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
            VALUES (?, ?, 'WORLD', 'thread', ?, ?, 'all')
            """,
            (
                int(scene["id"]) if scene is not None else None,
                tick,
                f"剧情线《{thread['title']}》{verb}：{speech[:80]}",
                dumps({"thread_id": thread["id"], "stage": new_stage, "status": new_status, "stalled_turns": next_stalled}),
            ),
        )
    return {"thread_id": thread["id"], "stage": new_stage, "status": new_status, "stalled_turns": next_stalled}


def _priority_for_event(row: sqlite3.Row) -> int:
    if row["tier"] == "emergent":
        return 80
    if row["target_need"] is not None:
        return 70
    return 55


def _next_stage(stage: str, speech: str, proposed: Any, beat_count: int, stalled_turns: int = 0) -> str:
    text = speech.lower()
    has_action = isinstance(proposed, dict) and bool(proposed.get("type"))
    if stage == "introduced":
        return "discussing"
    if stage == "discussing":
        if _contains_any(text, ("报酬", "条件", "细节", "谁", "怎么", "多少", "为什么", "?")):
            return "negotiating"
        if has_action or _contains_commitment(text):
            return "committed"
        return "discussing"
    if stage == "negotiating":
        if has_action or _contains_commitment(text):
            return "committed"
        return "negotiating"
    if stage == "committed":
        return "executing"
    if stage == "executing":
        if _contains_resolution(text) or stalled_turns >= PARK_AFTER:
            return "resolved"
        return "executing"
    return stage


def _updated_summary(thread: dict[str, Any], speech: str, stage: str) -> str:
    base = str(thread.get("summary") or "")
    label = STAGE_LABELS.get(stage, stage)
    addition = f"{label}：{speech[:80]}"
    return (base + " / " + addition)[-320:] if base else addition


def _contains_commitment(text: str) -> bool:
    if _looks_like_question(text):
        return False
    return _contains_any(text, ("接", "帮", "去", "查", "修", "谈", "找", "干", "做", "成交", "可以", "我来"))


def _contains_resolution(text: str) -> bool:
    if _looks_like_question(text):
        return False
    return _contains_any(
        text,
        (
            "完成",
            "搞定",
            "查到了",
            "拿到了",
            "找到了",
            "修好了",
            "处理完",
            "交付",
            "结清",
            "收到钱",
            "任务结束",
            "失败",
            "放弃",
            "取消",
            "撤离",
            "撤退",
        ),
    )


def _looks_like_question(text: str) -> bool:
    stripped = text.strip()
    if stripped.endswith(("?", "？")):
        return True
    return stripped.startswith(("谁", "哪", "为什么", "能不能", "要不要"))


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)
