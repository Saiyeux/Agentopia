from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "agentopia.sqlite3"


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def loads(value: str | None, fallback: Any = None) -> Any:
    if value is None or value == "":
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  summary TEXT NOT NULL DEFAULT '',
  created_tick INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attribute_defs (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK (value_type IN ('int','float','text','json','bool')),
  category TEXT NOT NULL DEFAULT 'state',
  min_value REAL,
  max_value REAL,
  default_value TEXT,
  description TEXT NOT NULL DEFAULT '',
  mutable INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS character_attributes (
  char_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  attr_id TEXT NOT NULL REFERENCES attribute_defs(id) ON DELETE CASCADE,
  value TEXT NOT NULL,
  updated_tick INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (char_id, attr_id)
);

CREATE TABLE IF NOT EXISTS trait_defs (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'personality',
  scale_min INTEGER NOT NULL DEFAULT 0,
  scale_max INTEGER NOT NULL DEFAULT 100,
  default_score INTEGER NOT NULL DEFAULT 50,
  description TEXT NOT NULL DEFAULT '',
  drift_per_chapter INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS character_traits (
  char_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  trait_id TEXT NOT NULL REFERENCES trait_defs(id) ON DELETE CASCADE,
  score INTEGER NOT NULL,
  locked INTEGER NOT NULL DEFAULT 0,
  updated_tick INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (char_id, trait_id)
);

CREATE TABLE IF NOT EXISTS relationships (
  from_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  to_id TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
  affection INTEGER NOT NULL DEFAULT 0,
  trust INTEGER NOT NULL DEFAULT 0,
  respect INTEGER NOT NULL DEFAULT 0,
  familiarity INTEGER NOT NULL DEFAULT 0,
  memory_text TEXT NOT NULL DEFAULT '',
  updated_tick INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (from_id, to_id),
  CHECK (from_id <> to_id)
);

CREATE TABLE IF NOT EXISTS world_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  sim_tick INTEGER NOT NULL DEFAULT 0,
  day INTEGER NOT NULL DEFAULT 0,
  chapter INTEGER NOT NULL DEFAULT 0,
  period TEXT NOT NULL DEFAULT 'morning',
  weather TEXT NOT NULL DEFAULT 'clear',
  tension INTEGER NOT NULL DEFAULT 0,
  economy_index INTEGER NOT NULL DEFAULT 50,
  active_threads TEXT NOT NULL DEFAULT '[]',
  worldview TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS locations (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  is_public INTEGER NOT NULL DEFAULT 1,
  state TEXT NOT NULL DEFAULT '{}',
  connected TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS scenes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  location_id TEXT NOT NULL REFERENCES locations(id),
  title TEXT NOT NULL DEFAULT '',
  purpose TEXT NOT NULL DEFAULT '',
  start_tick INTEGER NOT NULL,
  end_tick INTEGER,
  participants TEXT NOT NULL DEFAULT '[]',
  turn_budget INTEGER NOT NULL DEFAULT 24,
  turn_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'open',
  state TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scene_attribute_defs (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  value_type TEXT NOT NULL CHECK (value_type IN ('int','float','text','json','bool')),
  category TEXT NOT NULL DEFAULT 'scene',
  min_value REAL,
  max_value REAL,
  default_value TEXT,
  description TEXT NOT NULL DEFAULT '',
  mutable INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS scene_attributes (
  scene_id INTEGER NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
  attr_id TEXT NOT NULL REFERENCES scene_attribute_defs(id) ON DELETE CASCADE,
  value TEXT NOT NULL,
  updated_tick INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (scene_id, attr_id)
);

CREATE TABLE IF NOT EXISTS scene_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scene_id INTEGER REFERENCES scenes(id),
  tick INTEGER NOT NULL,
  actor_id TEXT,
  type TEXT NOT NULL,
  content TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  visibility TEXT NOT NULL DEFAULT 'all',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scene_conversations (
  scene_id INTEGER PRIMARY KEY REFERENCES scenes(id) ON DELETE CASCADE,
  topic TEXT NOT NULL DEFAULT '',
  last_speaker_id TEXT REFERENCES characters(id),
  last_recipient_id TEXT REFERENCES characters(id),
  last_content TEXT NOT NULL DEFAULT '',
  heard_by TEXT NOT NULL DEFAULT '[]',
  updated_tick INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS event_defs (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  tier TEXT NOT NULL CHECK (tier IN ('scheduled','weighted','emergent')),
  condition TEXT NOT NULL,
  cooldown INTEGER NOT NULL DEFAULT 0,
  weight INTEGER NOT NULL DEFAULT 100,
  guidance TEXT NOT NULL DEFAULT '',
  narration TEXT NOT NULL DEFAULT '',
  effects TEXT NOT NULL DEFAULT '[]',
  enabled INTEGER NOT NULL DEFAULT 1,
  last_fired_tick INTEGER NOT NULL DEFAULT -999999
);

CREATE TABLE IF NOT EXISTS event_instances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  def_id TEXT NOT NULL REFERENCES event_defs(id),
  scene_id INTEGER REFERENCES scenes(id),
  tier TEXT NOT NULL,
  fired_tick INTEGER NOT NULL,
  location_id TEXT REFERENCES locations(id),
  narration TEXT NOT NULL,
  payload TEXT NOT NULL DEFAULT '{}',
  visibility TEXT NOT NULL DEFAULT 'all',
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS llm_settings (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  provider TEXT NOT NULL DEFAULT 'lmstudio' CHECK (provider IN ('lmstudio','ollama','api')),
  base_url TEXT NOT NULL DEFAULT 'http://127.0.0.1:1234/v1',
  model TEXT NOT NULL DEFAULT 'qwen-agentworld-35b-a3b',
  api_key TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_character_attributes_attr ON character_attributes(attr_id);
CREATE INDEX IF NOT EXISTS idx_character_traits_trait ON character_traits(trait_id);
CREATE INDEX IF NOT EXISTS idx_scenes_status ON scenes(status, id);
CREATE INDEX IF NOT EXISTS idx_scene_log_tick ON scene_log(tick, id);
CREATE INDEX IF NOT EXISTS idx_scene_conversations_tick ON scene_conversations(updated_tick, scene_id);
CREATE INDEX IF NOT EXISTS idx_event_instances_tick ON event_instances(fired_tick, id);
"""


DEFAULT_ATTRIBUTES = [
    ("location", "位置", "text", "state", None, None, "hall", "角色当前所在地点", 1),
    ("mood", "心情", "int", "state", 0, 100, "50", "当前心情，0-100", 1),
    ("energy", "精力", "int", "state", 0, 100, "100", "当前精力，0-100", 1),
    ("health", "健康", "int", "state", 0, 100, "100", "当前健康，0-100", 1),
    ("money", "金钱", "int", "resource", None, None, "0", "可用于交易的货币", 1),
    ("goals", "当前目标", "json", "state", None, None, "[]", "短期目标数组", 1),
]


DEFAULT_TRAITS = [
    ("extraversion", "外向", "personality", 0, 100, 50, "越高越主动社交", 3),
    ("aggression", "攻击性", "personality", 0, 100, 35, "越高越容易冲突", 3),
    ("honesty", "诚实", "personality", 0, 100, 60, "越高越不擅欺骗", 3),
    ("caution", "谨慎", "personality", 0, 100, 55, "越高越保守审慎", 3),
    ("empathy", "共情", "personality", 0, 100, 55, "越高越在意他人感受", 3),
    ("self_control", "自制力", "personality", 0, 100, 55, "越高越能压住冲动", 3),
]


DEFAULT_LOCATIONS = [
    (
        "hall",
        "旧酒馆大厅",
        "酒馆最热闹的公共空间，吧台、木桌、炉火和门口的雨声都在这里交汇。",
        1,
        {"crowd": 45, "noise": 35, "warmth": 55, "order": 65},
        ["street", "kitchen"],
    ),
    (
        "street",
        "雨夜街角",
        "酒馆门外的石板路潮湿发亮，行人少，消息和麻烦都可能从这里进门。",
        1,
        {"crowd": 15, "noise": 25, "warmth": 20, "order": 45},
        ["hall"],
    ),
    (
        "kitchen",
        "后厨",
        "窄小温热的后厨，堆着酒桶、汤锅和不该让客人看见的账本。",
        0,
        {"crowd": 10, "noise": 30, "warmth": 75, "order": 50},
        ["hall"],
    ),
]


DEFAULT_SCENE_ATTRIBUTES = [
    ("crowd", "人气", "int", "atmosphere", 0, 100, "45", "当前场景里人群密度", 1),
    ("noise", "嘈杂", "int", "atmosphere", 0, 100, "35", "当前场景的声音强度", 1),
    ("warmth", "暖意", "int", "atmosphere", 0, 100, "55", "空间给人的温暖程度", 1),
    ("order", "秩序", "int", "atmosphere", 0, 100, "65", "越高越稳定，越低越混乱", 1),
]


DEFAULT_EVENT_DEFS = [
    (
        "scheduled_crowd_shift",
        "客流变化",
        "scheduled",
        "sim_tick > 0 and sim_tick % 12 == 0",
        12,
        100,
        "每隔一段时间让酒馆环境发生轻微变化。",
        "门外的雨声换了调子，几名过路客推门张望又缩回夜色里。",
        [
            {"target": "scene_attribute", "field": "crowd", "op": "add", "value": -3, "reason": "过路客没有进门"},
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": -2, "reason": "门外人声远去"},
        ],
        1,
    ),
    (
        "weighted_old_floor_creak",
        "旧地板异响",
        "weighted",
        "sim_tick > 0 and sim_tick % 7 == 0",
        7,
        45,
        "给当前场景加入轻微环境扰动。",
        "旧木地板忽然吱呀一声，酒馆里短暂安静了半拍。",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": 4, "reason": "地板异响"},
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -2, "reason": "众人被声音打断"},
        ],
        1,
    ),
    (
        "weighted_rain_leak",
        "屋檐漏雨",
        "weighted",
        "weather == 'rain'",
        10,
        25,
        "雨天可能触发屋檐漏雨，让角色注意到地点状态。",
        "屋檐滴下的雨水沿着门框淌进来，在地上积出一小片亮痕。",
        [
            {"target": "scene_attribute", "field": "warmth", "op": "add", "value": -3, "reason": "雨水渗入"},
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -4, "reason": "地面开始湿滑"},
        ],
        1,
    ),
    (
        "emergent_tension_spike",
        "气氛绷紧",
        "emergent",
        "tension >= 60",
        12,
        100,
        "世界紧张度过高时，提醒角色冲突正在靠近。",
        "空气像被拉紧的弦，几道目光在酒杯与门口之间来回扫过。",
        [
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -8, "reason": "气氛紧绷"},
            {"target": "world_state", "field": "tension", "op": "add", "value": -2, "reason": "压力被事件显性化"},
        ],
        1,
    ),
]


SEED_CHARACTERS = [
    {
        "id": "char_lin",
        "name": "林澈",
        "summary": "落魄但嘴硬的前护卫，在酒馆里寻找下一份差事。",
        "attributes": {"money": 12, "goals": ["找到稳定工作", "避免欠债扩大"]},
        "traits": {"caution": 68, "self_control": 62, "aggression": 28},
    },
    {
        "id": "char_maya",
        "name": "玛雅",
        "summary": "酒馆老板，精明、耐心有限，但很会看人。",
        "attributes": {"money": 80, "goals": ["维持酒馆秩序", "让今晚别亏本"]},
        "traits": {"extraversion": 70, "honesty": 58, "empathy": 48, "self_control": 74},
    },
    {
        "id": "char_ren",
        "name": "任十七",
        "summary": "跑腿信使，消息灵通、嘴快，习惯把危险说成小麻烦。",
        "attributes": {"money": 24, "goals": ["卖出一条有价消息", "别被债主认出来"]},
        "traits": {"extraversion": 82, "caution": 42, "honesty": 36, "self_control": 44},
    },
    {
        "id": "char_su",
        "name": "苏棠",
        "summary": "旅行药师，温和但警觉，正在寻找一种被管制的药材。",
        "attributes": {"money": 47, "goals": ["补齐药材", "判断酒馆里谁值得信任"]},
        "traits": {"empathy": 76, "caution": 72, "honesty": 67, "aggression": 18},
    },
    {
        "id": "char_hao",
        "name": "郝铁",
        "summary": "码头搬运工，嗓门大、脾气急，今晚刚丢了一袋货。",
        "attributes": {"money": 9, "goals": ["找回丢失的货", "喝点东西压住火气"]},
        "traits": {"extraversion": 64, "aggression": 72, "self_control": 31, "empathy": 39},
    },
]


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            """
            INSERT OR IGNORE INTO world_state
              (id, worldview, active_threads)
            VALUES
              (1, ?, ?)
            """,
            (
                "边境小镇的旧酒馆是消息、债务、临时工作和秘密交易的交汇点。",
                dumps([]),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO llm_settings
              (id, provider, base_url, model, api_key)
            VALUES
              (1, 'lmstudio', 'http://127.0.0.1:1234/v1', 'qwen-agentworld-35b-a3b', '')
            """
        )
        _migrate_existing_tables(conn)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_scene_log_scene ON scene_log(scene_id, tick, id)")
        conn.executemany(
            """
            INSERT OR IGNORE INTO attribute_defs
              (id, label, value_type, category, min_value, max_value, default_value, description, mutable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            DEFAULT_ATTRIBUTES,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO trait_defs
              (id, label, category, scale_min, scale_max, default_score, description, drift_per_chapter)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            DEFAULT_TRAITS,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO locations
              (id, name, description, is_public, state, connected)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (id, name, description, is_public, dumps(state), dumps(connected))
                for id, name, description, is_public, state, connected in DEFAULT_LOCATIONS
            ],
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO scene_attribute_defs
              (id, label, value_type, category, min_value, max_value, default_value, description, mutable)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            DEFAULT_SCENE_ATTRIBUTES,
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO event_defs
              (id, title, tier, condition, cooldown, weight, guidance, narration, effects, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (id, title, tier, condition, cooldown, weight, guidance, narration, dumps(effects), enabled)
                for id, title, tier, condition, cooldown, weight, guidance, narration, effects, enabled in DEFAULT_EVENT_DEFS
            ],
        )
        for id, _title, _tier, _condition, _cooldown, _weight, _guidance, _narration, effects, _enabled in DEFAULT_EVENT_DEFS:
            conn.execute(
                "UPDATE event_defs SET effects = ? WHERE id = ? AND effects = '[]'",
                (dumps(effects), id),
            )


def seed_demo() -> None:
    init_db()
    with connect() as conn:
        for character in SEED_CHARACTERS:
            conn.execute(
                "INSERT OR IGNORE INTO characters (id, name, summary) VALUES (?, ?, ?)",
                (character["id"], character["name"], character["summary"]),
            )
            seed_character_defaults(conn, character["id"])
            for attr_id, value in character.get("attributes", {}).items():
                set_character_attribute(conn, character["id"], attr_id, value)
            for trait_id, score in character.get("traits", {}).items():
                set_character_trait(conn, character["id"], trait_id, score)

        conn.execute(
            """
            INSERT INTO scene_log (scene_id, tick, actor_id, type, content, data)
            SELECT NULL, 0, 'WORLD', 'narration', '旧酒馆刚刚开门，雨意压在街角。', '{}'
            WHERE NOT EXISTS (SELECT 1 FROM scene_log)
            """
        )


def reset_character_state() -> None:
    init_db()
    seeded_by_id = {character["id"]: character for character in SEED_CHARACTERS}
    with connect() as conn:
        conn.execute("DELETE FROM relationships")
        characters = conn.execute("SELECT id FROM characters ORDER BY rowid").fetchall()
        for row in characters:
            char_id = row["id"]
            _reset_character_defaults(conn, char_id)
            seed = seeded_by_id.get(char_id)
            if seed is not None:
                for attr_id, value in seed.get("attributes", {}).items():
                    set_character_attribute(conn, char_id, attr_id, value)
                for trait_id, score in seed.get("traits", {}).items():
                    set_character_trait(conn, char_id, trait_id, score)


def _reset_character_defaults(conn: sqlite3.Connection, char_id: str) -> None:
    attr_rows = conn.execute("SELECT id, default_value FROM attribute_defs").fetchall()
    for row in attr_rows:
        conn.execute(
            """
            INSERT INTO character_attributes (char_id, attr_id, value, updated_tick)
            VALUES (?, ?, ?, 0)
            ON CONFLICT(char_id, attr_id) DO UPDATE SET
              value = excluded.value,
              updated_tick = 0
            """,
            (char_id, row["id"], row["default_value"] or ""),
        )
    trait_rows = conn.execute("SELECT id, default_score FROM trait_defs").fetchall()
    for row in trait_rows:
        conn.execute(
            """
            INSERT INTO character_traits (char_id, trait_id, score, locked, updated_tick)
            VALUES (?, ?, ?, 0, 0)
            ON CONFLICT(char_id, trait_id) DO UPDATE SET
              score = excluded.score,
              locked = 0,
              updated_tick = 0
            """,
            (char_id, row["id"], row["default_score"]),
        )


def _migrate_existing_tables(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "scene_log", "scene_id", "ALTER TABLE scene_log ADD COLUMN scene_id INTEGER REFERENCES scenes(id)")
    _ensure_column(conn, "event_instances", "scene_id", "ALTER TABLE event_instances ADD COLUMN scene_id INTEGER REFERENCES scenes(id)")
    _ensure_column(conn, "event_defs", "effects", "ALTER TABLE event_defs ADD COLUMN effects TEXT NOT NULL DEFAULT '[]'")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if rows and column not in {row["name"] for row in rows}:
        conn.execute(ddl)


def seed_character_defaults(conn: sqlite3.Connection, char_id: str) -> None:
    attr_rows = conn.execute("SELECT id, default_value FROM attribute_defs").fetchall()
    for row in attr_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO character_attributes (char_id, attr_id, value)
            VALUES (?, ?, ?)
            """,
            (char_id, row["id"], row["default_value"] or ""),
        )

    trait_rows = conn.execute("SELECT id, default_score FROM trait_defs").fetchall()
    for row in trait_rows:
        conn.execute(
            """
            INSERT OR IGNORE INTO character_traits (char_id, trait_id, score)
            VALUES (?, ?, ?)
            """,
            (char_id, row["id"], row["default_score"]),
        )


def coerce_for_storage(defn: sqlite3.Row, value: Any) -> str:
    value_type = defn["value_type"]
    if value_type == "int":
        number = int(value)
        if defn["min_value"] is not None:
            number = max(number, int(defn["min_value"]))
        if defn["max_value"] is not None:
            number = min(number, int(defn["max_value"]))
        return str(number)
    if value_type == "float":
        number = float(value)
        if defn["min_value"] is not None:
            number = max(number, float(defn["min_value"]))
        if defn["max_value"] is not None:
            number = min(number, float(defn["max_value"]))
        return str(number)
    if value_type == "bool":
        return "true" if bool(value) else "false"
    if value_type == "json":
        return dumps(value)
    return str(value)


def value_from_storage(value_type: str, value: str) -> Any:
    if value_type == "int":
        return int(value or 0)
    if value_type == "float":
        return float(value or 0)
    if value_type == "bool":
        return value == "true"
    if value_type == "json":
        return loads(value, [])
    return value


def set_character_attribute(conn: sqlite3.Connection, char_id: str, attr_id: str, value: Any) -> None:
    defn = conn.execute("SELECT * FROM attribute_defs WHERE id = ?", (attr_id,)).fetchone()
    if defn is None:
        raise ValueError(f"Unknown attribute: {attr_id}")
    stored = coerce_for_storage(defn, value)
    conn.execute(
        """
        INSERT INTO character_attributes (char_id, attr_id, value)
        VALUES (?, ?, ?)
        ON CONFLICT(char_id, attr_id) DO UPDATE SET value = excluded.value
        """,
        (char_id, attr_id, stored),
    )


def set_character_trait(conn: sqlite3.Connection, char_id: str, trait_id: str, score: int) -> None:
    defn = conn.execute("SELECT * FROM trait_defs WHERE id = ?", (trait_id,)).fetchone()
    if defn is None:
        raise ValueError(f"Unknown trait: {trait_id}")
    clamped = max(int(defn["scale_min"]), min(int(defn["scale_max"]), int(score)))
    conn.execute(
        """
        INSERT INTO character_traits (char_id, trait_id, score)
        VALUES (?, ?, ?)
        ON CONFLICT(char_id, trait_id) DO UPDATE SET score = excluded.score
        """,
        (char_id, trait_id, clamped),
    )
