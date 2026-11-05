# 专家插件

一条业务线 = 一个目录。加业务线只加目录，不改 `app/` 里的任何 `if` 分支。

```
backend/plugins/
  interview/         面试出题官（默认业务线）
    plugin.yaml      清单：mode、对外专家 id、入口、角色、工具、options
    pipeline.py      chat(ctx)：策划官 → 面试官 / 评分官
    prompts.py       三个角色的 system prompt
    intent.py        业务线内部意图路由（出题 vs 评分）
    preferences/     可训练偏好（hermit 格式）
      ADAPTER.md     偏好正文，只补 base 没规定的细节
      adapter.yaml   hermit 清单（template / exports / structure）
  review/            代码审核官
    plugin.yaml
    pipeline.py      chat(ctx) 审核追问 / run(payload) PR 审核
    prompts.py
    preferences/
```

## 最小步骤

1. 复制 `review/` 为 `<你的 id>/`。**目录名必须是小写字母 / 数字 / 下划线**（要当 Python 包名）。
2. **删掉 `preferences/` 或改写它**——照着拷会把审核官的可训练偏好原样带过来，
   新业务线的 prompt 末尾就多了一段不相干的规矩。不需要偏好层就整目录删掉（缺了自动跳过，不报错）。
3. 改 `plugin.yaml`：`id`、`mode`、`expert_id`、`name`、`stages`。
4. 改 `pipeline.py`：至少实现 `chat(ctx)` 或 `run(payload)` 之一，并在 `entry` 里声明。
5. 重启服务。启动时会把 `expert` / `agent` 两张表同步成这份清单的投影，
   前端专家中心自动出现；yaml 写错了会进 `problems()`，不影响服务启动。

## 清单字段

### 专家级

| 字段 | 必填 | 含义 |
|---|---|---|
| `id` / 目录名 | 是 | Python 包名、插件身份，`interview` |
| `mode` | 否 | 前端请求体 `agent_mode` 的值，默认同 `id` |
| `expert_id` | 否 | 对外专家 id，默认同 `id`。**历史取值与目录名不同时必须写**（`interview` → `interviewer`） |
| `name` / `icon` | 否 | 专家展示名与单字头像 |
| `role` / `desc` / `tags` / `cta` / `sort` | 否 | 专家卡片文案，首次建档时写入库 |
| `entry` | 是 | `模块:函数`，至少一个：`chat`（流式）/ `job`（一次性） |
| `stages` | 是 | 流水线角色，见下 |
| `options` | 否 | 自己的业务配置，原样进 `ctx.options` |
| `requires` | 否 | 包级依赖声明（自检用）；**实际装配看 `stages[].tools`** |

### 角色级（`stages[]`）

| 字段 | 含义 |
|---|---|
| `id` | 角色键（`planner` / `interviewer` …），SSE 与前端按它渲染 |
| `label` / `icon` | 流水线面板上的名字与 emoji |
| `avatar` | 专家中心用的**单字**头像（`谋` / `面` / `评`）。和 `icon` 是两个位置，别混 |
| `role` | 一句话职责，写进 agent 表的 `role` |
| `order` | 顺序，也作为库里的 `sort` |
| `primary` | 是否主智能体（每个专家有且只有一个） |
| `tools` | 该角色能用的工具名：`question_bank` / `repo_readonly`。**`[]` = 明确不装；不写 = 插件没接管，走旧启发式** |

## 偏好层（可训练指令）

`preferences/ADAPTER.md` 会被 `services/harness.compose(base, name)` 拼到该业务线的
system prompt 末尾（`base + "\n\n" + ADAPTER.md`）。`name` 就是**插件目录名**。

- 纯文本，改完下一次请求即生效（按目录 mtime 热加载，不用重启）；
- `adapter.yaml` 是 Hermit 的清单，`learnable.path` 指向 `ADAPTER.md`，装了 `hermit-harness`
  时走 `Session(d).context()`，没装就直读文件；
- **只增不改**：偏好只补 base 没规定的细节，盖不掉 base 里的格式契约（`### 第 N 题【题型】`、JSON schema）；
- 想整体换一份实验版本：`HARNESS_ADAPTER_DIR=<目录>`，此时**独占**，不会回落插件里的版本。

以前这块单独放 `backend/adapters/<name>/`，与插件目录同名却不同义，加业务线要在两个根各建一个目录。

## 两个入口的签名

```python
async def chat(ctx: ChatCtx) -> AsyncIterator[str]:
    """流式。yield SSE 字符串（用 backend.app.utils.sse 的 sse_chunk / sse_stage）"""
    yield sse_stage("advisor", "running")
    ...
    yield "data: [DONE]\n\n"

async def run(payload: dict) -> dict:
    """一次性任务。失败直接抛异常：ValueError → 400，其他 → 502"""
    return {"report": {...}}
```

`ctx` 里有：`payload`、`model`（已构造好，带缓存）、`base`（实际生效地址，404 补 `/v1`
重试时会变）、`model_name`、`cid`、`created`、`history`（AgentScope Msg 列表）、
`last_user`、`has_assistant`（历史里是否已有助手回复）、`profile`、`options`、`log()`。

**不要自己 `draft_model()`**，也不要往 payload 里塞 `_history` 之类的私有键。

## 库是清单的投影：谁能改什么

启动时 `store/db.py::_sync_catalog()` 会同步 `expert` / `agent` 两张表。覆盖规则分两类：

| 字段 | 以谁为准 | 后果 |
|---|---|---|
| 结构：`mode` / `expert_id` / `stage` / `is_primary` / `sort` / `enabled` | **插件清单** | 加角色只改 yaml，重启自动出现在库里 |
| 文案：`name` / `icon` / `role` / `desc` / `tags` / `cta` | **库**（首次建档才用 yaml 的值） | 改文案直接改库，重启不覆盖；想让 yaml 生效就删掉库里那行 |

- 插件里已经没有的专家 / 角色 → `enabled = 0` 软停用，**不删行**：历史会话的
  `convo.agent_id` 还指得到，`/api/experts` 不再列出，避免「前端能点、后端没有这个 mode」的 500。
- 插件清单**整个读不到**时（`PLUGINS_ENABLED=0`、目录没了）什么都不动，回落到 `SEED_*`
  内置配置，绝不清空库。
- 同步包在 `SAVEPOINT` 里：yaml 写错（例如两个 `primary`）时回滚到兜底配置并记日志。

## 边界：角色不是插件

`stages` 里的每一项是这条流水线上的编排位置，不是可独立插拔的单元——
侦察官离开审核官就没有消费者，评分官离开面试官没有输入，策划官的大纲只被面试官消费。
插件自己按顺序驱动它们。

`core/stages.py` 里的 `STAGE_META` / `EXPERT_STAGES` **已经是这份清单的投影**（模块级
`__getattr__`，每次访问现算），不要在那边再手抄一份角色表。

## 依赖规则

- 允许 import：`app.agents.runtime` / `app.agents.contract` / `app.llm` / `app.schemas` /
  `app.utils` / `app.core` / `app.store` / `app.prompts.profile`（公共片段）/ `app.services.harness`
- **禁止** import：其他插件目录、`app.api`（插件不该知道 HTTP 长什么样）
- `chat(ctx)` 里不要自己 `draft_model()`（`ctx.model` 已构造好，带缓存）；
  `run(payload)` 没有 ctx，需要自己建模型
- 新增工具：实现后在 `app/tools/registry.py` 的 `TOOL_FACTORIES` 里登记一个名字，
  再到 `stages[].tools` 引用
