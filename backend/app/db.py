from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "agentopia.sqlite3"
NIGHT_CITY_WORLDVIEW = (
    "2077年夜之城，沃森区。巨型公司、帮派、中间人、佣兵和地下医疗网络共同塑造日常秩序。"
    "来生酒吧是交换情报、谈判条件、躲避追查和处理人情债的公共节点。"
)


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
  drama INTEGER NOT NULL DEFAULT 30,
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
  target_need TEXT,
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

CREATE TABLE IF NOT EXISTS story_threads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scene_id INTEGER REFERENCES scenes(id),
  source_event_id INTEGER REFERENCES event_instances(id),
  title TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  stage TEXT NOT NULL DEFAULT 'introduced',
  summary TEXT NOT NULL DEFAULT '',
  participants TEXT NOT NULL DEFAULT '[]',
  stakes TEXT NOT NULL DEFAULT '{}',
  priority INTEGER NOT NULL DEFAULT 50,
  beat_count INTEGER NOT NULL DEFAULT 0,
  created_tick INTEGER NOT NULL DEFAULT 0,
  updated_tick INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS story_beats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  thread_id INTEGER NOT NULL REFERENCES story_threads(id) ON DELETE CASCADE,
  tick INTEGER NOT NULL,
  actor_id TEXT,
  beat_type TEXT NOT NULL,
  content TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
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
CREATE INDEX IF NOT EXISTS idx_story_threads_scene ON story_threads(scene_id, status, updated_tick);
CREATE INDEX IF NOT EXISTS idx_story_beats_thread ON story_beats(thread_id, tick, id);
"""


DEFAULT_ATTRIBUTES = [
    ("location", "位置", "text", "state", None, None, "main_floor", "角色当前所在地点", 1),
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
        "main_floor",
        "来生酒吧主厅",
        "公共交易区。入口、服务台、公共座位和任务屏集中在此；人群以佣兵、掮客、线人和观望者为主。可发生闲谈、交易、冲突预兆和临时协商。",
        1,
        {"crowd": 55, "noise": 45, "warmth": 40, "order": 60},
        ["back_alley", "vip_booth"],
    ),
    (
        "back_alley",
        "后巷",
        "建筑后侧通道。可用于私下会面、短距离跟踪、临时藏身和风险交易；能见度低，外部干扰较少。",
        1,
        {"crowd": 10, "noise": 20, "warmth": 15, "order": 35},
        ["main_floor"],
    ),
    (
        "vip_booth",
        "包厢区",
        "隔音会谈区。适合高风险谈判、分赃、保密交易和躲避公开视线；进入成本较高，外人难以旁听。",
        0,
        {"crowd": 15, "noise": 25, "warmth": 50, "order": 75},
        ["main_floor"],
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
        "人流波动",
        "scheduled",
        "sim_tick > 0 and sim_tick % 12 == 0",
        12,
        100,
        None,  # target_need
        "每隔一段时间让酒吧环境发生轻微变化。",
        "入口识别器短暂重启，几名重装来客核对完室内人数后离开，像是在确认某个目标是否出现。",
        [
            {"target": "scene_attribute", "field": "crowd", "op": "add", "value": -3, "reason": "可疑人物离开"},
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": -2, "reason": "谈话声暂停"},
        ],
        1,
    ),
    (
        "weighted_hologram_glitch",
        "全息故障",
        "weighted",
        "sim_tick > 0 and sim_tick % 7 == 0",
        7,
        45,
        None,  # target_need
        "给当前场景加入轻微环境扰动。",
        "墙面纪念屏短暂失真，名单和头像跳成噪点，服务区有人低声抱怨设备维护又被拖延。",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": 4, "reason": "全息故障引起注意"},
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -2, "reason": "设备故障"},
        ],
        1,
    ),
    (
        "weighted_acid_rain",
        "酸雨渗漏",
        "weighted",
        "weather == 'rain'",
        10,
        25,
        None,  # target_need
        "酸雨天气时可能触发渗漏，让角色注意到环境恶化。",
        "外面的酸雨透过破损的密封条渗进来，在地上留下淡淡的腐蚀痕迹。",
        [
            {"target": "scene_attribute", "field": "warmth", "op": "add", "value": -3, "reason": "外界污染渗入"},
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -4, "reason": "环境恶化"},
        ],
        1,
    ),
    (
        "emergent_tension_spike",
        "剑拔弩张",
        "emergent",
        "tension >= 60",
        12,
        100,
        None,  # target_need
        "世界紧张度过高时，提醒角色冲突正在靠近。",
        "几个人的手不约而同移向腰间，门口站着的人挡住退路，室内提示灯在沉默里持续低鸣。",
        [
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -8, "reason": "冲突一触即发"},
            {"target": "world_state", "field": "tension", "op": "add", "value": -2, "reason": "压力被释放"},
        ],
        1,
    ),
    # 新增：针对性事件（drama 旋钮会影响这些事件的触发权重）
    (
        "weighted_gig_offer",
        "委托单",
        "weighted",
        "sim_tick > 0",
        8,
        40,
        "money_low",  # target_need
        "针对缺钱的角色，提供赚钱机会。",
        "一个穿着体面的掮客走进来，在服务台放下一张数据芯片：'简单活儿，今晚就能结账。谁感兴趣？'",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": 3, "reason": "有新委托"},
        ],
        1,
    ),
    (
        "weighted_synth_drink",
        "免费合成酒",
        "weighted",
        "sim_tick > 0",
        10,
        40,
        "mood_low",  # target_need
        "针对心情低落的角色，给予情感支持。",
        "酒保把一杯低温合成酒推过来：'这杯算我的，看你今晚不太顺。'",
        [
            {"target": "scene_attribute", "field": "warmth", "op": "add", "value": 5, "reason": "酒保的善意"},
        ],
        1,
    ),
    (
        "weighted_medkit_offer",
        "急救包递来",
        "weighted",
        "sim_tick > 0",
        10,
        40,
        "health_low",  # target_need
        "针对健康状况不佳的角色，提供照料。",
        "一个义体医生注意到某人脸色不对，丢过来一个急救喷雾：'先用着，回头找我做次检查。'",
        [
            {"target": "scene_attribute", "field": "warmth", "op": "add", "value": 6, "reason": "医疗援助"},
        ],
        1,
    ),
    (
        "weighted_quiet_booth",
        "静音隔间",
        "weighted",
        "sim_tick > 0",
        9,
        40,
        "energy_low",  # target_need
        "针对精力不足的角色，提供休息机会。",
        "包厢区有个隔音隔间空出来了，柔和照明和独立通风正好能让人喘口气。",
        [
            {"target": "scene_attribute", "field": "order", "op": "add", "value": 2, "reason": "安静空间"},
        ],
        1,
    ),
    # 新增：目标推进型事件（为角色的 goals 提供具体机会或障碍）
    (
        "emergent_corp_intel",
        "公司情报线索",
        "emergent",
        "sim_tick > 8 and sim_tick % 17 == 0",
        20,
        100,
        None,
        "为寻找情报的角色提供线索或机会。",
        "一个神秘的中间人走进来，在服务台边低声说：'有人愿意出高价买最近荒坂的内网日志。你们谁有门路？'",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": -5, "reason": "敏感话题引起警觉"},
            {"target": "world_state", "field": "tension", "op": "add", "value": 3, "reason": "公司情报交易风险高"},
        ],
        1,
    ),
    (
        "emergent_corp_tracker",
        "荒坂追踪迹象",
        "emergent",
        "sim_tick > 12 and tension >= 40",
        25,
        100,
        None,
        "给被追踪的角色施加压力。",
        "门口停下一辆荒坂标志的黑色浮空车，几个穿西装的家伙在扫描酒吧入口，不过还没进来。",
        [
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -6, "reason": "公司特工出现"},
            {"target": "world_state", "field": "tension", "op": "add", "value": 5, "reason": "荒坂介入"},
        ],
        1,
    ),
    (
        "weighted_netrunner_job",
        "高价破解委托",
        "weighted",
        "sim_tick > 6",
        15,
        35,
        "money_low",
        "为网络行者提供赚钱机会。",
        "一个戴着反光镜的富商走进来，直接说：'我需要人破解一个公司防火墙，酬劳五位数，今晚能动手吗？'",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": 4, "reason": "高价委托引起注意"},
        ],
        1,
    ),
    (
        "emergent_debt_collector",
        "债主催收",
        "emergent",
        "sim_tick > 10 and sim_tick % 19 == 0",
        22,
        100,
        None,
        "给欠债角色施加压力。",
        "酒吧门被粗暴推开，两个手臂装着重型义体的催收员走进来，扫视着人群：'欠钱的最好主动站出来。'",
        [
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -10, "reason": "暴力催收"},
            {"target": "world_state", "field": "tension", "op": "add", "value": 4, "reason": "冲突威胁"},
        ],
        1,
    ),
    (
        "weighted_cyberware_emergency",
        "紧急义体维修",
        "weighted",
        "sim_tick > 5",
        12,
        30,
        None,
        "为义体医生提供业务机会。",
        "一个佣兵捂着冒火花的义体手臂冲进来，脸色发白：'谁能帮我紧急修一下？手臂过载了！'",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": 6, "reason": "紧急情况"},
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -3, "reason": "混乱"},
        ],
        1,
    ),
    (
        "emergent_gang_confrontation",
        "帮派旧怨",
        "emergent",
        "sim_tick > 14 and tension >= 50",
        28,
        100,
        None,
        "为脱离帮派的角色带来过往冲突。",
        "几个虎爪帮的人推门进来，领头的盯着某个角色冷笑：'叛徒也敢在这喝酒？今晚该算算旧账了。'",
        [
            {"target": "scene_attribute", "field": "order", "op": "add", "value": -12, "reason": "帮派对峙"},
            {"target": "world_state", "field": "tension", "op": "add", "value": 8, "reason": "暴力冲突迫在眉睫"},
        ],
        1,
    ),
    (
        "weighted_legendary_gig",
        "传奇委托",
        "weighted",
        "sim_tick > 8",
        18,
        25,
        None,
        "为追求名声的佣兵提供高难度机会。",
        "墙上的全息屏突然亮起，显示一条匿名委托：'寻找敢单挑荒坂运输队的佣兵，报酬丰厚，成功者将被载入传奇。'",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": 8, "reason": "传奇委托震动全场"},
            {"target": "world_state", "field": "tension", "op": "add", "value": 2, "reason": "高风险任务"},
        ],
        1,
    ),
    (
        "weighted_pro_recommendation",
        "专业推荐",
        "weighted",
        "sim_tick > 7",
        14,
        35,
        None,
        "为维持声誉的佣兵提供口碑机会。",
        "一个看起来像掮客的人走到某个角色面前：'听说你做事稳，有个老客户想再合作一次，保证干净利落。'",
        [
            {"target": "scene_attribute", "field": "warmth", "op": "add", "value": 3, "reason": "专业认可"},
        ],
        1,
    ),
    (
        "weighted_partner_prospect",
        "潜力搭档",
        "weighted",
        "sim_tick > 6",
        16,
        30,
        None,
        "为寻找搭档的角色提供合作机会。",
        "服务区旁坐着一个陌生的年轻佣兵，身上的装备都很新但搭配专业，看起来是在等人组队。",
        [
            {"target": "scene_attribute", "field": "noise", "op": "add", "value": 2, "reason": "新面孔"},
        ],
        1,
    ),
]


SEED_CHARACTERS = [
    {
        "id": "char_ghost",
        "name": "Kai 'Ghost' Chen",
        "summary": "前荒坂中层员工，因内部斗争被扫地出门。现在靠卖公司情报活着，总在寻找下一个大单。",
        "attributes": {"money": 15, "goals": ["找到能翻身的情报", "避开荒坂的追踪"]},
        "traits": {"caution": 75, "self_control": 68, "aggression": 25, "honesty": 35},
    },
    {
        "id": "char_byte",
        "name": "Zara 'Byte' Martinez",
        "summary": "年轻的网络行者，技术一流但欠了改装义体的债。在来生找活儿还债，同时躲债主。",
        "attributes": {"money": 8, "goals": ["接个高价的破解单", "还清义体改装费"]},
        "traits": {"extraversion": 52, "caution": 45, "honesty": 48, "self_control": 38, "aggression": 30},
    },
    {
        "id": "char_vik",
        "name": "Viktor 'Ripper' Kozlov",
        "summary": "前漩涡帮御用义体改装师，脱帮后在夜之城各处接私活。手艺好，但名声复杂。",
        "attributes": {"money": 42, "goals": ["扩大客户群", "洗白漩涡帮的污点"]},
        "traits": {"empathy": 58, "caution": 65, "honesty": 55, "aggression": 35, "self_control": 70},
    },
    {
        "id": "char_reaper",
        "name": "Mateo 'Reaper' Silva",
        "summary": "虎爪帮前成员，因不服从命令被逐出。现在是自由雇佣兵，擅长近战和快速潜入。",
        "attributes": {"money": 18, "goals": ["证明自己比虎爪帮强", "接个能出名的活儿"]},
        "traits": {"extraversion": 48, "aggression": 78, "self_control": 35, "empathy": 32, "caution": 40},
    },
    {
        "id": "char_dak",
        "name": "Dakota 'Dak' Smith",
        "summary": "经验丰富的女性独行者，以完成任务的稳定性闻名。很少失手，但也很少冒险接超出能力的单。",
        "attributes": {"money": 55, "goals": ["维持专业声誉", "今晚找个合适的搭档"]},
        "traits": {"extraversion": 62, "honesty": 68, "empathy": 60, "caution": 72, "self_control": 75, "aggression": 45},
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
                NIGHT_CITY_WORLDVIEW,
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
              (id, title, tier, condition, cooldown, weight, target_need, guidance, narration, effects, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (id, title, tier, condition, cooldown, weight, target_need, guidance, narration, dumps(effects), enabled)
                for id, title, tier, condition, cooldown, weight, target_need, guidance, narration, effects, enabled in DEFAULT_EVENT_DEFS
            ],
        )
        for id, _title, _tier, _condition, _cooldown, _weight, _target_need, _guidance, _narration, effects, _enabled in DEFAULT_EVENT_DEFS:
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



def reset_world() -> None:
    """完全重置世界：删除旧角色、旧地点和运行日志，重新载入夜之城默认世界包。"""
    init_db()
    with connect() as conn:
        # 删除所有运行期和题材包数据，避免旧 demo 角色/地点残留。
        conn.execute("DELETE FROM scene_log")
        conn.execute("DELETE FROM story_beats")
        conn.execute("DELETE FROM story_threads")
        conn.execute("DELETE FROM event_instances")
        conn.execute("DELETE FROM scene_attributes")
        conn.execute("DELETE FROM scene_conversations")
        conn.execute("DELETE FROM scenes")
        conn.execute("DELETE FROM character_attributes")
        conn.execute("DELETE FROM character_traits")
        conn.execute("DELETE FROM relationships")
        conn.execute("DELETE FROM characters")
        conn.execute("DELETE FROM locations")
        conn.execute("DELETE FROM event_defs")
        conn.execute(
            """
            UPDATE world_state
            SET sim_tick = 0,
                day = 0,
                chapter = 0,
                period = 'morning',
                weather = 'clear',
                tension = 0,
                economy_index = 50,
                drama = 30,
                active_threads = ?,
                worldview = ?
            WHERE id = 1
            """,
            (dumps([]), NIGHT_CITY_WORLDVIEW),
        )

        conn.executemany(
            """
            INSERT INTO locations
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
            INSERT INTO event_defs
              (id, title, tier, condition, cooldown, weight, target_need, guidance, narration, effects, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (id, title, tier, condition, cooldown, weight, target_need, guidance, narration, dumps(effects), enabled)
                for id, title, tier, condition, cooldown, weight, target_need, guidance, narration, effects, enabled in DEFAULT_EVENT_DEFS
            ],
        )

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
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS story_threads (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          scene_id INTEGER REFERENCES scenes(id),
          source_event_id INTEGER REFERENCES event_instances(id),
          title TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'active',
          stage TEXT NOT NULL DEFAULT 'introduced',
          summary TEXT NOT NULL DEFAULT '',
          participants TEXT NOT NULL DEFAULT '[]',
          stakes TEXT NOT NULL DEFAULT '{}',
          priority INTEGER NOT NULL DEFAULT 50,
          beat_count INTEGER NOT NULL DEFAULT 0,
          created_tick INTEGER NOT NULL DEFAULT 0,
          updated_tick INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS story_beats (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          thread_id INTEGER NOT NULL REFERENCES story_threads(id) ON DELETE CASCADE,
          tick INTEGER NOT NULL,
          actor_id TEXT,
          beat_type TEXT NOT NULL,
          content TEXT NOT NULL,
          data TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_story_threads_scene ON story_threads(scene_id, status, updated_tick);
        CREATE INDEX IF NOT EXISTS idx_story_beats_thread ON story_beats(thread_id, tick, id);
        """
    )
    _ensure_column(conn, "scene_log", "scene_id", "ALTER TABLE scene_log ADD COLUMN scene_id INTEGER REFERENCES scenes(id)")
    _ensure_column(conn, "event_instances", "scene_id", "ALTER TABLE event_instances ADD COLUMN scene_id INTEGER REFERENCES scenes(id)")
    _ensure_column(conn, "event_defs", "effects", "ALTER TABLE event_defs ADD COLUMN effects TEXT NOT NULL DEFAULT '[]'")
    _ensure_column(conn, "world_state", "drama", "ALTER TABLE world_state ADD COLUMN drama INTEGER NOT NULL DEFAULT 30")
    _ensure_column(conn, "event_defs", "target_need", "ALTER TABLE event_defs ADD COLUMN target_need TEXT")


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
