from __future__ import annotations

from typing import Any

from .db import dumps
from .prompt_store import get_prompt


def build_character_prompt(
    *,
    actor: dict[str, Any],
    actor_attributes: dict[str, Any],
    actor_traits: dict[str, Any],
    actor_relationships: list[dict[str, Any]],
    scene: dict[str, Any] | None,
    conversation_state: dict[str, Any],
    present_characters: list[dict[str, Any]],
    recent_scene_log: list[dict[str, Any]],
    active_story_threads: list[dict[str, Any]],
    situation: str,
) -> tuple[str, str]:
    payload = {
        "actor": {
            "id": actor["id"],
            "name": actor["name"],
            "summary": actor["summary"],
            "current_state": actor_attributes,
            "traits": actor_traits,
            "short_term_goals": actor_attributes.get("goals", []),
            "relationships_to_present_characters": actor_relationships,
        },
        "scene": scene,
        "conversation_state": conversation_state,
        "active_story_threads": active_story_threads,
        "present_characters": present_characters,
        "recent_scene_log": recent_scene_log[-6:],
        "task": {
            "situation": situation,
            "instruction": get_prompt("character_task_instruction"),
        },
        "required_json": {
            "speech": "一句自然中文台词",
            "inner": "不超过15字，可空",
            "action": {
                "type": "行动类型，如 ask/help/move/take/work/buy/sell/argue",
                "target": "目标角色id、地点id或null",
                "detail": "行动细节",
            },
            "to": "被直接说话的角色id或null",
            "topic": "本句延续或新开的简短话题；无变化可空",
        },
    }
    return get_prompt("character_system"), dumps(payload)


def build_judge_prompt(
    *,
    actor: dict[str, Any],
    turn: dict[str, Any],
    scene: dict[str, Any] | None,
    present_characters: list[dict[str, Any]],
    recent_scene_log: list[dict[str, Any]],
) -> tuple[str, str]:
    payload = {
        "actor": actor,
        "turn": turn,
        "scene": scene,
        "present_characters": present_characters,
        "recent_scene_log": recent_scene_log[-6:],
        "task": {
            "instruction": "判断 actor 本拍 speech/action 对在场角色的直接影响。只输出角色属性 delta；没有明确影响就返回空 deltas。",
            "allowed_character_fields": ["mood", "energy", "health", "money"],
            "allowed_relationship_fields": ["affection", "trust", "respect", "familiarity"],
            "relationship_contract": "relationship 的 source 必须是 actor id，ref 必须是 actor 本拍直接互动的在场角色。",
            "delta_scale": "轻微影响 1-2，中等影响 3-5，强影响 6-10。避免每拍都改数值。",
        },
    }
    return get_prompt("judge_system"), dumps(payload)


def world_system_prompt() -> str:
    return get_prompt("world_system")
