from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .db import (
    connect,
    dumps,
    loads,
    reset_character_state,
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
from .scheduler import current_or_open_scene, scene_prompt_slice


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
    char_id: str = "char_lin"
    situation: str = "旧酒馆刚开门，雨意压在街角。"


class EngineStepPayload(BaseModel):
    situation: str | None = None


class OpeningPayload(BaseModel):
    content: str = "旧酒馆刚刚开门，雨意压在街角。"


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


@app.on_event("startup")
def startup() -> None:
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
    return {
        "sim_tick": row["sim_tick"],
        "day": row["day"],
        "chapter": row["chapter"],
        "period": row["period"],
        "weather": row["weather"],
        "tension": row["tension"],
        "economy_index": row["economy_index"],
        "active_threads": loads(row["active_threads"], []),
        "worldview": row["worldview"],
        "log_count": log_count,
        "current_scene": scene_to_public(scene_row) if scene_row is not None else None,
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
        return sanitize_display_text(content, "沉默了一瞬。")
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
        world = conn.execute("SELECT sim_tick FROM world_state WHERE id = 1").fetchone()
        tick = world["sim_tick"] if world else 0
        scene = current_or_open_scene(conn)
        cursor = conn.execute(
            """
            INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data, visibility)
            VALUES (?, ?, 'WORLD', 'narration', ?, '{}', 'all')
            """,
            (scene["id"], tick, payload.content),
        )
    return {"status": "ok", "log_id": cursor.lastrowid}


@app.post("/api/dev/llm-act")
def dev_llm_act(payload: LlmActPayload) -> dict[str, Any]:
    try:
        with connect() as conn:
            result = run_character_action(conn, payload.char_id, payload.situation)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"status": "ok", **result}
