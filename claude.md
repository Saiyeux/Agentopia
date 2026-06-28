# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**Agentopia** 是一个基于 AI 的自主角色世界模拟系统,使用 SQLite 作为唯一真相源(Single Source of Truth),结合 LLM(推荐 30B 级别模型如 Qwen3.6)实现角色成长、世界演化和自动剧情生成。

核心特性:
- **数据库驱动**: 所有状态变化必须写入 SQLite,LLM 输出的数值不作数
- **三层架构**: 角色层(Character)、裁定层(Judge/GM)、世界层(World)严格分工
- **事件驱动**: 基于 tick 的时间系统,支持 scheduled/weighted/emergent 三层事件
- **长跑稳定**: 通过记忆分级、周期性摘要、人格漂移限制等机制保证长期运行不崩坏

## 快速开始

### 开发命令

**后端开发**:
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate      # Windows
# source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
python run.py               # 启动 FastAPI 服务器 (http://localhost:8010)
```

**前端开发**:
```bash
cd frontend
npm install
npm run dev                 # 启动开发服务器 (http://localhost:5173)
npm run build               # 构建生产版本
```

### 环境变量配置

在 `backend/` 目录下可创建 `.env` 文件:
- `AGENTOPIA_LLM_PROVIDER`: LLM 提供商(默认 `lmstudio`)
- `LMSTUDIO_BASE_URL`: LM Studio API 地址(默认 `http://127.0.0.1:1234/v1`)
- `LMSTUDIO_MODEL`: 使用的模型名称(默认 `qwen-agentworld-35b-a3b`)

### API 端点

后端运行后访问:
- `http://localhost:8010/docs` - Swagger UI 文档
- `http://localhost:8010/redoc` - ReDoc 文档

## 核心架构

### 1. 四层设计文档

项目包含四个核心设计文档,它们之间紧密关联:

- **`docs/01_数据库设计.md`**: 定义所有数据表结构、字段白名单、时间模型
- **`docs/02_引擎程序设计.md`**: 定义 Python 引擎模块划分、主循环、调度逻辑
- **`docs/03_提示词与模型交互设计.md`**: 定义三种 LLM 角色的 Prompt 契约和 JSON schema
- **`docs/04_上下文无关游戏引擎设计.md`**: 定义"固定系统提示 + 当前数据库切片 + 本拍任务"的架构原则

**工作时必读**: 修改任何设计时需检查四份文档的一致性。

### 2. 数据流与真相源原则

```
代码决定条件 → LLM 生成语义 → 代码校验 → 写入 SQLite(唯一真相)
```

关键约束:
- 数值变更只能通过 `executor.py` 的白名单机制落库
- LLM 只能输出 **delta(变化量)**,不能输出绝对值
- 所有 LLM 输出必须经过 JSON schema 验证和代码边界检查

### 3. 时间模型

```
1 tick = 一个最小行动单元(一句话/一个 action)
1 day  = N_TICKS_PER_DAY 个 tick(默认 24)
1 chapter = N_DAYS_PER_CHAPTER 天(默认 7)
```

章(chapter)是成长结算的大周期,对应:
- 技能等级提升
- 人格缓慢漂移(±3 以内)
- 记忆压缩与淘汰
- 世界线推进

## 技术栈

- **数据库**: SQLite (开启 `PRAGMA foreign_keys=ON`)
- **引擎**: Python orchestrator
- **LLM 后端**: LM Studio OpenAI-compatible endpoint (单模型、串行调用)
- **推荐模型**: Qwen3.6 30B 级别,支持 JSON structured output

## 模块结构(计划实现)

```
engine/
├── clock.py        # tick 推进、昼夜切换、状态自然衰减
├── eventsys.py     # 三层事件判定 + 世界模型调用
├── scheduler.py    # 场景调度、发言者选择、冷场检测
├── agent.py        # 角色层:组装上下文 → LLM → ACTION JSON
├── judge.py        # 裁定层:预检 → LLM → VERDICT JSON
├── executor.py     # 落账:白名单校验 + 边界夹紧 + 写 SQLite
├── memory.py       # 记忆管理:写入、压缩、淘汰、检索
├── growth.py       # 成长:技能 xp、章末人格漂移、关系沉淀
├── llm.py          # LLM 客户端:调用、JSON 解析、重试、降级
├── db.py           # 数据访问层(DAL):所有读写经此,带校验
└── loop.py         # 主循环:while True 自走引擎
```

## 数据库核心表

### 角色相关
- `characters`: 写死的人格内核(personality, talents, values, taboos)
- `char_states`: 动态状态(mood, energy, money, fulfillment, current_goals)
- `skills`: 技能成长(level, xp),上限受 talents 约束
- `relationships`: 有向关系(affection, trust, respect, memory_text)
- `memory`: 分级记忆(episodic/summary/note,带 salience 重要度)

### 世界相关
- `world_state`: 世界单例(sim_tick, tension, economy_index, active_threads)
- `locations`: 地点及其动态状态
- `event_defs`: 事件模板(三层 tier + 触发条件表达式)
- `event_instances`: 触发的事件实例

### 事件流
- `scene_log`: append-only 事件流(所有 speech/action/verdict/narration)
- `scenes`: 场景生命周期管理

### 配置
- `config`: 所有可调参数(tick/day/chapter 换算、衰减率、预算等)

## 关键设计原则

### 1. 字段白名单机制

LLM 产出的 delta 只允许修改以下字段(见 `docs/01_数据库设计.md` §6):

| target | 允许字段 | 约束 |
|--------|---------|------|
| char_state | mood, energy, health, money, reputation, fulfillment.* | 0-100 |
| skill | xp | 只增,level 由代码推导 |
| rel | affection, trust, respect, memory_text | 0-100 |
| inventory | item(add/remove) | qty ≥ 0 |
| world | tension, economy_index | 0-100 |

**不在白名单的字段会被 executor 拒绝,堵死数值幻觉。**

### 2. LLM 调用优化

每拍最少调用数:
- 纯闲聊 = 1 次(角色)
- 有 action = 2 次(角色 + 裁定)
- 触发事件 = 3 次(+世界模型)

优化策略:
- 鼓励日常对话不带 action,跳过裁定层
- 接力调度:A 对 B 说话 → 直接交给 B,零额外调用
- 代码预检:硬规则不满足直接驳回,省裁定调用

### 3. 长跑防漂移机制

- **周期 re-grounding**: 每 N 拍压缩历史,重新注入 persona
- **记忆分级检索**: 只取高 salience summary + 最近 episodic
- **记忆淘汰**: 章末删除低 salience 老记忆
- **response filtering**: 实体幻觉检测、OOC 检测、复读检测

### 4. 三种 LLM 角色契约

| 角色 | 职责 | 输出 | temperature |
|------|------|------|-------------|
| 角色(Character) | 以 persona 产出台词/行动 | ACTION JSON | 0.8-0.95 |
| 裁定(Judge) | 判成败、给变化量 delta | VERDICT JSON | 0.3-0.5 |
| 世界(World) | 把事件演绎成情节 | EVENT JSON | 0.7 |

**关键**: 三者都只输出 JSON,由代码解析。越权输出会被丢弃。

## 开发建议

### 落地次序

1. **先实现核心数据库**: 建表 SQL + 白名单校验
2. **角色 + 裁定双层验证**: 手动塞场景,跑通"提议→裁定→落库"闭环
3. **response filtering**: 确保长跑不漂、不复读
4. **世界层与事件系统**: 实现三层 tier 事件判定
5. **成长系统**: 章末结算,观察角色演化

### 代码编写注意事项

- **所有数值来自 DB 实时查询**,不在内存中缓存状态
- **executor 是唯一写库入口**,其他模块只读或产出 delta
- **LLM 调用都要重试 + 降级**,失败时返回安全默认值
- **JSON 解析要容错**: 允许 ```json 包裹、尾随文本
- **condition 表达式用安全求值器**: 不直接 eval,防注入

### 性能优化

- **Prompt 前缀稳定化**: 写死内容放最前,变化内容放后,利用 KV cache
- **关闭或限制 reasoning**: Qwen 系列过度 thinking 会爆 token
- **收紧 max_tokens**: 角色 ~256,裁定/世界 ~512
- **记忆分级**: 绝不全量加载,只取高 salience + 最近 N 条

## 配置参数(见 config 表)

关键可调参数:
- `N_TICKS_PER_DAY`: 每天多少 tick(默认 24)
- `N_DAYS_PER_CHAPTER`: 每章多少天(默认 7)
- `FULFILLMENT_DECAY`: 享乐适应衰减率
- `SCENE_DEFAULT_BUDGET`: 场景默认回合预算(默认 12)
- `STALE_THRESHOLD`: 冷场判定阈值(连续 N 拍无信息)
- `SUMMARY_EVERY_TICKS`: 多少拍做一次摘要

## 常见陷阱

1. **不要让 LLM 直接写数值**: 只接受 delta,绝对值由代码计算
2. **不要跳过白名单校验**: executor 必须检查所有字段
3. **不要全量加载记忆**: 会炸上下文,用 salience 分级检索
4. **不要让人格无限漂移**: 章末漂移限制在 ±3 以内
5. **不要让技能超过 talent 上限**: growth.py 必须检查
6. **不要让事件凭空造人**: 世界模型只能用注册表内的角色

## 模拟与回放解耦

系统设计为:
```
[后台进程] 全速模拟 ──append──> scene_log(SQLite)
                                  ↓
[前端/回放器] ←──poll/tail── 以可读速度回放
```

模拟端和观察端异步运行,30B 模型慢也不影响观看体验。

## 初始化流程

```python
1. init_db()           # 建表(数据库文档顺序)
2. seed_world()        # 载入 worldview、locations、event_defs
3. seed_characters()   # 灌入 8-12 张角色卡
4. config 写入运行参数
5. run()               # while True 主循环
```

角色与世界的初始生成建议用云端强模型离线完成,运行期只用本地 30B。

## 当前实现状态

### 已实现的核心模块

**后端 (backend/app/)**:
- `db.py`: 数据库访问层,包含动态属性/特质系统
- `llm.py`: LLM 客户端(支持 LM Studio),JSON schema 约束
- `engine.py`: 角色行动引擎核心逻辑
- `judge.py`: 裁定层实现
- `executor.py`: Delta 应用和白名单校验
- `filtering.py`: 响应过滤(实体检查、文本清洗)
- `scheduler.py`: 场景调度和对话状态管理
- `eventsys.py`: 事件系统基础
- `prompts.py`: Prompt 模板构建
- `main.py`: FastAPI REST API

**前端 (frontend/src/)**:
- React + TypeScript + Vite 技术栈
- 观察窗 UI 组件

### 数据库架构特点

当前实现采用**动态属性系统**,而非完全固定表结构:
- `attribute_defs`: 定义可用的属性类型(mood, energy, money 等)
- `char_attributes`: 存储角色的实际属性值
- `trait_defs`: 定义可用的人格特质
- `char_traits`: 存储角色的人格特质分数

这种设计提供更高灵活性,可通过 API 动态添加新属性/特质,无需修改表结构。

### 待完善功能

以下模块在设计文档中规划但尚未完全实现:
- `memory.py`: 记忆管理(分级、压缩、淘汰)
- `growth.py`: 成长系统(技能 xp、章末人格漂移)
- `clock.py`: 完整的 tick/day/chapter 时间推进
- 完整的三层事件系统(scheduled/weighted/emergent)
- 章末结算机制
- 世界层 LLM 角色(World model)

### 开发时注意

1. **数据库初始化**: 首次运行时数据库会自动创建在 `backend/data/agentopia.sqlite3`,但需要通过 API 或手动添加 attribute_defs/trait_defs/characters
2. **LLM 连接**: 确保 LM Studio 或兼容的 OpenAI API 服务正在运行
3. **动态 schema**: 属性和特质可通过 API 动态管理,参考 `http://localhost:8010/docs` 了解可用接口
4. **数据目录**: `backend/data/` 已在 .gitignore 中,不会提交到仓库
