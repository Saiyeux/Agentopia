from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .db import (
    connect,
    dumps,
    loads,
    reset_character_state,
    reset_world,
    seed_character_defaults,
    seed_demo,
    set_character_attribute,
    set_character_trait,
    value_from_storage,
)
from .engine import run_character_action, step_once
from .engine import summarize_applied
from .executor import Delta, append_verdict_log, apply_deltas
from .filtering import sanitize_display_text
from .llm import LlmSettings, get_settings, list_models, public_settings, save_settings
from .prompt_store import ensure_prompt_files, get_prompt, list_prompts, reset_prompt, save_prompt
from .scheduler import current_or_open_scene, scene_prompt_slice
from .threads import active_thread_slice


app = FastAPI(title="Agentopia API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CharacterCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    name: str
    summary: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    traits: dict[str, int] = Field(default_factory=dict)


class AttributeDefCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    label: str
    value_type: str = Field(pattern=r"^(int|float|text|json|bool)$")
    category: str = "state"
    min_value: float | None = None
    max_value: float | None = None
    default_value: Any = None
    description: str = ""
    mutable: bool = True


class TraitDefCreate(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]+$")
    label: str
    category: str = "personality"
    scale_min: int = 0
    scale_max: int = 100
    default_score: int = 50
    description: str = ""
    drift_per_chapter: int = 3


class ValueUpdate(BaseModel):
    value: Any


class TraitUpdate(BaseModel):
    score: int
    locked: bool = False


class SceneLogCreate(BaseModel):
    actor_id: str | None = None
    type: str
    content: str
    data: dict[str, Any] = Field(default_factory=dict)
    visibility: str = "all"


class LlmActPayload(BaseModel):
    char_id: str = "char_ghost"
    situation: str = "来生酒吧刚进入夜间高峰，又是一个寻找机会的时段。"


class EngineStepPayload(BaseModel):
    situation: str | None = None


class OpeningPayload(BaseModel):
    content: str | None = None


class DeltaPayload(BaseModel):
    target: str = Field(pattern=r"^(character_attribute|relationship|world_state)$")
    ref: str
    field: str
    op: str = Field(pattern=r"^(add|set)$")
    value: Any
    reason: str = ""
    source: str | None = None


class VerdictApplyPayload(BaseModel):
    actor_id: str = "JUDGE"
    narration: str
    deltas: list[DeltaPayload]


class LlmSettingsPayload(BaseModel):
    provider: str = Field(pattern=r"^(lmstudio|ollama|api)$")
    base_url: str
    model: str
    api_key: str = ""


class PromptPayload(BaseModel):
    content: str


@app.on_event("startup")
def startup() -> None:
    ensure_prompt_files()
    seed_demo()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/dev/seed")
def dev_seed() -> dict[str, str]:
    seed_demo()
    return {"status": "seeded"}


@app.post("/api/dev/reset-characters")
def dev_reset_characters() -> dict[str, str]:
    reset_character_state()
    return {"status": "characters_reset"}


@app.post("/api/dev/reset-world")
def dev_reset_world() -> dict[str, str]:
    """完全重置世界：删除所有旧角色和日志，重新seed新的赛博朋克角色"""
    reset_world()
    return {"status": "world_reset"}


@app.get("/api/llm/status")
def llm_status() -> dict[str, Any]:
    try:
        settings = get_settings()
        models = list_models()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "settings": public_settings(settings), "models": models.get("data", [])}


@app.get("/api/llm/settings")
def get_llm_settings() -> dict[str, Any]:
    return public_settings()


@app.put("/api/llm/settings")
def update_llm_settings(payload: LlmSettingsPayload) -> dict[str, Any]:
    try:
        settings = save_settings(
            LlmSettings(
                provider=payload.provider,
                base_url=payload.base_url,
                model=payload.model,
                api_key=payload.api_key,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "settings": public_settings(settings)}


@app.get("/api/llm/models")
def get_llm_models() -> dict[str, Any]:
    try:
        settings = get_settings()
        models = list_models(settings)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "settings": public_settings(settings), "models": models.get("data", [])}


@app.post("/api/llm/test")
def test_llm_connection() -> dict[str, Any]:
    try:
        settings = get_settings()
        models = list_models(settings)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ok", "settings": public_settings(settings), "model_count": len(models.get("data", []))}


@app.get("/api/prompts")
def api_list_prompts() -> dict[str, Any]:
    return {"prompts": list_prompts()}


@app.get("/api/prompts/{prompt_id}")
def api_get_prompt(prompt_id: str) -> dict[str, Any]:
    try:
        prompt = next((item for item in list_prompts() if item["id"] == prompt_id), None)
        if prompt is None:
            raise KeyError(prompt_id)
        return {**prompt, "content": get_prompt(prompt_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {prompt_id}") from exc


@app.put("/api/prompts/{prompt_id}")
def api_save_prompt(prompt_id: str, payload: PromptPayload) -> dict[str, Any]:
    try:
        content = save_prompt(prompt_id, payload.content)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {prompt_id}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "saved", "id": prompt_id, "content": content}


@app.post("/api/prompts/{prompt_id}/reset")
def api_reset_prompt(prompt_id: str) -> dict[str, Any]:
    try:
        content = reset_prompt(prompt_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Prompt not found: {prompt_id}") from exc
    return {"status": "reset", "id": prompt_id, "content": content}


@app.get("/api/world")
def get_world() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM world_state WHERE id = 1").fetchone()
        log_count = conn.execute("SELECT COUNT(*) AS count FROM scene_log").fetchone()["count"]
        scene_row = conn.execute(
            """
            SELECT id, location_id, title, purpose, start_tick, end_tick, participants,
                   turn_budget, turn_count, status, state
            FROM scenes
            WHERE status = 'open'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="World state not initialized")
    current_scene = scene_to_public(scene_row) if scene_row is not None else None
    with connect() as conn:
        active_threads = active_thread_slice(conn, current_scene, limit=5) if current_scene is not None else []
    return {
        "sim_tick": row["sim_tick"],
        "day": row["day"],
        "chapter": row["chapter"],
        "period": row["period"],
        "weather": row["weather"],
        "tension": row["tension"],
        "economy_index": row["economy_index"],
        "active_threads": active_threads or loads(row["active_threads"], []),
        "worldview": row["worldview"],
        "log_count": log_count,
        "current_scene": current_scene,
    }


def scene_to_public(row: Any) -> dict[str, Any]:
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


@app.get("/api/locations")
def list_locations() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM locations ORDER BY id").fetchall()
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "is_public": bool(row["is_public"]),
            "state": loads(row["state"], {}),
            "connected": loads(row["connected"], []),
        }
        for row in rows
    ]


@app.get("/api/scenes/current")
def get_current_scene() -> dict[str, Any]:
    with connect() as conn:
        scene = current_or_open_scene(conn)
        return scene_prompt_slice(conn, scene)


@app.get("/api/attribute-defs")
def list_attribute_defs() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM attribute_defs ORDER BY category, id").fetchall()
    return [dict(row) for row in rows]


@app.post("/api/attribute-defs")
def create_attribute_def(payload: AttributeDefCreate) -> dict[str, Any]:
    default_value = payload.default_value
    if payload.value_type == "json":
        stored_default = dumps(default_value if default_value is not None else {})
    elif payload.value_type == "bool":
        stored_default = "true" if bool(default_value) else "false"
    elif default_value is None:
        stored_default = ""
    else:
        stored_default = str(default_value)

    with connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO attribute_defs
                  (id, label, value_type, category, min_value, max_value, default_value, description, mutable)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.id,
                    payload.label,
                    payload.value_type,
                    payload.category,
                    payload.min_value,
                    payload.max_value,
                    stored_default,
                    payload.description,
                    int(payload.mutable),
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": payload.id, "status": "created"}


@app.get("/api/trait-defs")
def list_trait_defs() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM trait_defs ORDER BY category, id").fetchall()
    return [dict(row) for row in rows]


@app.post("/api/trait-defs")
def create_trait_def(payload: TraitDefCreate) -> dict[str, Any]:
    if payload.scale_min >= payload.scale_max:
        raise HTTPException(status_code=400, detail="scale_min must be lower than scale_max")
    score = max(payload.scale_min, min(payload.scale_max, payload.default_score))
    with connect() as conn:
        try:
            conn.execute(
                """
                INSERT INTO trait_defs
                  (id, label, category, scale_min, scale_max, default_score, description, drift_per_chapter)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.id,
                    payload.label,
                    payload.category,
                    payload.scale_min,
                    payload.scale_max,
                    score,
                    payload.description,
                    payload.drift_per_chapter,
                ),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": payload.id, "status": "created"}


@app.get("/api/events/defs")
def list_event_defs() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, tier, condition, cooldown, weight, guidance, narration, enabled, last_fired_tick
            FROM event_defs
            ORDER BY tier, id
            """
        ).fetchall()
    return [dict(row) for row in rows]


@app.get("/api/events/instances")
def list_event_instances(limit: int = 50) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM event_instances
            ORDER BY id DESC
            LIMIT ?
            """,
            (min(limit, 200),),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["payload"] = loads(row["payload"], {})
        items.append(item)
    return list(reversed(items))


@app.get("/api/characters")
def list_characters() -> list[dict[str, Any]]:
    with connect() as conn:
        characters = conn.execute("SELECT * FROM characters ORDER BY id").fetchall()
        attr_rows = conn.execute(
            """
            SELECT ca.char_id, ad.id, ad.label, ad.value_type, ad.category, ca.value
            FROM character_attributes ca
            JOIN attribute_defs ad ON ad.id = ca.attr_id
            ORDER BY ad.category, ad.id
            """
        ).fetchall()
        trait_rows = conn.execute(
            """
            SELECT ct.char_id, td.id, td.label, td.category, td.scale_min, td.scale_max, ct.score, ct.locked
            FROM character_traits ct
            JOIN trait_defs td ON td.id = ct.trait_id
            ORDER BY td.category, td.id
            """
        ).fetchall()
        relationship_rows = conn.execute(
            """
            SELECT r.from_id, r.to_id, c.name AS to_name,
                   r.affection, r.trust, r.respect, r.familiarity, r.updated_tick
            FROM relationships r
            JOIN characters c ON c.id = r.to_id
            ORDER BY r.from_id, r.updated_tick DESC, r.to_id
            """
        ).fetchall()

    by_id = {
        row["id"]: {
            "id": row["id"],
            "name": row["name"],
            "summary": row["summary"],
            "attributes": {},
            "traits": {},
            "relationships": [],
        }
        for row in characters
    }
    for row in attr_rows:
        target = by_id.get(row["char_id"])
        if target is not None:
            target["attributes"][row["id"]] = {
                "label": row["label"],
                "category": row["category"],
                "value": value_from_storage(row["value_type"], row["value"]),
            }
    for row in trait_rows:
        target = by_id.get(row["char_id"])
        if target is not None:
            target["traits"][row["id"]] = {
                "label": row["label"],
                "category": row["category"],
                "score": row["score"],
                "locked": bool(row["locked"]),
                "scale_min": row["scale_min"],
                "scale_max": row["scale_max"],
            }
    for row in relationship_rows:
        target = by_id.get(row["from_id"])
        if target is not None:
            target["relationships"].append(
                {
                    "to_id": row["to_id"],
                    "name": row["to_name"],
                    "affection": row["affection"],
                    "trust": row["trust"],
                    "respect": row["respect"],
                    "familiarity": row["familiarity"],
                    "updated_tick": row["updated_tick"],
                }
            )
    return list(by_id.values())


@app.post("/api/characters")
def create_character(payload: CharacterCreate) -> dict[str, str]:
    with connect() as conn:
        try:
            conn.execute(
                "INSERT INTO characters (id, name, summary) VALUES (?, ?, ?)",
                (payload.id, payload.name, payload.summary),
            )
            seed_character_defaults(conn, payload.id)
            for attr_id, value in payload.attributes.items():
                set_character_attribute(conn, payload.id, attr_id, value)
            for trait_id, score in payload.traits.items():
                set_character_trait(conn, payload.id, trait_id, score)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": payload.id, "status": "created"}


@app.put("/api/characters/{char_id}/attributes/{attr_id}")
def update_character_attribute(char_id: str, attr_id: str, payload: ValueUpdate) -> dict[str, str]:
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM characters WHERE id = ?", (char_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Character not found")
        try:
            set_character_attribute(conn, char_id, attr_id, payload.value)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated"}


@app.put("/api/characters/{char_id}/traits/{trait_id}")
def update_character_trait(char_id: str, trait_id: str, payload: TraitUpdate) -> dict[str, str]:
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM characters WHERE id = ?", (char_id,)).fetchone()
        if exists is None:
            raise HTTPException(status_code=404, detail="Character not found")
        try:
            set_character_trait(conn, char_id, trait_id, payload.score)
            conn.execute(
                "UPDATE character_traits SET locked = ? WHERE char_id = ? AND trait_id = ?",
                (int(payload.locked), char_id, trait_id),
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "updated"}


@app.get("/api/scene-log")
def list_scene_log(after_id: int = 0, limit: int = 100) -> list[dict[str, Any]]:
    with connect() as conn:
        safe_limit = min(limit, 500)
        if after_id > 0:
            rows = conn.execute(
                """
                SELECT * FROM scene_log
                WHERE id > ?
                ORDER BY id
                LIMIT ?
                """,
                (after_id, safe_limit),
            ).fetchall()
        else:
            rows = list(
                reversed(
                    conn.execute(
                        """
                        SELECT * FROM scene_log
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (safe_limit,),
                    ).fetchall()
                )
            )
    items: list[dict[str, Any]] = []
    for row in rows:
        data = loads(row["data"], {})
        items.append(
            {
                "id": row["id"],
                "scene_id": row["scene_id"],
                "tick": row["tick"],
                "actor_id": row["actor_id"],
                "type": row["type"],
                "content": sanitize_log_content(row["type"], row["content"], data),
                "data": data,
                "visibility": row["visibility"],
                "created_at": row["created_at"],
            }
        )
    return items


def sanitize_log_content(log_type: str, content: str, data: dict[str, Any] | None = None) -> str:
    if log_type == "verdict":
        applied = (data or {}).get("applied")
        if isinstance(applied, list):
            return summarize_applied(applied)
        return sanitize_display_text(content, "裁定完成，本拍无数值变化。")
    if log_type == "speech":
        return sanitize_display_text(content, "")
    return sanitize_display_text(content, "世界安静了一瞬。")


@app.post("/api/scene-log")
def append_scene_log(payload: SceneLogCreate) -> dict[str, Any]:
    with connect() as conn:
        world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
        tick = world["sim_tick"] if world else 0
        scene = current_or_open_scene(conn)
        cursor = conn.execute(
            """
            INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (scene["id"], tick, payload.actor_id, payload.type, payload.content, dumps(payload.data), payload.visibility),
        )
    return {"id": cursor.lastrowid, "status": "created"}


@app.post("/api/scene-log/clear")
def clear_scene_log() -> dict[str, Any]:
    with connect() as conn:
        conn.execute("DELETE FROM scene_log")
        conn.execute("DELETE FROM story_beats")
        conn.execute("DELETE FROM story_threads")
        conn.execute("DELETE FROM event_instances")
        conn.execute("DELETE FROM scenes")
        conn.execute("UPDATE event_defs SET last_fired_tick = -999999")
        conn.execute("UPDATE world_state SET sim_tick = 0, day = 0, chapter = 0 WHERE id = 1")
    return {"status": "cleared"}


@app.post("/api/executor/apply")
def apply_verdict(payload: VerdictApplyPayload) -> dict[str, Any]:
    deltas = [
        Delta(
            target=delta.target,  # type: ignore[arg-type]
            ref=delta.ref,
            field=delta.field,
            op=delta.op,  # type: ignore[arg-type]
            value=delta.value,
            reason=delta.reason,
            source=delta.source,
        )
        for delta in payload.deltas
    ]
    with connect() as conn:
        applied = apply_deltas(conn, deltas)
        log_id = append_verdict_log(conn, payload.actor_id, payload.narration, applied)
    return {"status": "ok", "log_id": log_id, "applied": applied}


@app.post("/api/engine/step")
def engine_step(payload: EngineStepPayload) -> dict[str, Any]:
    try:
        with connect() as conn:
            result = step_once(conn, payload.situation)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", **result}


@app.post("/api/engine/opening")
def engine_opening(payload: OpeningPayload) -> dict[str, Any]:
    with connect() as conn:
        world = conn.execute("SELECT worldview FROM world_state WHERE id = 1").fetchone()
        worldview = world["worldview"] if world else "夜之城，来生酒吧。"
        tick_row = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
        tick = tick_row["sim_tick"] if tick_row else 0
        scene = current_or_open_scene(conn)

        location = conn.execute("SELECT * FROM locations WHERE id = ?", (scene["location_id"],)).fetchone()
        if location is None:
            raise HTTPException(status_code=500, detail=f"当前场景地点不存在: {scene['location_id']}")
        location_desc = location["description"]
        location_name = location["name"]
        location_state = loads(location["state"], {})
        recent_rows = conn.execute(
            """
            SELECT content FROM scene_log
            WHERE type = 'narration'
            ORDER BY id DESC
            LIMIT 6
            """
        ).fetchall()
        recent_openings = [row["content"] for row in recent_rows]

        system_prompt = get_prompt("opening_system")

        user_prompt = f"""世界观：{worldview}
地点：{location_name}
地点事实：{location_desc}
场景数值：{dumps(location_state)}
最近已用开场：{dumps(recent_openings)}

请生成一句全新的开场："""

        try:
            from .llm import chat_json_schema
            import random

            opening_schema = {
                "type": "object",
                "properties": {"opening": {"type": "string"}},
                "required": ["opening"],
                "additionalProperties": False,
            }
            source_texts = [worldview, location_desc, *recent_openings]
            attempts = [
                user_prompt,
                f"""上一轮没有按契约输出。请只返回 JSON，不要分析。

地点：{location_name}
地点事实：{location_desc}
场景数值：{dumps(location_state)}
最近已用开场：{dumps(recent_openings)}

返回格式：{{"opening":"一句20到40个汉字的环境开场"}}""",
            ]
            errors: list[str] = []
            content = ""
            for index, prompt in enumerate(attempts):
                try:
                    temp = 0.82 + random.uniform(-0.08, 0.12) if index == 0 else 0.65
                    result = chat_json_schema(
                        schema_name="agentopia_opening",
                        schema=opening_schema,
                        system=system_prompt,
                        user=prompt,
                        temperature=temp,
                        max_tokens=120 if index == 0 else 80,
                    )
                    parsed = result.get("parsed", {})
                    raw_content = str(parsed.get("opening") or result.get("content", ""))
                    content = normalize_opening_content(raw_content, source_texts=source_texts)
                    break
                except Exception as exc:
                    errors.append(str(exc))
            if not content:
                raise ValueError("; ".join(errors[-2:]) or "LLM 未返回合格开场")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"首句生成失败: {exc}") from exc

        cursor = conn.execute(
            """
            INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
            VALUES (?, ?, 'WORLD', 'narration', ?, '{}', 'all')
            """,
            (scene["id"], tick, content),
        )
    return {"status": "ok", "log_id": cursor.lastrowid, "content": content}


def normalize_opening_content(raw_content: str, source_texts: list[str] | None = None) -> str:
    content = raw_content.strip().strip('"\'`')
    if not content:
        raise ValueError("LLM 未返回任何开场内容")
    if content.startswith("```") and content.endswith("```"):
        lines = content.splitlines()
        content = "\n".join(lines[1:-1]).strip()
    prefixes = [
        "开场白：",
        "开场白:",
        "narration:",
        "Narration:",
        "NARRATION:",
        "输出：",
        "输出:",
        "Output:",
        "output:",
        "→ 输出：",
        "→ 输出:",
    ]
    for prefix in prefixes:
        if content.startswith(prefix):
            content = content[len(prefix) :].strip()
    content = re.sub(r"<[^>]+>", " ", content)
    content = " ".join(content.split())
    if _is_valid_opening_sentence(content, source_texts=source_texts):
        cleaned = sanitize_display_text(content, "")
        if cleaned:
            return cleaned
    candidates = re.findall(r"[\u4e00-\u9fff][\u4e00-\u9fff0-9，、；：：“”‘’（）《》\s]{8,80}[。！？]?", content)
    for candidate in candidates:
        candidate = candidate.strip(" ，,;；")
        if not _is_valid_opening_sentence(candidate, source_texts=source_texts):
            continue
        cleaned = sanitize_display_text(candidate, "")
        if 10 <= len(cleaned) <= 90:
            return cleaned
    raise ValueError(f"LLM 开场内容不可用: {raw_content[:200]}")


def _is_valid_opening_sentence(text: str, source_texts: list[str] | None = None) -> bool:
    if not 10 <= len(text) <= 90:
        return False
    cjk_count = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    ascii_letters = sum(1 for char in text if char.isascii() and char.isalpha())
    if cjk_count < 10:
        return False
    if ascii_letters:
        return False
    blocked_markers = (
        "开场白",
        "世界观",
        "场景描述",
        "地点事实",
        "场景数值",
        "最近已用",
        "请直接",
        "请生成",
        "生成一句",
        "全新的开场",
        "一句中文",
        "输出",
        "要求",
        "不能",
        "需要",
        "不要",
        "没有具体角色",
        "角色名称",
    )
    if any(marker in text for marker in blocked_markers):
        return False
    if _copies_source_text(text, source_texts or []):
        return False
    return True


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
        window = 8
        chunks = {compact[index : index + window] for index in range(0, max(1, len(compact) - window + 1), window)}
        hits = sum(1 for chunk in chunks if len(chunk) == window and chunk in source_compact)
        if hits >= 2:
            return True
    return False


def _compact_cjk(text: str) -> str:
    return "".join(char for char in text if "\u4e00" <= char <= "\u9fff")


@app.post("/api/dev/llm-act")
def dev_llm_act(payload: LlmActPayload) -> dict[str, Any]:
    try:
        with connect() as conn:
            result = run_character_action(conn, payload.char_id, payload.situation)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", **result}
