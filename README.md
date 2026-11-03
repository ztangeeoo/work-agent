# IT 面试出题官（React + CopilotKit + AgentScope + FastAPI）

一个本地运行的研发助手智能体，内含两个可切换的智能体：

- 🎯 **面试出题官**：配置自己的模型（任意 OpenAI 兼容接口），选择技术方向 / 细分方向 / 级别 / 经验 / 行业背景，上传简历（txt / md / pdf / docx / 图片），由 **AgentScope 多智能体** 生成针对性面试题，支持对话式追问与结构化评分。
- 🛡️ **代码审核官**：配置代码仓库地址（GitHub / GitLab，支持 Token），按间隔**自动轮询 PR**、拉取 diff 交给三智能体（侦察官 → 审核官 → 裁决官）自动审核、给出结构化意见与风险结论，并可在聊天里追问这份审核；可选**一键回帖**到 PR。

前端为 **React SPA（Vite）+ CopilotKit**，后端为 **Node（托管 + CopilotKit runtime + 反向代理）+ Python FastAPI（智能体 + 数据服务）**，前后端分离。

## 架构

```
浏览器（React SPA · web/，Vite 构建产物在 web/dist）
  │  CopilotKit（useCopilotChatHeadless_c，runtimeUrl=/api/copilotkit）
  ▼
server.js（Node :8787）——只做「静态托管 + CopilotKit runtime + 反向代理」
  ├── GET  /                    托管 React 构建产物（web/dist，未构建时回退旧 index.html）
  ├── GET  /assets/...          Vite 静态资源
  ├── POST /api/models          直接代理模型接口，拉取可用模型列表
  ├── POST /api/chat            转发给智能体服务（测试连接 / 图片简历视觉识别）
  ├── POST /api/model-config    前端模型配置（内存留一份供适配器用，同时同步给 Python）
  ├── GET  /api/stages          智能体执行阶段 SSE（与适配器同进程）
  ├── POST /api/copilotkit      CopilotKit 自托管 runtime
  │     └── agent_adapter.js（AgentScopeAdapter）
  │           ├── 读取模型配置（base / key / model / profile / agentMode）
  │           ├── 转成 OpenAI 兼容请求 POST :8790/v1/chat/completions
  │           └── SSE 增量流转发回 CopilotKit 事件流
  └── /api/convos · /api/search · /api/repos · /api/reviews · /api/review/events
        └── 反向代理 → :8790（持久化、仓库配置、PR 轮询、审核报告与事件流都在 Python 侧）
  ▼
agent_server.py（Python :8790，FastAPI + AgentScope）
  ├── POST /v1/chat/completions   OpenAI 兼容，SSE 流式（面试问答 / 审核追问）
  ├── POST /v1/review             代码审核流水线（侦察官 → 审核官 → 裁决官）
  ├── /api/*                      数据服务：会话 / 检索 / 仓库 / PR / 审核报告 / 事件流
  │     ├── store.py              持久层（标准库 sqlite3：七张表 + FTS5 中文检索）
  │     ├── reviewer.py           代码托管适配（GitHub / GitLab 拉 PR、diff、回帖，自动走代理）
  │     └── review_poller.py      轮询调度（同仓库串行、单轮限流、到期触发、事件广播）
  ▼
┌─────────────── AgentScope 多 Agent 编排 ───────────────┐
│  🧭 策划官 Planner     首次出题前产出考察大纲            │
│  🎯 面试官 Interviewer + 高频题库工具：出题/追问         │
│  🧑‍⚖️ 评分官 Evaluator  结构化输出：回答评分卡            │
│  🔍 侦察官 Scout       读 diff、归纳改动与风险区          │
│  🛡️ 审核官 Reviewer    结构化问题清单（严重度/文件/建议） │
│  ⚖️ 裁决官 Judge       风险等级 + 合入结论                │
└───────────────────────────────────────────────────────┘
  │
  ▼
你配置的模型 API（DeepSeek / tokens.store / Ollama / ...）
```

为什么用 CopilotKit 而不引整套 React UI 框架：CopilotKit 只负责「消息状态 / 流式 / runtime 协议」，
消息列表仍自渲染，保留「一题一卡」的卡片化出题显示。

## 运行时基线：AgentScope 2.0

| 项 | 要求 | 说明 |
|---|---|---|
| Python | **3.11+** | AgentScope 2.0 的硬性要求（1.x 时代 3.10 也能跑，现在不行） |
| AgentScope | **>= 2.0.11, < 3.0** | 只在 `requirements.txt` 里声明，不要额外装 `agentscope-runtime` |
| 模型接口 | 任意 OpenAI 兼容 | 通过 `OpenAIChatModel` + `OpenAICredential(base_url=...)` 接入 |

项目用到的 2.0 API（升级时重点看这几处）：

- `Agent(name, system_prompt, model, toolkit)` + `reply_stream(inputs, structured_schema=..., yield_final_msg=...)`
- 事件流：`TextBlockDeltaEvent.delta` / `ToolCallStartEvent.tool_call_name` / `ReplyEndEvent.finished_reason|error`
- 工具：`FunctionTool(func, permission=PermissionDecision(behavior=ALLOW, ...))` + `Toolkit(tools=[...])`（显式放行，服务端不会卡在人工确认）
- 结构化输出：`structured_schema=ScoreCard`，最终消息的 `structured_output` 取 dict，失败回退到文本 JSON 解析

> **关于 `agentscope-runtime`**：那个包（最后版本 1.1.6.post2）已随 AgentScope 2.0 发布而归档，
> 沙箱、AaaS 服务化、全栈可观测性都已并入 `agentscope` 主包。本项目**不依赖它**，也不用
> `ReActAgent` 那套 1.x 写法，升级时不要再把它装回来。

### 上下文语义（易踩坑）

2.0 的 `reply_stream(inputs)` 会把 `inputs` **追加**进 `state.context`。所以历史对话只能通过
`inputs` 传一次：如果既 `AgentState(context=history)` 又传 `inputs=history`，模型会收到双份历史、
最后一条用户消息出现三次——token 翻倍且干扰判断。项目里 `make_agent()` 因此只造空状态 Agent，
历史统一由 `drive_agent()` 的 `inputs` 带入；`tests/test_agentscope_api.py` 里有一条断言锁死这一点。

## Sandbox 与 Agent Service（2.0 内建）

两个能力都在 `agentscope` 主包里，**不需要** `agentscope-runtime`。安装时只要带上 extras：

```bash
.venv\Scripts\python.exe -m pip install -U -r requirements.txt   # 已含 [service,storage-sql] + aiosqlite
```

### Sandbox（工作区 / 工具执行环境）

`agentscope.workspace` 是 Agent 执行 Shell、读写文件、挂 MCP 的落点，三种常见后端：

| 后端 | 隔离性 | 适用 |
|---|---|---|
| `LocalWorkspace` | 无（直接在宿主机执行） | 开发机、单机自用 |
| `DockerWorkspace` | 容器级（workdir 挂到 `/workspace`） | 多用户 / 跑不可信代码，需 Docker 引擎 |
| `E2B / Daytona / K8s / OpenSandbox` | 远端沙箱 / 集群 | 生产弹性伸缩 |

自检（会真的跑命令、读写文件）：

```bash
.venv\Scripts\python.exe sandbox_probe.py                          # 默认容器后端（需 Docker 引擎）
set SANDBOX=local && .venv\Scripts\python.exe sandbox_probe.py     # 宿主机直跑，快
```

> 本机实测（Docker Desktop 4.50 / 引擎 28.5.1，Linux 容器）：容器后端 5 项全过，首次初始化 150s
> （要构建 `agentscope-workspace:<hash>` 镜像，基底 `python:3.11-slim`），之后镜像命中缓存约 5s。
> 容器里没有宿主机的 python.exe，`exec_shell` 要用容器内的 `python3`；本地后端则用 `sys.executable`。

### Agent Service（官方服务化入口）

`agent_service.py` 用 `agentscope.app.create_app()` 起官方服务，替掉归档的 runtime：

```bash
.venv\Scripts\python.exe agent_service.py                # 默认容器沙箱（要求 Docker 引擎在跑）
set SANDBOX=local && .venv\Scripts\python.exe agent_service.py   # 只本机调试，放弃隔离
# → http://localhost:8799/docs   共 77 个接口（会话 / 运行 / 模型 / 凭据 / RAG / 工作区 / 渠道 / 计划任务）
```

启动时会先探测 Docker 引擎：不可用直接退出并提示，**不会静默降级成本地执行**（否则你以为在沙箱里、其实在宿主机上跑）。

零外部依赖跑法（本项目默认配置）：

| 组件 | 用了什么 | 说明 |
|---|---|---|
| 持久化 | `AsyncSQLAlchemyStorage("sqlite+aiosqlite:///data/agent_service.db")` | 不装 Redis 也能跑；多副本换 Postgres |
| 消息总线 | `InMemoryMessageBus` | 单进程；多副本换 `RedisMessageBus` |
| 工作区 | `DockerWorkspaceManager`（默认，容器隔离）；`SANDBOX=local` 换本地 | 每个 agent 一个 workdir，TTL 回收；容器模式实测 `/health` 全组件 ok |

调用注意：`/health` 等接口要求 `x-user-id` 头（多租户隔离），不带会返回 422：

```bash
curl -H "x-user-id: local" http://127.0.0.1:8799/health
```

端口占用：Node `8787` / 出题官 `8790` / Agent Service `8799`，互不冲突。
`agent_service.py` 与出题官是**两套独立服务**，可以只跑其中一套；出题官页面仍走 `8787 → 8790`。


## 快速开始

```bash
git clone https://github.com/ztangeeoo/work-agent.git
cd work-agent
```

```bat
双击 start.bat
```

`start.bat` 会自动：找到/创建 Python 虚拟环境 → 安装依赖 → `npm install` → 构建前端（`web/dist`）→ 启动两个服务 → 打开浏览器。
首次运行安装依赖需要几分钟（AgentScope 依赖树较大，CopilotKit 依赖树也不小）。

手动启动：

```bash
# 终端 1：智能体服务
.venv\Scripts\python.exe agent_server.py

# 终端 2：页面服务（先装根目录依赖：npm install）
npm install
node server.js

# 浏览器打开
http://localhost:8787
```

局域网访问：同一 WiFi 下访问 `http://<你的内网IP>:8787`（启动日志会列出所有可用 IP）。
防火墙如拦截，执行一次（管理员）：

```cmd
netsh advfirewall firewall add rule name="IT面试出题官" dir=in action=allow protocol=TCP localport=8787 profile=private
```

## 开发模式（热更新）

```bash
cd web
npm install     # 首次
npm run dev     # Vite :5173，/api 代理到 :8787
```

打开 http://localhost:5173 。Node（:8787）与 Python（:8790）服务仍需运行。

## 样式与主题（全局规范）

全站配色、字号、间距、圆角、阴影、层级收敛在**一个 token 文件**里，业务代码不写 `#hex`：

```
web/src/styles/tokens.css   唯一 token 源（颜色 / 字体 / 间距 / 圆角 / 阴影 / 动效 / 层级 + 亮暗两套）
web/src/styles.css          组件样式，只消费 var(--md-*)
web/src/main.tsx            引入顺序：先 tokens.css，后 styles.css
docs/design-spec-v1.html    规范正文（怎么用、为什么这么定）
docs/theme-demo.html        实时预览，可切深浅，直接引用 tokens.css 保证同源
```

改主色只需动 `tokens.css` 里的 `--md-brand*` 一组，全站跟着变。核心约定：

- **绿色分轨**：色块 / 色条 / 图标用 `--md-brand`（#07C160）；**写字**一律 `--md-brand-text`（#0E7C46，5.26:1）。微信绿当文字只有 2.38:1。
- **实心按钮用 `--md-brand-solid`**（同为 #0E7C46）—— 白字压在 #07C160 上不达 AA。
- **字号 6 档、字重只有 400 / 500**（中文无真 700，会伪加粗）。
- **深浅主题**：右上角月亮 / 太阳图标切换，偏好存 `localStorage.theme`。切换逻辑在 `web/src/lib/theme.ts`，`web/index.html` 里有一段内联脚本在样式加载前挂类，避免深色用户看到闪白。首次访问默认浅色。

改样式后跑一遍回归，两个测试会读 `styles.css` 断言关键布局约束：

```bash
node --experimental-strip-types tests/test_layout.ts
node --experimental-strip-types tests/test_qcard.ts
```

## 多 Agent 是怎么分工的

| Agent | 触发条件 | 做什么 |
|---|---|---|
| 🧭 策划官 | 首次出题（对话中还没有助手消息）且 `USE_PLANNER=1` | 根据画像与简历产出 3-5 条考察大纲，先流式展示给用户，再交给面试官按大纲出题 |
| 🎯 面试官 | 其他所有出题/追问请求 | 出题、讲解思路、给参考答案、按同样风格再出几道；可调用内置高频题库作参考 |
| 🧑‍⚖️ 评分官 | 用户消息包含「评分 / 评估 / 打个分 / 评价一下 / 复盘」等 | 对候选人最近一次回答打分，输出维度分、亮点、不足、改进建议与参考回答要点，渲染成 Markdown 评分卡 |
| 🔍 侦察官 | 每次代码审核第 1 棒 | 只读 diff 归纳：改动意图、涉及文件、重点风险区（不下结论、不提建议） |
| 🛡️ 审核官 | 代码审核第 2 棒 | 按正确性 / 安全 / 并发性能 / 可维护性 / 兼容测试五个维度输出结构化问题清单（严重度 · 文件 · 行号 · 触发条件 · 改法），最多 10 条 |
| ⚖️ 裁决官 | 代码审核第 3 棒 | 基于前两棒给出风险等级（高/中/低）与合入结论（可合入 / 建议先改 / 打回修改），并给亮点与建议 |

### 界面怎么用

两个智能体是**输入框上方的两个标签**（🎯 面试出题官 / 🛡️ 代码审核官），点一下就切换：

- 切换只改变「谁来回话」，**左侧会话列表保持不变**（不新建、不切换、不过滤会话）；
- 配置都在**弹框**里完成，点输入框上方的「⚙️ 面试配置 / 仓库配置」打开，也可再点一次当前智能体标签呼出：

| 智能体 | 配置弹框 | 内容 |
|---|---|---|
| 🎯 面试出题官 | 面试配置 | 技术方向 / 细分 / 级别 / 经验 / 行业 / 题数 / 语言 / 补充要求 / 简历上传 → 「🚀 确认并生成面试题」 |
| 🛡️ 代码审核官 | 仓库配置 | 代码地址 / 平台 / Token / 轮询间隔 / 自动回帖 → 「测试连接」「保存并轮询」 |

弹框默认关闭，不占对话空间；点遮罩或按 `Esc` 即可关掉，确认生成 / 保存成功后自动关闭。
主操作按钮固定在弹框底部（`.modal-foot`），表单再长也点得到；还没配过仓库时会自动弹出来引导填写。

### 代码审核官怎么用

1. 点输入框上方的「🛡️ 代码审核官」，首次使用（还没有仓库）会自动弹出仓库配置。
2. 填代码地址（`https://github.com/owner/repo`、`github.com/owner/repo`、`git@github.com:owner/repo.git`、`owner/repo` 都能识别；GitLab 同理），私有仓库填 Token，设置轮询间隔与是否自动回帖。
3. 点「测试连接」确认能读到 PR，再点「保存并轮询」；之后按间隔自动轮询，也可在状态条点「立即轮询」。
4. 工作台列出开放 PR，未审的前面有圆点；点某个 PR 可直接审核，或在下一轮自动被审。
5. 审核完成出报告卡：风险 / 结论徽章、问题清单（按严重度分组）、亮点与建议、三智能体耗时；有 Token 时可在卡片上「🚀 回帖到 PR」。
6. 关于这次审核的疑问，直接在聊天框追问（例如「第一个问题怎么改」「写一条 PR 评论」），审核官会带着报告上下文回答。

轮询与审核的安全边界：单轮最多审 `MAX_REVIEWS_PER_POLL`（默认 3）个 PR，其余下轮继续，避免一次刷爆模型额度；
草稿 PR 自动跳过；同 PR 同 head commit 只留一份报告；**自动回帖默认关闭**。

每次请求都是**无状态**的：按前端传来的 messages 重建 Agent 记忆，因此多人同时使用互不串场。

### 执行过程可视化（Agent Trace）

聊天区上方有一块「智能体协同」面板，实时展示这一轮里哪个智能体在干活：

```
┌ 智能体协同 ─── 🧭 策划官执行中 · 2.1s ─────────── ▾ ┐
│ 🧭 策划官    规划考察大纲…                2.1s      │
│ 🎯 面试官    待命                                   │
│ 🧑‍⚖️ 评分官   待命                                   │
└─────────────────────────────────────────────────────┘
```

结束后自动折叠成一行摘要（`已完成 2 个环节 · 跳过 1 · 共 5.8s`），点击可展开回看各环节耗时。

数据链路：

```
agent_server.py  run_pipeline() 各环节起止 → SSE 里发 {agent_stage: {...}}
  → agent_adapter.js 捕获（不进正文）→ server.js 阶段中心按 runId 归集
  → 前端 EventSource(/api/stages?runId=) → useAgentStages → AgentTrace 渲染
```

- 阶段事件是 SSE 里一条不含 `choices` 的自定义载荷，标准 OpenAI 客户端会安全忽略；
- `runId` 由前端每轮生成，随 `/api/model-config` 同步到服务端，用于把事件归到本轮；
- 中途点「停止」时面板显示「已中止」，不会一直转圈。

### 一题一卡（题目卡片）

出题 prompt 要求标题写成 `### 第 N 题【题型】`，前端按这个边界把一条 AI 回复切成多张独立卡片：

```
┌ Q1  【基础概念 + 深挖追问】 ──────────────────────┐  ← 题号徽标 + 题型标签（按类型着色）
│ 某银行核心系统的支付回调服务使用一个 ThreadPoolExecutor…  │
│ 请回答：1. … 2. … 3. …                              │
│ ─────────────────────────────────────────────── │
│ 🎯 考察点  是否真正理解线程池参数与任务提交/执行路径…      │  ← 从【考察点】行抽出的说明条
└─────────────────────────────────────────────────┘
```

- **切分不挑格式**：行首的 `###` / `>` / `**` 前缀都能识别，兼容 `第N题` / `题N` / `问题N` / `QN`，
  题目之间的 `---` 分隔线自动剥离；
- **题型驱动配色**：基础=蓝、场景设计=青、深挖=紫、算法=橙、排查=红，卡片左色条与标签同色；
- **流式边出边成卡**：已完成的题立刻变卡片，最后一道带脉冲点表示「正在出题」；
- **按块渲染 + 稳定 key**：新 token 只更新进行中的那张卡，已出现的卡片不重建（不闪烁、可选中）；
- 非题目内容（开场白、考察大纲、结尾总结）保持普通富文本，不与卡片混排。

## 历史数据存储（SQLite）

历史会话原来是**只存在浏览器 localStorage**（约 5MB 上限，换设备/清缓存即丢）。现在改为：

```
浏览器（localStorage 作为离线缓存）
   │  写入去抖 1.2s
   ▼
Node :8787  /api/convos  ──反向代理──►  Python :8790 /api/convos
                                              └──►  data/interview.db（单文件 SQLite）
```

**为什么是 SQLite**：项目定位是本地/局域网一键启动，用 Python 标准库 `sqlite3`（`store.py`）**不需要任何额外依赖或服务**。1000 场面试 × 40 条消息 = 4 万行，查询在毫秒级。多人并发写或要上公网时再换 Postgres + pgvector。

**表结构**（七张表，都在 `data/interview.db`）

| 表 | 作用 |
|---|---|
| `convo` | 会话元数据 + 面试配置快照（`profile_json`）+ `user_key` 隔离字段 |
| `msg` | 消息正文，按 `convo_id + seq` 排序 |
| `msg_fts` | FTS5 全文索引，`trigram` 分词（中文 3 字以上可命中） |
| `profile_memory` | 候选人能力画像（表已建好，抽取逻辑待接入） |
| `repo` | 代码审核：仓库配置（地址 / provider / Token / 轮询间隔 / 自动回帖 / 上次轮询与错误） |
| `pr` | 代码审核：轮询到的 PR（标题 / 作者 / 分支 / head sha / 状态 / 草稿） |
| `review` | 代码审核：审核报告（风险 / 结论 / 摘要 / Markdown 正文 / 问题清单 / 流水线耗时 / 是否回帖） |

**同步策略**

- 前端写 localStorage 后异步推服务端，1.2s 内的多次变更合并成一次请求（流式输出不会刷爆）
- 切换会话、页面隐藏（`pagehide` / `visibilitychange`）时立即 flush
- 启动时与服务端合并：同一会话 `updatedAt` 大者胜；本地较新的自动回推；服务端独有的会话直接出现在列表里（换浏览器也能看到）
- 服务端不可用（库初始化失败）时自动降级为纯本地模式，功能不中断

**备份**：WAL 模式下有 `interview.db`、`-shm`、`-wal` 三个文件，**停服务后复制整个 `data/` 目录**最稳妥；只拷 `.db` 可能丢最近的写入。

**接口**（Python 提供，`server.js` 反向代理，前端只访问 :8787）

| 端点 | 说明 |
|---|---|
| `GET /api/convos` | 会话列表（默认含消息，`?messages=0` 只要元数据） |
| `POST /api/convos/:id` | 保存整场会话（覆盖式，事务内完成） |
| `GET /api/convos/:id` | 单场详情 |
| `DELETE /api/convos/:id` | 删除（消息与全文索引级联清理） |
| `GET /api/search?q=缓存穿透` | 跨会话全文检索，返回带上下文的片段 |
| `GET /api/db/stats` | 库状态：会话数 / 消息数 / 仓库数 / 报告数 / 文件位置与大小 |
| `GET/POST /api/repos`、`DELETE /api/repos/:id` | 代码仓库配置（列表不含 Token，只给 `hasToken`） |
| `POST /api/repos/:id/test` | 测试地址 / Token 能否读到 PR |
| `POST /api/repos/:id/poll` | 立即轮询（异步，结果走事件流；`?wait=1` 阻塞等待） |
| `GET /api/repos/:id/prs` | PR 列表，附带「当前 head 是否已审」的结论 |
| `POST /api/repos/:id/prs/:n/review` | 立即审核某个 PR |
| `GET /api/reviews?repoId=` | 审核报告列表 |
| `POST /api/reviews/:id/post` | 把某份意见回帖到 PR |
| `GET /api/review/events` | 审核事件流（SSE）：轮询状态 + 审核完成，带历史回放 |

## 配置

### 模型配置（页面右上角齿轮）

任意 OpenAI 兼容接口：DeepSeek、通义、Kimi、tokens.store、Ollama 等。
「拉取模型列表」按钮会列出该接口全部可用模型名。
这份配置同时供**出题对话**与**自动审核轮询**使用（Node 侧留一份给适配器，另同步一份给 Python 轮询器）。

### 环境变量（可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENTSCOPE_URL` | `http://127.0.0.1:8790` | 智能体 / 数据服务地址 |
| `AGENTSCOPE` | `1` | 设为 `0` 时 `/api/chat` 回退为直接转发模型 API（不经过智能体） |
| `AGENT_PORT` | `8790` | 智能体服务端口 |
| `USE_PLANNER` | `1` | 设为 `0` 关闭策划官，省一次模型调用、出题更快 |
| `AGENT_TIMEOUT` | `300` | 单次请求超时（秒） |
| `DB_PATH` | `data/interview.db` | SQLite 库文件路径 |
| `HTTP_PROXY` / `HTTPS_PROXY` | 读取系统环境 | 拉取 PR / 回帖时自动走代理；`localhost` 与代理自身地址直连 |
| `COPILOTKIT_TELEMETRY_DISABLED` | 自动设为 `1` | server.js 启动 runtime 时自动关闭 CopilotKit 匿名遥测，保证纯本地/局域网部署零外网请求 |
| `HARNESS_ADAPTER` | `1` | 设为 `0` 完全旁路可训练指令层，行为与接入前逐字节一致 |
| `HARNESS_ADAPTER_MAX` | `6000` | 单份 adapter 文本的字符上限，防止误写超长文件 |
| `HARNESS_ADAPTER_DIR` | `adapters/` | adapter 根目录，换一份实验版本时指向别处 |

## 可训练指令层（harness adapter）

各 Agent 的 system prompt 有一段**外挂的、可训练的部分**，放在 `adapters/<name>/ADAPTER.md`：

```
adapters/
  interview/  ADAPTER.md  adapter.yaml   → 面试官 + 策划官
  review/     ADAPTER.md  adapter.yaml   → 侦察官 + 审核官 + 裁决官 + 审核追问
```

加载链：`agent_server.py` 各 `*_sys_prompt()` → `harness_adapter.compose(base, name)` → `Session(...).context()`（装了 `hermit-harness` 时）或直接读 `ADAPTER.md`（没装时）。最后拼成 `base + "\n\n" + ADAPTER.md`。

为什么要这一层：

- **提示词从代码里搬出来**：调风格改 markdown 就行，不用改 Python，也不用重启（按目录 mtime 热加载）；
- **可版本化、可回滚**：adapter 目录进 Git，改坏了 revert 即可；
- **可训练**：目录格式遵循 [Hermit](https://github.com/Infini-AI-Lab/hermit) 的 harness adapter 约定（`template` / `exports` / `structure` 三段），以后可以直接用 `hermit train` 拿真实出题记录演化这份文件，产物仍然是 markdown。

边界与取舍：

- **只增不改**：`ADAPTER.md` 只补充 base prompt 没规定的细节，覆盖不了 base 里的格式契约（`### 第 N 题【题型】`、JSON schema）——回归测试会守住这一点；
- **评分官不接**：评分标准需要稳定可复现，先不让它参与演化，等有 held-out 标注再单独开一个 `scoring` adapter；
- **零成本**：`context()` 只读本地文件，不调模型，不影响首字延迟；
- **可关**：`set HARNESS_ADAPTER=0 && node server.js` 即刻退回原行为。

接入前的状态可以用 `GET /api/db/stats` 里的 `harness` 字段自检：

```json
"harness": {"enabled": true, "backend": "hermit", "dir": "...\\adapters",
            "adapters": {"interview": 309, "review": 330}}
```

`backend` 为 `hermit` 表示走 Hermit 的 Session（会校验清单合法性），`file` 表示降级直读，`none` 表示关闭或目录不存在。


## CopilotKit 授权 key 说明

前端用的 `useCopilotChatHeadless_c` 在 CopilotKit 1.83 属于 Intelligence 特性，provider 上需要带一个非空的 `publicLicenseKey`。本项目**默认使用占位值 `self-hosted-local`**，原因：

- 该校验是纯本地布尔检查（`Boolean(publicApiKey)`），不联网验证；
- key 只作为 `x-copilotcloud-public-api-key` 请求头发往 `chatApiEndpoint`，即本项目的自托管 runtime（`/api/copilotkit`），不会外发；
- 因此无需注册账号即可完整体验全部功能，适合本地与局域网部署。

如需改用 Copilot Cloud 增值特性（MCP、AI suggestions 等），可去 <https://cloud.copilotkit.ai> 免费注册，然后在 `web/.env.local` 写入：

```
VITE_COPILOTKIT_KEY=ckpb_你的key
```

重新 `vite build` 后生效。

## 文件说明

| 文件 | 作用 |
|---|---|
| `web/` | React 前端（Vite + TS + CopilotKit）。`src/lib/markdown.ts` 负责题目卡片渲染，`src/lib/stageReducer.ts` 负责流水线状态归并，`src/lib/remote.ts` 负责与服务端历史库同步，`src/lib/review.ts` 负责代码审核接口与展示辅助，`src/components/` 为各界面区块（`AgentSwitcher.tsx` 输入框上方的智能体标签、`InterviewSetup.tsx` / `RepoSetup.tsx` 面试与仓库配置弹框、`AgentTrace.tsx` 流水线面板、`ReviewWorkbench.tsx` 审核工作台） |
| `server.js` | Node 服务：静态托管、`/api/models`、`/api/chat`、`/api/model-config`、`/api/stages`（阶段事件 SSE）、`/api/copilotkit`（CopilotKit runtime），其余 `/api/*` 反向代理到 Python |
| `agent_adapter.js` | CopilotKit → AgentScope 智能体服务的 ServiceAdapter（SSE 转接，并截获智能体阶段事件） |
| `agent_server.py` | FastAPI 服务：AgentScope 多 Agent 编排、`/v1/chat/completions`（OpenAI 兼容 SSE）、`/v1/review`（审核流水线），以及全部 `/api/*` 数据接口 |
| `agent_service.py` | AgentScope 2.0 官方 Agent Service（`create_app`）：SQLite 持久化 + 内存总线 + 本地工作区，独立端口 8799，自带 `/docs` |
| `sandbox_probe.py` | 沙箱自检：建工作区 → 跑命令 / 读写文件 / 校验退出码；默认容器后端，`SANDBOX=local` 切宿主机 |
| `store.py` | 持久层（标准库 `sqlite3`，零依赖）：七张表建表迁移、会话读写、FTS5 检索、仓库 / PR / 审核报告 CRUD、容量上限保护 |
| `reviewer.py` | 代码托管平台适配：解析仓库地址、拉开放 PR 与 diff、回帖；自动识别 `HTTP(S)_PROXY`，`localhost` 直连 |
| `review_poller.py` | PR 轮询调度：同仓库串行、单轮限流、到期触发、事件广播；模型配置从页面内存读取 |
| `question_bank.py` | 内置高频面试题库（11 个主方向 × 细分），作为面试官的检索工具 |
| `harness_adapter.py` | 可训练指令层加载：`compose(base, name)` 把 `adapters/<name>/ADAPTER.md` 接到 system prompt 后；hermit 可缺省、按 mtime 热加载、`HARNESS_ADAPTER=0` 可旁路 |
| `adapters/` | 可训练的外挂指令（Hermit 格式）：`interview/`（面试官 + 策划官）、`review/`（侦察官 + 审核官 + 裁决官 + 审核追问） |
| `data/interview.db` | 会话 / 仓库 / 审核报告数据库（自动创建，已加入 `.gitignore`） |
| `tests/` | 回归测试：`test_store.py`（持久层）、`test_reviewer.py`（地址解析 / diff / 代理规则）、`test_adapter.py`（指令层加载 / 降级 / 热更新 / 格式契约）、`test_web.ts`（渲染逻辑）、`test_qcard.ts`（卡片布局与历史守卫）、`test_stages.ts`（流水线状态）、`test_layout.ts`（布局约束）、`test_render.js`（旧版渲染逻辑）、`mock_llm.js`（mock 上游验全链路） |
| `docs/` | `design-spec-v1.html` 全局样式主题规范 · `theme-demo.html` 主题实时预览（可切深浅） |
| `index.html` | 旧版单文件应用（保留作回退，不再维护） |
| `start.bat` | 一键启动 |

## 测试

```bash
.venv\Scripts\python.exe tests/test_agentscope_api.py # AgentScope 2.0 运行时基线：版本/API 面/事件字段/上下文语义（40 断言）
.venv\Scripts\python.exe sandbox_probe.py             # 沙箱自检：命令执行 / 文件读写 / 退出码（默认容器，SANDBOX=local 切本地）
.venv\Scripts\python.exe tests/test_store.py          # 持久层：会话/检索/隔离/仓库/审核报告（85 断言）
.venv\Scripts\python.exe tests/test_reviewer.py       # 仓库地址解析 / diff 统计 / 代理规则（32 断言）
.venv\Scripts\python.exe tests/test_adapter.py        # 指令层：降级 / 拼接 / 开关 / 截断 / 热更新 / 格式契约 / 接线守卫（38 断言）
node --experimental-strip-types tests/test_web.ts     # 渲染 / 题目卡片逻辑（47 断言）
node --experimental-strip-types tests/test_qcard.ts   # 卡片布局 / 历史恢复守卫（27 断言）
node --experimental-strip-types tests/test_stages.ts  # 智能体流水线状态归并（23 断言）
node --experimental-strip-types tests/test_layout.ts  # 滚动布局 + 智能体切换/配置弹框（54 断言）
node tests/test_render.js                             # 旧版单文件渲染逻辑（26 断言）
node tests/mock_llm.js                                # mock 上游（含审核三智能体应答），配合 curl 验证全链路
```

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| 启动报 `本项目需要 AgentScope 2.0+，当前安装的是 1.x` | 环境里是旧版本：`.venv\Scripts\python.exe -m pip install -U -r requirements.txt`；`start.bat` 也会自动检测升级 |
| `ImportError: cannot import name 'ReActAgent'` 之类 | 代码已是 2.0 写法，装的是 1.x 或照旧教程装了 `agentscope-runtime`，按上一行升级即可 |
| 上下文很长、模型重复回答同一个问题 | 检查是否又给 `AgentState(context=...)` 预置了历史又往 `inputs` 里传了一遍（见「上下文语义」） |
| 页面提示「AgentScope 智能体服务未连接」 | Python 服务没起来：双击 `start.bat`，或手动 `.venv\Scripts\python.exe agent_server.py` |
| 出题报「模型未配置」 | 点右上角齿轮完成模型配置；每次发消息前页面会自动同步配置到 Node 与 Python |
| `模型 API Key 无效`（401） | 齿轮弹框里 Key 填错或额度不足 |
| `接口或模型不存在`（404） | API 地址要以 `/v1` 结尾；或模型名不对，点「拉取模型列表」选一个 |
| `连接被拒绝 ECONNREFUSED` | 本地模型（Ollama）没启动，或地址端口写错 |
| `/api/copilotkit` 返回 503 | 根目录 `npm install` 没执行（CopilotKit runtime 加载失败） |
| 历史会话 / 仓库配置读不到 | 看启动日志「历史库」一行；或 `GET /api/db/stats` 看 `dbAvailable`。数据服务在 Python（:8790），确认它在线 |
| 拉 PR 报 `HTTP 403` | GitHub 匿名调用有速率限制；填 Token 即可（私有仓库必须填） |
| 拉 PR 一直超时 | 本机需要代理才能访问 GitHub / GitLab：确认 `HTTPS_PROXY` 已设置（服务会自动走代理） |
| 仓库一直「未配置模型」 | 还没在页面齿轮里配模型——轮询会继续但不会审核，配好后下一轮自动开始审 |
| `vite build` 报 `emptyDir` 权限错误 | Node 服务正在占用 `web/dist`，先停服务再构建 |
| 想换回「无智能体」直连模式 | `set AGENTSCOPE=0 && node server.js` |
