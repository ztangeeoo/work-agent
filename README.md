# IT 面试出题官（React + CopilotKit + AgentScope）

一个本地运行的技术面试出题智能体：配置自己的模型（任意 OpenAI 兼容接口），选择技术方向 / 细分方向 / 级别 / 经验 / 行业背景，上传简历（txt / md / pdf / docx / 图片），由 **AgentScope 多智能体** 生成针对性面试题，并支持对话式追问与结构化评分。

前端为 **React SPA（Vite）+ CopilotKit**，与 Node / Python 服务前后端分离；三智能体流水线完整保留。

## 架构

```
浏览器（React SPA · web/，Vite 构建产物在 web/dist）
  │  CopilotKit（useCopilotChatHeadless_c，runtimeUrl=/api/copilotkit）
  ▼
server.js（Node :8787）
  ├── GET  /                托管 React 构建产物（web/dist，未构建时回退旧 index.html）
  ├── GET  /assets/...      Vite 静态资源
  ├── POST /api/models      直接代理模型接口，拉取可用模型列表
  ├── POST /api/chat        转发给智能体服务（测试连接 / 图片简历视觉识别）
  ├── POST /api/model-config  保存前端最新模型配置（内存，供适配器读取）
  └── POST /api/copilotkit  CopilotKit 自托管 runtime
        └── agent_adapter.js（AgentScopeAdapter）
              ├── 读取模型配置（base / key / model / profile）
              ├── 转成 OpenAI 兼容请求 POST :8790/v1/chat/completions
              └── SSE 增量流转发回 CopilotKit 事件流
  ▼
agent_server.py（Python :8790，AgentScope + FastAPI）
  │  POST /v1/chat/completions   OpenAI 兼容，SSE 流式
  ▼
┌─────────────── AgentScope 多 Agent 编排 ───────────────┐
│  🧭 策划官 Planner     首次出题前产出考察大纲            │
│  🎯 面试官 Interviewer + 高频题库工具：出题/追问         │
│  🧑‍⚖️ 评分官 Evaluator  结构化输出：回答评分卡            │
└───────────────────────────────────────────────────────┘
  │
  ▼
你配置的模型 API（DeepSeek / tokens.store / Ollama / ...）
```

为什么用 CopilotKit 而不引整套 React UI 框架：CopilotKit 只负责「消息状态 / 流式 / runtime 协议」，
消息列表仍自渲染，保留「一题一卡」的卡片化出题显示。

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

## 多 Agent 是怎么分工的

| Agent | 触发条件 | 做什么 |
|---|---|---|
| 🧭 策划官 | 首次出题（对话中还没有助手消息）且 `USE_PLANNER=1` | 根据画像与简历产出 3-5 条考察大纲，先流式展示给用户，再交给面试官按大纲出题 |
| 🎯 面试官 | 其他所有出题/追问请求 | 出题、讲解思路、给参考答案、按同样风格再出几道；可调用内置高频题库作参考 |
| 🧑‍⚖️ 评分官 | 用户消息包含「评分 / 评估 / 打个分 / 评价一下 / 复盘」等 | 对候选人最近一次回答打分，输出维度分、亮点、不足、改进建议与参考回答要点，渲染成 Markdown 评分卡 |

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

## 历史数据存储（SQLite）

历史会话原来是**只存在浏览器 localStorage**（约 5MB 上限，换设备/清缓存即丢）。现在改为：

```
浏览器（localStorage 作为离线缓存）
   │  写入去抖 1.2s
   ▼
Node :8787  /api/convos  ──►  data/interview.db（单文件 SQLite）
```

**为什么是 SQLite**：项目定位是本地/局域网一键启动，用 Node 22 内置的 `node:sqlite`（`DatabaseSync`）**不需要任何 npm 依赖**，也没有额外服务、端口和账号。1000 场面试 × 40 条消息 = 4 万行，查询在毫秒级。多人并发写或要上公网时再换 Postgres + pgvector。

**表结构**

| 表 | 作用 |
|---|---|
| `convo` | 会话元数据 + 面试配置快照（`profile_json`）+ `user_key` 隔离字段 |
| `msg` | 消息正文，按 `convo_id + seq` 排序 |
| `msg_fts` | FTS5 全文索引，`trigram` 分词（中文 3 字以上可命中） |
| `profile_memory` | 候选人能力画像（表已建好，抽取逻辑待接入） |

**同步策略**

- 前端写 localStorage 后异步推服务端，1.2s 内的多次变更合并成一次请求（流式输出不会刷爆）
- 切换会话、页面隐藏（`pagehide` / `visibilitychange`）时立即 flush
- 启动时与服务端合并：同一会话 `updatedAt` 大者胜；本地较新的自动回推；服务端独有的会话直接出现在列表里（换浏览器也能看到）
- 服务端不可用（Node < 22.5 或库初始化失败）时自动降级为纯本地模式，功能不中断

**备份**：WAL 模式下有 `interview.db`、`-shm`、`-wal` 三个文件，**停服务后复制整个 `data/` 目录**最稳妥；只拷 `.db` 可能丢最近的写入。

**接口**

| 端点 | 说明 |
|---|---|
| `GET /api/convos` | 会话列表（默认含消息，`?messages=0` 只要元数据） |
| `POST /api/convos/:id` | 保存整场会话（覆盖式，事务内完成） |
| `GET /api/convos/:id` | 单场详情 |
| `DELETE /api/convos/:id` | 删除（消息与全文索引级联清理） |
| `GET /api/search?q=缓存穿透` | 跨会话全文检索，返回带上下文的片段 |
| `GET /api/db/stats` | 库状态：会话数 / 消息数 / 文件位置与大小 |

## 配置

### 模型配置（页面右上角齿轮）

任意 OpenAI 兼容接口：DeepSeek、通义、Kimi、tokens.store、Ollama 等。
「拉取模型列表」按钮会列出该接口全部可用模型名。

### 环境变量（可选）

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENTSCOPE_URL` | `http://127.0.0.1:8790` | 智能体服务地址 |
| `AGENTSCOPE` | `1` | 设为 `0` 时 `/api/chat` 回退为直接转发模型 API（不经过智能体） |
| `AGENT_PORT` | `8790` | 智能体服务端口 |
| `USE_PLANNER` | `1` | 设为 `0` 关闭策划官，省一次模型调用、出题更快 |
| `AGENT_TIMEOUT` | `300` | 单次请求超时（秒） |
| `DB_PATH` | `data/interview.db` | SQLite 库文件路径 |
| `COPILOTKIT_TELEMETRY_DISABLED` | 自动设为 `1` | server.js 启动 runtime 时自动关闭 CopilotKit 匿名遥测，保证纯本地/局域网部署零外网请求 |

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
| `web/` | React 前端（Vite + TS + CopilotKit）。`src/lib/markdown.ts` 负责题目卡片渲染，`src/lib/stageReducer.ts` 负责流水线状态归并，`src/lib/remote.ts` 负责与服务端历史库同步，`src/components/` 为各界面区块（`AgentTrace.tsx` 是执行流水线面板） |
| `server.js` | Node 服务：静态托管、`/api/models`、`/api/chat`、`/api/model-config`、`/api/stages`（阶段事件 SSE）、`/api/copilotkit`（CopilotKit runtime）、`/api/convos` 与 `/api/search`（历史库） |
| `db.js` | SQLite 持久层（`node:sqlite`，零依赖）：建表迁移、会话读写、FTS5 检索、容量上限保护 |
| `agent_adapter.js` | CopilotKit → AgentScope 智能体服务的 ServiceAdapter（SSE 转接，并截获智能体阶段事件） |
| `agent_server.py` | AgentScope 智能体服务：多 Agent 编排、OpenAI 兼容 SSE 接口、阶段事件、错误人性化提示 |
| `question_bank.py` | 内置高频面试题库（11 个主方向 × 细分），作为面试官的检索工具 |
| `data/interview.db` | 历史会话数据库（自动创建，已加入 `.gitignore`） |
| `tests/` | 回归测试：`test_db.js`（持久层）、`test_web.ts`（渲染逻辑）、`test_stages.ts`（流水线状态）、`test_layout.ts`（布局约束）、`test_render.js`（旧版渲染逻辑）、`mock_llm.js`（mock 上游验全链路） |
| `index.html` | 旧版单文件应用（保留作回退，不再维护） |
| `start.bat` | 一键启动 |

## 测试

```bash
node tests/test_db.js                              # 持久层：读写/检索/隔离/级联（29 断言）
node --experimental-strip-types tests/test_web.ts     # 渲染 / 题目卡片逻辑（25 断言）
node --experimental-strip-types tests/test_stages.ts  # 智能体流水线状态归并（23 断言）
node --experimental-strip-types tests/test_layout.ts  # 聊天区滚动布局约束（17 断言）
node tests/test_render.js                            # 旧版单文件渲染逻辑（26 断言）
node tests/mock_llm.js                               # mock 上游，配合 curl 验证全链路
```

## 排障

| 现象 | 原因 / 处理 |
|---|---|
| 页面提示「AgentScope 智能体服务未连接」 | Python 服务没起来：双击 `start.bat`，或手动 `.venv\Scripts\python.exe agent_server.py` |
| 出题报「模型未配置」 | 点右上角齿轮完成模型配置；每次发消息前页面会自动同步配置到 Node 服务 |
| `模型 API Key 无效`（401） | 齿轮弹框里 Key 填错或额度不足 |
| `接口或模型不存在`（404） | API 地址要以 `/v1` 结尾；或模型名不对，点「拉取模型列表」选一个 |
| `连接被拒绝 ECONNREFUSED` | 本地模型（Ollama）没启动，或地址端口写错 |
| `/api/copilotkit` 返回 503 | 根目录 `npm install` 没执行（CopilotKit runtime 加载失败） |
| 历史会话没同步到数据库 | 看启动日志「历史库」一行：若提示 `node:sqlite 不可用`，说明 Node < 22.5，升级 Node 即可；否则检查 `GET /api/db/stats` |
| `vite build` 报 `emptyDir` 权限错误 | Node 服务正在占用 `web/dist`，先停服务再构建 |
| 想换回「无智能体」直连模式 | `set AGENTSCOPE=0 && node server.js` |
