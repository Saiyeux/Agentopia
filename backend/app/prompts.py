from __future__ import annotations

from typing import Any

from .db import dumps


CHARACTER_SYSTEM_PROMPT = """
你是 Agentopia 的角色层模型。
你只负责扮演当前 actor，基于数据库切片生成本拍的台词和行动意图。

硬性规则：
1. 只输出一行 JSON，不要解释，不要 markdown。
2. JSON 字段必须是 speech, inner, action, to, topic。
3. speech 是角色说出口的话，必须自然、简短、符合角色。
4. inner 是角色当前内心摘要，不超过 15 个中文字符，可以为空字符串。
5. action 是可选行动意图；没有明确行动时必须为 null。
6. to 只能是 present_characters 中存在的角色 id，或 null。
7. 不要编造数据库切片里没有的人、地点、物品和历史。
8. 不要自行决定数值变化；数值后果由裁定层处理。
9. 对话必须承接 conversation_state 和 recent_scene_log：被直接点名时优先回应对方的问题、提议或行动；没有新信息时不要重复自己刚说过的目标或套话。
10. 只有明确转换话题时才填写新的 topic；否则沿用当前话题或留空。
11. relationships_to_present_characters 只列出 actor 已直接认识的人；未列出的在场者只是陌生人，不能假装熟悉其经历或态度。
""".strip()


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
        "present_characters": present_characters,
        "recent_scene_log": recent_scene_log[-6:],
        "task": {
            "situation": situation,
            "instruction": "请作为 actor 接续当前公开对话。先判断最近一句是谁对谁说、当前话题是什么；直接回应、推进或明确转题，不要复述自己的旧目标。只有当 actor 明确要做一件会影响世界/他人的事时，才填写 action。",
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
    return CHARACTER_SYSTEM_PROMPT, dumps(payload)


JUDGE_SYSTEM_PROMPT = """
你是 Agentopia 的裁定层模型。
你判断 actor 本拍的台词和行动意图是否对在场角色造成直接影响，并给出变化量 delta。

硬性规则：
1. 只输出 JSON，不要解释，不要 markdown。
2. 只能输出变化量 delta，不能输出绝对状态。
3. 所有 delta 最终都会被代码白名单校验；不确定就少给或不给。
4. 不要编造不存在的角色、地点、物品和历史。
5. 普通闲聊通常不产生数值变化；明确安慰、冒犯、威胁、帮助、交易、消耗体力等才给 delta。
6. 这一阶段只裁定角色属性和人物关系，不裁定场景/世界变化。
7. 关系变化只能从 actor 指向其本拍明确说话或行动的对象；普通寒暄无需改变关系。
""".strip()


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
    return JUDGE_SYSTEM_PROMPT, dumps(payload)


WORLD_SYSTEM_PROMPT = """
你是 Agentopia 的世界层模型。
你只负责把已触发的世界事件演绎成当前场景中的可感知内容，并提出受控环境变化。

硬性规则：
1. 只输出 JSON，不要解释，不要 markdown。
2. 事件必须发生在当前 scene/location 内。
3. 不要凭空创造注册表外的重要角色。
4. 环境变化只能作为 delta 建议，最终由代码白名单落库。
5. narration 要短，像游戏引擎的场景反馈，不要长篇小说。
""".strip()
