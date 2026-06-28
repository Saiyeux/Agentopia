"""
watch.py — Agentopia 观察窗 / trace dumper

独立脚本，不修改引擎任何逻辑。跑 N 拍，把每一拍发生的事打印成人能读的形式，
让你第一次"看见"世界每拍在干什么：事件有没有触发、角色说了什么、裁决到底改了什么。

用法（在 backend/ 目录下）：
    python watch.py 20            # 跑 20 拍
    python watch.py 20 --reset    # 先重置角色状态再跑（每次想从干净状态看时用）

依赖你本地的 LM Studio（和引擎一样）。若模型连不上，引擎会走 fallback，
脚本仍能跑完——这时你会看到大量"裁决：本拍无变化"，那本身就是有用的信号。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 让脚本能 import 到 app.*（无论从哪运行）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.db import connect, loads, reset_character_state, seed_demo  # noqa: E402
from app.engine import step_once  # noqa: E402
from app.scheduler import list_open_scenes  # noqa: E402


GREY = "\033[90m"
BOLD = "\033[1m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
DIM = "\033[2m"
RESET = "\033[0m"


def _name_map(conn) -> dict[str, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM characters")}


def _char_state(conn, char_id: str) -> dict:
    rows = conn.execute(
        "SELECT attr_id, value FROM character_attributes WHERE char_id = ?",
        (char_id,),
    ).fetchall()
    return {r["attr_id"]: r["value"] for r in rows}


def _world(conn) -> dict:
    row = conn.execute(
        "SELECT sim_tick, day, period, weather, tension, economy_index, drama FROM world_state WHERE id = 1"
    ).fetchone()
    return dict(row) if row else {}


def _threads(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT title, status, stage, stalled_turns, beat_count
        FROM story_threads
        WHERE status IN ('active', 'parked')
        ORDER BY updated_tick DESC, id DESC
        LIMIT 5
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _open_scenes(conn) -> list[dict]:
    scenes = list_open_scenes(conn)
    names = {
        row["id"]: row["name"]
        for row in conn.execute("SELECT id, name FROM locations").fetchall()
    }
    return [
        {
            **scene,
            "location_name": names.get(scene["location_id"], scene["location_id"]),
        }
        for scene in scenes
    ]


def print_header(conn) -> None:
    names = _name_map(conn)
    w = _world(conn)
    print(f"\n{BOLD}=== 世界初始状态 ==={RESET}")
    print(f"  tick={w.get('sim_tick')} 天={w.get('day')} 时段={w.get('period')} "
          f"天气={w.get('weather')} 紧张度={w.get('tension')} 经济={w.get('economy_index')} "
          f"{BOLD}drama={w.get('drama', 30)}{RESET}")
    print(f"{BOLD}=== 角色（钱/心情/精力/健康 + 目标）==={RESET}")
    for cid, name in names.items():
        s = _char_state(conn, cid)
        goals = loads(s.get("goals"), [])
        print(f"  {name:6} 钱{s.get('money'):>3} 心情{s.get('mood'):>3} "
              f"精力{s.get('energy'):>3} 健康{s.get('health'):>3}  "
              f"{DIM}目标:{goals}{RESET}")
    print()


def print_tick(conn, result: dict, names: dict[str, str]) -> None:
    tick = result["tick"]
    events = result.get("events", [])
    actor_id = result["actor_id"]
    actor_name = result.get("actor_name", actor_id)
    action = result.get("action", {})
    verdict = result.get("verdict", {})

    if events:
        ev = events[-1]
        ev_line = f"{YELLOW}★ 事件[{ev.get('tier')}] {ev.get('title')}：{ev.get('narration')}{RESET}"
    else:
        ev_line = f"{GREY}（无事件）{RESET}"

    s = _char_state(conn, actor_id)
    goals = loads(s.get("goals"), [])

    print(f"{BOLD}[t={tick:>3}]{RESET} {ev_line}")
    print(f"   {CYAN}{actor_name}{RESET} "
          f"{DIM}(钱{s.get('money')} 心情{s.get('mood')} 精力{s.get('energy')} 健康{s.get('health')} "
          f"目标{goals}){RESET}")

    speech = action.get("speech", "")
    inner = action.get("inner", "")
    to = action.get("to")
    act = action.get("action")
    to_str = f" → {names.get(to, to)}" if to else ""
    print(f"   说{to_str}：「{speech}」" + (f"  {DIM}（心声:{inner}）{RESET}" if inner else ""))
    if act:
        print(f"   {DIM}意图: {act.get('type')} target={act.get('target')} {act.get('detail','')}{RESET}")

    applied = verdict.get("applied", [])
    changed = [a for a in applied
               if a.get("status") == "applied" and a.get("old") != a.get("new")]
    ok = "成功" if verdict.get("success") else "未成"
    if changed:
        parts = []
        for a in changed:
            owner = names.get(a.get("ref"), a.get("ref"))
            parts.append(f"{owner}.{a.get('field')} {a.get('old')}→{a.get('new')}")
        print(f"   {GREEN}裁决({ok})：{'，'.join(parts)}{RESET}")
    else:
        print(f"   {GREY}裁决({ok})：本拍无数值变化{RESET}")
    threads = _threads(conn)
    if threads:
        thread_text = "；".join(
            f"{item['title']}[{item['status']}/{item['stage']}/stalled={item.get('stalled_turns', 0)}/beats={item.get('beat_count', 0)}]"
            for item in threads
        )
        print(f"   {DIM}线程: {thread_text}{RESET}")
    scenes = _open_scenes(conn)
    if scenes:
        scene_text = "；".join(
            f"{item['location_name'] or item['location_id']}#s{item['id']}({len(item['participants'])}人/t{item['turn_count']})"
            for item in scenes
        )
        print(f"   {DIM}场景: {scene_text}{RESET}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentopia 观察窗")
    parser.add_argument("ticks", type=int, nargs="?", default=10, help="要跑的拍数")
    parser.add_argument("--reset", action="store_true", help="先重置角色状态再跑")
    args = parser.parse_args()

    seed_demo()
    if args.reset:
        reset_character_state()
        print(f"{DIM}（已重置角色状态）{RESET}")

    conn = connect()
    names = _name_map(conn)
    print_header(conn)

    empty_verdicts = 0
    event_ticks = 0
    for _ in range(args.ticks):
        result = step_once(conn)
        conn.commit()
        print_tick(conn, result, names)
        if result.get("events"):
            event_ticks += 1
        applied = result.get("verdict", {}).get("applied", [])
        if not any(a.get("status") == "applied" and a.get("old") != a.get("new") for a in applied):
            empty_verdicts += 1

    n = args.ticks
    print(f"{BOLD}=== 统计 ==={RESET}")
    print(f"  有事件的拍: {event_ticks}/{n} ({event_ticks*100//max(n,1)}%)")
    print(f"  无数值变化的拍: {empty_verdicts}/{n} ({empty_verdicts*100//max(n,1)}%)")
    print(f"  {DIM}→ 这两个数字越接近'事件少、空裁决多'，世界就越闷。"
          f"做导演层的目标，就是把它们往'事件多且指向角色目标、裁决常推进目标'的方向拉。{RESET}\n")

    conn.close()


if __name__ == "__main__":
    main()
