# Agentopia 回复处理链路

## 角色层
1. Python 从 SQLite 读取当前 actor、属性、人格、关系、当前场景、最近事件、活动剧情线。
2. 后端读取 `character_system.md` 作为 system prompt。
3. 后端把数据库切片组装成 user JSON，其中 `task.instruction` 来自 `character_task_instruction.md`。
4. LLM 必须返回 ACTION JSON：`speech`, `inner`, `action`, `to`, `topic`。
5. `filter_action` 会检查 JSON 字段、目标角色是否在场、文本是否可显示。
6. 合格 speech 写入 `scene_log(type='speech')`。
7. 如果有活动剧情线，`record_thread_turn` 会根据 speech/action 推进剧情阶段；阶段变化会清空 `stalled_turns`，无进展会累加。
8. 线程停滞达到阈值后会变成 `parked`，不删除，但不再被每拍强推。
9. `to` 表示这句话直接对谁说；被点名角色下一拍会被 scheduler 优先选中回应。`heard_by` 表示同场景里谁听见了这句话。

## 裁定层
1. 后端把本拍 action、当前 scene、在场角色、最近事件交给裁定层。
2. 后端读取 `judge_system.md` 作为 system prompt。
3. LLM 必须返回 VERDICT JSON：`success`, `narration`, `deltas`。
4. 裁定层只能提出 delta，不能直接写数据库，也不能设置绝对值。

## 落库层
1. `executor.apply_deltas` 是唯一数值落库入口。
2. 代码检查 target、field、op 是否在白名单内。
3. 数值会被 min/max 边界夹紧。
4. 不合法 delta 被拒绝，合法 delta 写入 SQLite。
5. 裁定结果写入 `scene_log(type='verdict')`，前端把它挂在对应 speech 后面显示。

## 对话接力
1. `record_scene_turn` 会保存上一句的 speaker、recipient、topic、heard_by。
2. 下一拍 `pick_next_actor` 优先选择上一句的 recipient；没有明确 recipient 时才按场景轮转。
3. 角色 prompt 中会包含 `dialogue_context.directed_to_me / overheard / not_heard`，帮助模型区分“该回应我”还是“我只是旁听”。

## 开场层
1. 后端读取当前 world、scene location、地点状态、最近 narration。
2. 后端读取 `opening_system.md`，要求 LLM 返回 `{"opening":"..."}`。
3. `normalize_opening_content` 会拒绝英文分析、提示词回显、过短内容和照抄地点事实的内容。
4. 合格 opening 写入 `scene_log(type='narration')`。

## 世界事件
1. 本地事件系统只负责判断哪个事件类型被触发：条件、权重、冷却、target_need。
2. `event_defs.narration` 不作为展示内容使用；当前库中应保持为空。
3. 事件触发后，后端读取 `world_system.md`，把 event_def 的结构意图、当前 scene、world 和最近日志交给世界层 LLM。
4. 世界层 LLM 必须返回 `{"narration":"..."}`。
5. narration 为空、像英文分析、复述任务、或照抄 event_def/guidance，都会被拒绝并报错；不会写入 `scene_log`。
6. 合格 narration 写入 `event_instances` 和 `scene_log(type='event')`。
