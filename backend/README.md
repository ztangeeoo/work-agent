# 后端（backend/）

Python 智能体服务（FastAPI + AgentScope 2.0）与 Node 网关都在这一层。前端仍是仓库根的 `web/`。

## 目录结构

```
backend/
├── run.py                    ★ 启动入口：python backend/run.py（默认 :8790）
├── server.js                 Node 网关 :8787：静态托管 + CopilotKit runtime + 反向代理到 :8790
├── agent_adapter.js          CopilotKit ServiceAdapter：把 runtime 的请求转成 OpenAI 兼容请求
├── requirements.txt
├── adapters/                 可训练的外挂指令（Hermit 格式），按智能体分目录
│   ├── interview/            ADAPTER.md（面试官 + 策划官）
│   └── review/               ADAPTER.md（侦察官 + 审核官 + 裁决官 + 审核追问）
├── skills/                   技能（Agent Skills 格式）：一个目录一个 SKILL.md
│   ├── question-design/      场景化出题方法（面试出题官）
│   ├── code-review-security/ 安全审查清单（代码审核官）
│   └── cn-tech-writing/      中文技术表达规范（全局）
├── scripts/                  独立脚本，不被服务进程加载
│   ├── agent_service.py      官方 Agent Service（agentscope.app.create_app，:8799）
│   └── sandbox_probe.py      沙箱自检（Docker / 本地）
└── app/                      ★ 应用主包
    ├── main.py               创建 FastAPI、生命周期（建库 → 起轮询 → 收尾）、注册路由
    ├── config.py             路径与开关（端口、策划官开关、数据目录、adapter 目录）
    ├── state.py              进程内共享状态：前端模型配置、轮询器单例
    ├── core/                 横切关注点
    │   ├── version.py        AgentScope 2.0 版本闸门（装错版本立刻给出修复命令）
    │   ├── log.py            统一日志
    │   ├── errors.py         模型调用异常 → 人话
    │   └── stages.py         流水线阶段元信息（前端面板的 label / icon）
    ├── schemas/              数据契约
    │   ├── score_card.py     评分卡（Pydantic，交给 structured_schema）
    │   └── review_report.py  审核报告 JSON 形状、严重度/结论标签、归一化
    ├── llm/
    │   └── factory.py        任意 OpenAI 兼容接口 → OpenAIChatModel（带缓存）
    ├── prompts/              提示词仓库（一个智能体一个函数）
    │   ├── profile.py        候选人画像 / PR 信息 → 文本片段
    │   ├── interview.py      面试官、策划官、评分官
    │   └── review.py         侦察官、审核官、裁决官、审核答疑
    ├── tools/
    │   ├── question_bank.py  内置高频题库（11 个主方向 × 细分）
    │   └── registry.py       FunctionTool / Toolkit 装配（显式 ALLOW）
    ├── store/                持久层（标准库 sqlite3，零第三方依赖）
    │   ├── db.py             连接、建表（9 张表 + FTS5）、内置专家/智能体种子、容量统计
    │   ├── catalog.py        专家（expert）与智能体（agent）：会话 → 智能体 → 专家
    │   ├── convos.py         会话与消息、中文全文检索
    │   ├── reviews.py        仓库 / PR / 审核报告 CRUD
    │   └── __init__.py      门面：`from .. import store` 后按 `store.xxx()` 调用
    ├── services/             外部适配与应用服务
    │   ├── harness.py        adapter 加载（hermit 可缺省 / mtime 热更新 / 可旁路）
    │   ├── skills.py         技能扫描与归属解析（全局 / 专家 / 角色，mtime 热更新）
    │   ├── review_client.py  代码托管客户端：解析地址、拉 PR / diff、回帖、走代理
    │   ├── poller.py         PR 轮询调度：同仓库串行、单轮限流、到期触发
    │   └── events.py         审核事件中心（订阅 / 补发最近一批）
    ├── agents/               智能体编排
    │   ├── runtime.py        make_agent / drive_agent（事件流）/ StatusFlusher
    │   ├── router.py         意图路由（出题 vs 评分）
    │   ├── interview.py      策划官 → 面试官 →（评分官）流水线，SSE 输出
    │   └── review.py         侦察官 → 审核官 → 裁决官流水线
    ├── utils/                无状态工具
    │   ├── sse.py            OpenAI 兼容 chunk + agent_stage 事件
    │   ├── messages.py       OpenAI messages → AgentScope Msg
    │   ├── parsing.py        容错解析（捞 JSON、diff 统计）
    │   └── render.py         评分卡 / 审核报告 → Markdown
    └── api/                  HTTP 层
        ├── deps.py           用户标识、错误响应、SQLite 降级判断
        └── routes/
            ├── system.py     /health、/api/model-config、/api/db/stats
            ├── catalog.py    /api/experts、/api/agents（专家与智能体清单）
            ├── chat.py       /v1/chat/completions、/v1/review
            ├── convos.py     /api/convos*、/api/search
            ├── repos.py      /api/repos*
            ├── reviews.py    /api/reviews*、/api/review/events
            └── skills.py     /api/skills（技能目录 + 专家绑定关系，只读）
```

## 数据模型：会话 → 智能体 → 专家

```
convo.agent_id ──▶ agent(expert_id) ──▶ expert(mode: interview/review)
```

- `expert`：对外的能力包（面试出题官 / 代码审核官），带 icon / role / desc / tags / cta。
- `agent`：专家下挂的流水线角色（策划官 / 面试官 / 评分官、侦察官 / 审核官 / 裁决官 / 审核答疑），
  每个专家有且只有一个 `is_primary=1` 的主智能体，新会话默认绑定它。
- `convo.agent_id`：会话绑定的智能体，`NULL` = 还没关联专家；`convo.agent_mode` 是
  由 `agent → expert` 派生的冗余字段，只给老接口和路由用，写入时一律以 `agentId` 为准。
- 内置数据由 `store/db.py` 的 `SEED_EXPERTS` / `SEED_AGENTS` 在首次建库时 `INSERT OR IGNORE`
  灌入；之后以库为准，改文案 / 加专家直接改库即可，重启不覆盖。
- 老库自动迁移：`convo` 补 `agent_id` 列，并按 `agent_mode` 回填成对应专家的主智能体。

## 分层与依赖方向

```
api  →  agents  →  { llm, prompts, tools, schemas }
             ↘  services  →  store
                    ↘  core / config / utils
```

- **只允许上层依赖下层**。`services/poller.py` 不直接 import `agents/review.py`，
  审核函数由 `main.create_app()` 注入——需要时用这个办法打断环，别做模块级互相 import。
- **路由层只做参数校验与响应**，任何编排逻辑都下沉到 `agents/` 或 `services/`。
- **提示词只拼字符串**，不读文件：adapter 拼接统一在 `services/harness.py`。

## 一次请求怎么走

面试出题（`POST /v1/chat/completions`，SSE）：

```
routes/chat.py  预取第一块（404/401 直接 JSON 502，不混进正文）
  → agents/interview.stream_with_retry   /v1 缺失时补全重试一次
    → agents/router.route_intent        评分意图 → 评分官；否则出题
    → agents/runtime.make_agent         每次请求新建空状态 Agent
    → prompts/*.compose                 system prompt + adapters/*/ADAPTER.md
    → agents/runtime.drive_agent        事件流 → text / tool / structured
    → utils/sse.sse_chunk|sse_stage     文本增量 + 阶段事件
```

代码审核（`POST /v1/review`，一次性 JSON）：

```
routes/chat.py → agents/review.run_review
  → 侦察官（概览）→ 审核官（JSON 问题清单）→ 裁决官（风险/结论）
  → utils/render.render_review_md      同时落库（services/poller.review_one）
```

数据接口（`/api/*`）：`routes/*` → `store.*`（SQLite）；仓库相关再经 `services/review_client` 出网。

## 改哪里（常见需求）

| 需求 | 改这里 |
|---|---|
| 加一个智能体 / 调整流水线顺序 | `agents/*.py` + `core/stages.py`（前端面板标签）+ `prompts/`（提示词） |
| 调提示词 | `prompts/*.py`；若属于「可训练的外挂指令」，写 `adapters/<name>/ADAPTER.md` |
| 加一个 Agent 工具 | `tools/` 里写函数 → `tools/registry.build_toolkit()` 注册 → `runtime.make_agent(with_tools=True)` |
| 加一个技能 | 在 `skills/<name>/SKILL.md` 写正文 + 声明归属（`scope`），放到目录即生效，不用改代码 |
| 加一个 HTTP 接口 | `api/routes/<域>.py` 里加 `@router.<method>`；`main.create_app()` 已统一挂载，无需改动 |
| 换数据库 / 加表 | `store/db.py`（SCHEMA）+ 对应仓库模块；对外 JSON 契约保持 camelCase |
| 换代码托管平台 | `services/review_client.py`（解析 / 拉取 / 回帖三处） |
| 加配置项 | `config.py`，全部走环境变量并给默认值 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_PORT` | `8790` | 智能体服务端口 |
| `USE_PLANNER` | `1` | 首次出题前是否跑策划官；`0` 省一次模型调用 |
| `DB_PATH` | `<仓库根>/data/interview.db` | SQLite 文件位置 |
| `DATA_DIR` | `<仓库根>/data` | 数据目录（沙箱工作区等） |
| `HARNESS_ADAPTER` | `1` | 是否启用 adapter 指令层；`0` 完全旁路 |
| `HARNESS_ADAPTER_DIR` | `backend/adapters` | adapter 根目录 |
| `HARNESS_ADAPTER_MAX` | `6000` | 单份 adapter 的字符上限 |
| `SKILLS_ENABLED` | `1` | 是否装载技能；`0` 时所有 Agent 都不装（恢复接入前行为） |
| `SKILLS_DIR` | `backend/skills` | 技能根目录，一个子目录一个技能 |
| `AGENT_SERVICE_PORT` / `AGENT_SERVICE_DB` / `AGENT_WORKSPACE_DIR` / `SANDBOX` | 见 `scripts/agent_service.py` | 官方 Agent Service 的配置 |
| `PORT` / `AGENTSCOPE_URL` / `AGENTSCOPE` | `8787` / `http://127.0.0.1:8790` / 开 | Node 网关的端口、上游地址与总开关 |

## 技能（Skill）与归属

技能是 AgentScope 2.0 内建能力，格式即 Anthropic Agent Skills：一个目录一个
`SKILL.md`，YAML frontmatter 写 `name` + `description`，正文是操作手册。
Toolkit 初始化时把 name/description 拼进 system prompt，运行中由内建 `Skill`
查看器按名字读取全文——**不占常驻上下文**，也不需要改 agent loop。

`services/skills.py` 只多做一件事：决定**哪个角色装哪些技能**。frontmatter 里：

```yaml
---
name: question-design               # 必填，kebab-case（AgentScope 按它查找技能）
title: 场景化出题                    # 可选，界面展示名；不写就显示 name
description: 做什么 + 什么时候用       # 必填，模型只靠这句话选技能
scope: expert                      # global | expert（不写则不生效）
experts: [interviewer]             # scope=expert 时必填：interviewer / reviewer
agents: [planner, interviewer]     # 可选：再收窄到具体角色
---
```

| 声明 | 装载给谁 |
|---|---|
| `scope: global` | 所有角色（库里现有的全部 stage） |
| `scope: expert` + `experts` | 该专家下的全部角色；再用 `agents` 收窄 |
| 不写 `scope` / 写错 / 未知专家 | **不装载**，`/api/skills` 里标为未绑定并给出原因 |

为什么缺失归属就不生效：从市面上拿来的技能是外部内容，默认让它常驻每次调用的
system prompt 等于静默注入指令。宁可显式声明，也不要隐式生效。

### 「专家 → 角色」从哪来

**唯一真相在数据库**：`store` 的 `expert` / `agent` 两张表（`agent.stage` 就是流水线角色）。
`services/skills.py` 的 `_experts()` 先读 `store.list_experts()`；库没连上（未初始化 /
单测环境）才回退 `core/stages.py` 的 `EXPERT_META` / `EXPERT_STAGES` 常量。

这样做的原因：这份映射以前在代码里手抄一份，往库里加一个智能体（比如面试专家加个
「复盘官」），`/api/experts` 会立刻显示它，技能却不会自动挂上去——不报错，只是悄悄不生效。
现在改库即生效，代码里的常量退化成兜底。

缓存签名是「SKILL.md 的 mtime + `catalog_version()`（两张表的条数与最后修改时间）」，
所以改库也会触发重算，不用重启、不用手动清缓存。`/api/db/stats` 的 `skills.source`
会显示这次归属用的是 `db` 还是 `fallback`，排障时先看这一项。

注意 `store.connected()` 与 `store.available()` 的区别：前者只判断连接是否已建立、
**不会建库**，读一次技能不该顺手拉起一个数据库文件；后者会 `init()`。

调用链：

```
make_agent(stage=...)            # 各流水线角色传自己的 stage
  → build_toolkit(stage=...)     # tools 与技能互不影响
    → services.skills.for_stage  # 目录扫描 + 归属过滤（文件与库双签名热更新）
      → Toolkit(skills_or_loaders=[目录…])
        → Agent 自动把技能清单拼进 system prompt
```

`GET /api/skills` 返回 `skills`（全部，含未生效的）与 `assignments`（按全局 /
各专家分好组），前端「专家」页在卡片上展示每位专家绑了哪些技能。
`GET /health` 与 `/api/db/stats` 里的 `skills` 字段是自检信息（数量、按角色分布）。

技能的 scripts/ 与 references/ 属于第三级披露，需要文件读写与命令执行工具；
本项目 Agent 没有这类工具，所以只用到前两级（元数据 + SKILL.md 正文）。


## 启动与自检

```bash
python backend/run.py                 # 智能体服务（等价 python -m backend.app.main）
node backend/server.js                # 网关（需先 npm install）

curl http://127.0.0.1:8790/health     # 服务与库状态（含 skills 自检）
curl http://127.0.0.1:8790/api/skills # 技能目录与专家绑定关系
python tests/test_agentscope_api.py   # 运行时基线
python tests/test_store.py            # 持久层
python tests/test_skills.py           # 技能解析与归属
```

仓库根的双击启动仍是 `start.bat`（会构建前端并拉起上面两个服务）。
