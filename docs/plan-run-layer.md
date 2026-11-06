# 执行托管层（Run Layer）方案

> 目标：把「一次执行」的归属从 HTTP 连接搬到数据库里的**一行记录**。
> 切会话不断流、多 PR 并行、审核进度可见，是同一个改动的三个表现，不是三个功能。

---

## 1. 现状与缺口

| 链路 | 现在怎么跑 | 归属 | 断连 / 重启后果 |
|---|---|---|---|
| 聊天生成 | `chat.py:112` SSE 流式返回 | **请求** | 切会话 → `switchConvo` 主动 `stopGeneration()`，后半段丢失 |
| 手动审核 | `POST /v1/review` 一次性 HTTP，最长 600s | **请求** | 黑屏等待；刷新即丢；无法查进度 |
| 轮询审核 | `poller.py:41` 内存 `_inflight` | **进程内存** | `kill` 后正在跑的活凭空消失；跨仓库并发无上限 |
| 阶段事件 | `server.js:835` → `/api/stages` SSE | 内存 | 只服务面试线；审核的 `REVIEW_STAGE_ORDER` 是死代码 |
| 消息持久化 | `remote.ts` 整场覆盖 POST | 前端 | 半句话会盖掉完整回复 |

两个结论：

1. **协议不是问题**。聊天已经是 SSE，切会话照样断——SSE 只是长 HTTP 响应，连接没了生成就没保证。
2. **真正的问题是「谁负责把它跑完」**。现在答案分别是「那条请求」「那个进程」，都不持久。

---

## 2. 目标架构

```
             ┌────────────── 订阅（可断可重连）──────────────┐
             │                                              │
事件源 ──→ 准入 ──→ run 表（唯一真相） ──→ 执行器 ──→ 副作用队列
(轮询/手动)  (Policy)    │                  (槽位/串行)      (回帖重试)
                        │
                        └──→ run_event 表 ──→ GET /api/runs/{id}/events (SSE)
```

四条原则：

- **单写者**：`run` 的所有状态迁移只走 `engine.py`，其他模块只能调用它。
- **执行与连接解耦**：HTTP 请求只是订阅者，断开 = 退订，任务继续跑。
- **状态落库**：状态、正文、事件都在 SQLite，进程重启后可恢复/可查询。
- **订阅可断**：前端切走再切回，拉一次最新状态即可，不需要逐字续播。

---

## 3. 五个设计决策

### D1 · 一张 `run` 表，不建两套

聊天、审核、将来的批量出题都是「一次有生命周期的执行」，用 `kind` 区分，用 `scope` 表达"谁跟谁不能并行"：

| kind | scope | subject_key（幂等） |
|---|---|---|
| `chat` | `convo_id` | 空 |
| `review.pr` | `repo_id` | `owner/name#123@sha` |
| `interview.batch` | `convo_id` | 空 |

**为什么不建两张**：现在 `poller` 和 `chat` 各自维护一套执行逻辑（一个在内存 dict，一个在请求里），这正是"切会话丢结果、重启丢工单"并存的原因。合成一张表，恢复逻辑、并发控制、面板查询都只需要写一遍。

### D2 · 托管：生成跑在独立 task 里

```python
rec["buf"] = []
async def pump():
    try:
        async for chunk in gen:
            rec["buf"].append(chunk)
            for q in list(rec["subs"]): q.put_nowait(chunk)
        rec["status"] = "done"
    except asyncio.CancelledError:
        rec["status"] = "cancelled"; raise
    except Exception as e:
        rec["status"] = "error"; rec["error"] = str(e)
    finally:
        for q in list(rec["subs"]): q.put_nowait(None)
        await on_done(rec)          # 落库：写 text + status
rec["task"] = asyncio.create_task(pump())   # 必须强引用，否则被 GC
```

请求侧只做订阅：先补发 `rec["buf"]`，再从队列读；`finally` 里**只退订，绝不 `aclose()` 那个 task**。

### D3 · 补齐优于续传

不做 `offset`、不做 `Last-Event-ID`、不做 EventSource 重连补偿。

理由：用户切回来时，任务多半已经跑完，他需要的是**看到完整结果**，不是看逐字回放。所以切回来只做一次 `GET /api/runs/{id}`，拿最终正文替换本地那条消息。这一条把复杂度砍掉一个数量级。

### D4 · 覆盖保存改「同 id 取长」

不修这条，前面全白做：后端写好完整回复 → 前端切回来 `setMessages(本地半句)` → `saveCurrent()` 把服务端的完整版盖回半句。

规则：`save_convo` 对已存在的 `msg.id`，**新内容比库里短就保留旧的**。顺带治好"切会话瞬间旧消息回流覆盖历史"的老毛病（`db.py:87` 那条注释里的坑）。

前端补齐判断（`ChatApp.tsx:293`）也要从「条数更多」改成「条数更多 **或** 末尾内容更长」——现在是纯比条数，同一条消息变长永远补不上。

### D5 · 并行三约束

| 约束 | 实现 | 为什么 |
|---|---|---|
| 全局槽位 ≤ 3 | `asyncio.Semaphore(3)` | 瓶颈是模型接口额度，不是 CPU |
| 同 scope 串行 | claim SQL 里 `NOT EXISTS (... running with same scope)` | 同仓库抢 token、回帖交错 |
| 同 subject 唯一 | `idx_run_live` 部分唯一索引 | 同一个 PR 的同一个 sha 不重复审 |

领取必须一步完成（SQLite 3.35+ 支持 `RETURNING`）：

```sql
UPDATE run SET status='running', locked_by=?, locked_at=?, attempt=attempt+1, updated_at=?
WHERE id = (
  SELECT r.id FROM run r
   WHERE r.status='queued' AND (r.not_before IS NULL OR r.not_before <= ?)
     AND NOT EXISTS (SELECT 1 FROM run x
                      WHERE x.status='running' AND x.scope = r.scope)
   ORDER BY r.created_at LIMIT 1)
AND status='queued'
RETURNING id, kind, scope, payload_json;
```

**注意**：不要把握手放进事务。claim 提交之后再去拉 diff、调模型；SQLite 单连接 + `RLock` 会串行化写，模型调用包在事务里会让并行直接退化成串行。

---

## 4. 表结构

```sql
CREATE TABLE IF NOT EXISTS run (
  id           TEXT PRIMARY KEY,
  kind         TEXT NOT NULL,                 -- chat / review.pr / interview.batch
  scope        TEXT NOT NULL,                 -- convo_id 或 repo_id：同 scope 串行
  subject_key  TEXT,                          -- 幂等键（聊天为空）
  mode         TEXT NOT NULL DEFAULT 'auto',  -- auto / observe（干跑不回帖）
  status       TEXT NOT NULL,                 -- queued/running/done/failed/waiting_human/cancelled
  stage        TEXT,                          -- 当前环节，前端画进度用
  text         TEXT,                          -- 聊天累积正文
  payload_json TEXT,
  result_json  TEXT,
  error        TEXT,
  attempt      INTEGER NOT NULL DEFAULT 0,
  max_attempt  INTEGER NOT NULL DEFAULT 2,
  not_before   INTEGER,                       -- 退避后下次可执行时间
  locked_by    TEXT,                          -- 进程实例 id，崩溃恢复用
  locked_at    INTEGER,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_run_live ON run(kind, subject_key)
  WHERE subject_key IS NOT NULL AND status IN ('queued','running','waiting_human');
CREATE INDEX IF NOT EXISTS idx_run_scope ON run(scope, status);
CREATE INDEX IF NOT EXISTS idx_run_claim ON run(status, not_before, created_at);

CREATE TABLE IF NOT EXISTS run_event (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL,
  type         TEXT NOT NULL,                 -- stage / delta / done / error / human
  payload_json TEXT,
  at           INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_event ON run_event(run_id, id);
```

崩溃恢复：启动时把 `status='running' AND locked_at < now - 超时` 的行改回 `queued`（`attempt+1`）；超过 `max_attempt` 转 `waiting_human`。

---

## 5. 分期

| 期 | 范围 | 改动 | 验收 | 回滚 |
|---|---|---|---|---|
| **P0**<br>止血 | 不再用半句话覆盖完整回复 | `store/convos.py` 取长；`ChatApp.tsx:293` 判断；中断标记 | 切走再切回，看到完整回复或明确的中断提示 | 纯函数级，revert 即可 |
| **P1**<br>聊天托管 | 切会话 / 关页面 / 刷新都不丢 | 新增 `services/chat_runs.py`；`chat.py` 订阅化；`GET /api/runs/{id}`；前端切回补齐 | 发长回复 → 切会话 → 等 10s → 切回，内容完整；关浏览器同理 | `RUNS_ENABLED=0` 回到请求内执行 |
| **P2**<br>审核工单 + 并行 + 调度 | 多 PR 并行、重启不丢活、定时任务与执行脱钩 | `db.py` 加三张表（`run` / `run_event` / `schedule`）；新增 `jobs/{engine,runner,policy,scheduler}.py`；`poller.py` 瘦身为 Source；手动审核改 intake | 3 仓库 9 PR：并发恒 ≤3、同仓库串行；`kill -9` 后工单自动重跑且不重复回帖；停机 2h 后启动只补跑 1 次 | 开关 + 旧代码保留一个版本 |
| **P3**<br>审核流式进度 | 点下去不再黑屏 600s | `pipeline.py` 的 `stage_done` 挂回调；`/v1/review/stream`；`review.ts` 改读流 | 1 秒内看到侦察官亮起；401 在 1 秒内报错而非等 600s | 新老接口并存，前端可切回 |
| **P4**<br>订阅直通 + 副作用 | 高性价比的收尾 | `thread` 表；`effect` 队列（回帖退避 3 次 → waiting_human） | 回帖失败自动重试；同一话题后续消息直达同一 run | 独立模块 |

### P1 关键骨架

```python
# backend/app/services/chat_runs.py
_runs: dict[str, dict] = {}

def start(run_id: str, gen, on_done): ...        # 见 D2
def subscribe(run_id: str) -> tuple[list, asyncio.Queue]: ...
def snapshot(run_id: str) -> dict | None: ...
```

`chat.py` 的 `body()`：补发 → 读队列 → `finally: subs.discard(q)`。

落库时机：`on_done` 里写 `run.text` + `run.status`，并把正文 upsert 成一条 `msg`（配合 D4 的取长规则）。

### P2 关键骨架

```python
# backend/app/jobs/runner.py
_slots = asyncio.Semaphore(SLOTS)          # 3

async def _loop():
    while True:
        await asyncio.sleep(TICK_SEC)       # 3s
        while _slots.locked() is False:
            row = engine.claim_next()       # 原子 UPDATE ... RETURNING
            if not row: break
            asyncio.create_task(_execute(row))
```

`_execute` 里调 `registry.for_mode(kind 前缀).job(payload)`，把 `on_stage` 回调接到 `run_event`，失败按 `min(2**attempt * 30, 900)` 退避。

`poller.py` 保留 `list_open_prs` + 到期判断，删掉 `review_one` 与 `_inflight`——发现即 `engine.intake()`，执行交给 runner。

---

## 6. 调度层：定时任务怎么兼容

### 6.1 定时任务不是一种 run，是 run 的生产者

现在 `services/poller.py` 把两件事压在同一层：`_loop()` 每 20s 扫到期仓库，`create_task(self.poll_repo(...))`，而 `poll_repo` 里真的在跑 600s 的审核。等于「闹钟一边响一边干活」。

两者的可靠性要求正好相反：

| | 闹钟（schedule） | 活（run） |
|---|---|---|
| 要求 | 长期活着、绝不阻塞、极简 | 可以慢、可以失败、可以重试 |
| 失败后果 | 后面所有 tick 全乱 | 单次任务重跑 |
| 状态 | `next_at` 一个字段 | 完整生命周期 |

**结论：闹钟只做一件事——把「该干什么」写成一行 run，然后立刻返回。** 所有复杂度下沉到 run 层（槽位、退避、崩溃恢复在 P2 已经有了）。

### 6.2 表结构

```sql
CREATE TABLE IF NOT EXISTS schedule (
  id          TEXT PRIMARY KEY,               -- 'repo.poll:<repo_id>' / 'cron:daily.review'
  kind        TEXT NOT NULL,                  -- 到点后生成哪种 run
  scope       TEXT NOT NULL,                  -- 同 scope 串行，通常等于 repo_id
  every_sec   INTEGER NOT NULL,
  next_at     INTEGER NOT NULL,               -- 下次应触发（墙钟，跨重启必须用墙钟）
  last_at     INTEGER,
  catch_up    TEXT NOT NULL DEFAULT 'skip',   -- skip / once / all：停机期间错过怎么补
  max_backlog INTEGER NOT NULL DEFAULT 1,     -- 同 scope 最多积压几张 run
  enabled     INTEGER NOT NULL DEFAULT 1,
  fail_streak INTEGER NOT NULL DEFAULT 0,     -- 连续触发失败，看板用
  last_error  TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_schedule_due ON schedule(enabled, next_at);
```

### 6.3 推进用 CAS，不用「先查再改」

```sql
UPDATE schedule
   SET last_at = next_at, next_at = ?, updated_at = ?
 WHERE id = ? AND next_at = ?      -- 最后这个 ? 是读到的旧值
```

返回 `rowcount`，0 表示被别人抢先推进了，本轮安静跳过。

比「一条 UPDATE 里塞 CASE 算新值」更清楚的地方在于：新值需要考虑 jitter 和 catch_up 两种策略，逻辑放进 SQL 会既难读又没法单测。CAS 只负责「同一个到期点只放行一个」，算法留在 Python 里：

```python
def advance(prev_next_at, every_sec, at, catch_up="skip"):
    step = interval_ms(every_sec)
    nxt = prev_next_at + step
    if catch_up == "all":
        return True, nxt                    # 连补
    if at - prev_next_at >= step * 1.5:
        nxt = at + step                     # 真错过了：补一次，节拍从此刻重算
    return True, nxt
```

两个细节都是长期跑才会暴露的：

- **`next_at + interval`，不是 `now + interval`**。后者把每次 tick 的检查延迟（最多 20s）也累加进去，一天偏 2 分钟，一年偏十几小时。实测 24 小时模拟 2160 次触发，与理论值一条不差。
- **落后超过 1.5 个周期就重置**。阈值取 1.5 而不是 1：正常 tick 延迟（<50%）不该被当成停机，否则每个周期都会重算一次，反而引入抖动。

### 6.4 catch_up：默认 skip 是安全的

因为 intake 幂等。`idx_run_live` 保证同一 `(kind, subject_key)` 只有一张在途 run，所以调度层重复触发 100 次，也只会得到一张。

这是两层之间最重要的契约：**run 层幂等 → 调度层可以随便重复触发，不必自己记账「上次跑到哪」**。顺序不能倒，先有 run 层的唯一索引，才能有会 skip 的闹钟。

| 策略 | 语义 | 适合 |
|---|---|---|
| `skip`（默认） | 错过就跳过，立刻进入下一周期 | 仓库轮询：停机两天，补拉一次就够 |
| `once` | 错过的合成一次，立刻跑一回 | 日报/复盘：停机一天，当天补一份 |
| `all` | 每个错过的周期都补 | 几乎不该用，会瞬间灌满队列 |

### 6.5 三个循环分开，互不阻塞

| 循环 | 周期 | 职责 | 失败影响 |
|---|---|---|---|
| `tick` | 20s | 扫 schedule → intake 成 run，只写库 | 下一轮自动补上 |
| `runner` | 1s | claim run → 执行（全局槽位 3） | run 停在 queued，下轮再领 |
| `reaper` | 60s | `running` 且 `locked_at` 超时 → 重排队；超 `max_attempt` → `waiting_human` | 孤儿 run 多留一分钟 |

现在是 `_loop` 一个循环干所有事，`create_task` 抛出去就不管了。分开之后，任何一环卡住不影响其他两环。`reaper` 就是「看门狗」的正确形态：一条 `SELECT` + 一条 `UPDATE`，不用第二个模型。

### 6.6 「不间断」的真相：代码只管接得上

没有任何代码能让一个 Windows 上的 Python 进程 7×24 不断。必须分清谁负责什么：

| 层次 | 谁负责 | 做法 |
|---|---|---|
| 进程存活 | 操作系统 | 注册成 Windows 服务（NSSM）或任务计划程序「开机启动 + 失败自动重启」。现在的 `start_service.bat` 是一次性启动，崩了就没了 |
| 断点续跑 | 库 | `next_at` + `locked_at` + `attempt` |
| 不重复 | 库 | `idx_run_live` 幂等索引 |
| 不漂移 | 算法 | `next_at += interval` |

代码能保证的是「**断了之后接得上，且不重复**」。这一条做到，进程重启就只是「晚几分钟」，而不是「丢活」。剩下的交给 OS。

### 6.7 长期运行的四个维护点

| 问题 | 现象 | 对策 |
|---|---|---|
| `run` / `run_event` 无限增长 | 一年几十万行，`/api/runs` 越来越慢 | `cron:housekeeping` 每天凌晨删 30 天前的 `done`；`run_event` 跟着级联 |
| WAL 膨胀 | `data/*.db-wal` 只涨不缩（`db.py:407` 开了 WAL 但没有 checkpoint 策略） | 同上，每天 `PRAGMA wal_checkpoint(TRUNCATE)` |
| 写锁串行化 | runner 3 槽 + tick + reaper 共用一个连接 | 事务只包状态迁移，模型调用绝不在锁内（D5 已约定） |
| 时钟回拨 | 改系统时间 / 夏令时，`next_at` 落在过去 | 与「落后 2 周期」同一分支重置 |

### 6.8 落地与兼容

1. **迁移是单向读**：启动时把 `repo.interval_sec` / `repo.last_poll_at` 同步成 `schedule` 行（`id='repo.poll:'+repo_id`）。旧字段保留不删，随时能回退。
2. **`SCHED_ENABLED=0` 回落到现在的 `ReviewPoller._loop`**，两套并存一个版本。
3. **第一版只把「拉 PR」变成 intake**，审核执行仍走 `poll_repo` 老路径（与 P2 的 shadow 同一步）。跑通再切 runner。
4. **手动审核和 tick 产出同一种 run**，靠 `run.source` 区分（`tick` / `manual`），看板上能分清「谁触发的」。
5. **同仓库串行仍靠 claim 的 `NOT EXISTS`**，不在 tick 里限制。tick 可以同时给一个仓库写 5 张 run，runner 一个一个跑——把「防并发」集中在执行层，闹钟就不需要知道并发是什么。

### 6.9 顺带能挂上的几个定时任务

仓库轮询迁完之后，同一套 `schedule` 直接能挂：

| id | 周期 | 干什么 | 价值 |
|---|---|---|---|
| `repo.poll:<id>` | 仓库配置的 `interval_sec` | 拉 PR → intake 审核 | 已有，迁过来 |
| `cron:daily.review` | 每天 | 汇总当日 score card → 生成错题复习计划 | 面试业务真正的长期价值 |
| `cron:hourly.health` | 每小时 | 模型连通性、DB/WAL/磁盘大小 → 写 `health` | 挂了能自己发现 |
| `cron:daily.housekeeping` | 每天凌晨 | 归档旧 run、checkpoint WAL、清日志 | 见 6.7 |

后三个都是「到点生成一张 run」，跟审核走同一条 runner，不需要新机制。

### 6.10 已落地（本轮实现）

| 文件 | 职责 |
|---|---|
| `store/db.py` | 三张表：`schedule_group` / `schedule` / `tick_log`（全部 `IF NOT EXISTS`，老库启动自动补建） |
| `store/schedule.py` | 纯 SQL：读写、CAS 推进、批量配置、记录回填、统计 |
| `jobs/schedule.py` | 纯算法：`advance` / `jitter_offset` / `collect_due`，不碰连接 |
| `api/routes/schedule.py` | 六个接口，见下 |
| `services/poller.py` | `_loop` 改为调 `collect_due`；`poll_repo` 负责回填记录；`SCHED_ENABLED=0` 回退老判定 |
| `web/public/schedule.html` | 看板：分组、批量操作、逐次执行记录 |

接口：

```
GET    /api/schedule                  列表（含分组、上次结果、近 7 天统计）
GET    /api/schedule/ticks            全局最近记录
GET    /api/schedule/{id}/ticks       单项历史 + 统计
POST   /api/schedule/batch            批量改节奏 / 启停（同步回写 repo.interval_sec）
POST   /api/schedule/group            编组（多个仓库共享一个节拍器）
DELETE /api/schedule/group/{gid}      删组只解绑，不删成员
POST   /api/schedule/run              立即执行（走同一条记录链路，source=manual）
```

三处与上文设计不同、实现时改掉的地方：

1. **组的语义定为「节拍器」**，不是「并发的批量」。组到点，成员本轮全部触发；成员的 `next_at` 直接对齐到组的新节拍，不各自累加——否则组里几个仓库会越跑越散，最后又变回各跑各的。
2. **`catch_up` 三种值实际只有两档行为**：`skip` 和 `once` 都是「补一次然后对齐到 now」，因为一次 tick 只能触发一次；只有 `all` 会连续补。保留三个值是为了将来需要时不用改表，文档里要说清免得误以为三档都不同。
3. **`tick_log` 暂时兼任执行记录**：`run` 表还没建，所以执行结果（拉到几个 PR、审了几个、耗时、错误）回填在同一行。run 层落地后 `tick_log.run_id` 指向 run，本表退回「触发决策」职责，不用迁移数据。

验收已覆盖（`tests/test_schedule.py`，41 条）：24 小时零漂移（2160 次触发与理论值完全一致）、停机 2 小时只补一次、CAS 防重复触发、组一次触发全成员且不重复、停用成员记 `skipped`、批量配置、记录回填与统计。

### 6.11 看板要回答的三个问题

「看到执行记录」不是把 `tick_log` 直接铺成一张表就算完。用户点开这个页面时真正想知道的是三件不同的事，对应三种信息密度：

| 问题 | 放哪 | 字段 |
|---|---|---|
| **整体现在还正常吗** | 顶部六个数字 | 今日触发 / 成功 / 失败 / 跳过 / 执行中 / 平均耗时 |
| **这一次到底干了什么** | 主区域流水（默认展开可见） | 时间、仓库、定时还是手动、结果、耗时、一句话结果摘要 |
| **为什么失败** | 点开一行 | 计划时刻与实际触发时刻、延迟、起止时间、错误列表、原始 result JSON |

三个设计取舍：

1. **流水放主区域，不塞抽屉**。抽屉是「按仓库看历史」的入口，用它承载「最近发生了什么」等于让用户多点一次才看得到答案。抽屉保留，职责改成单仓库纵深。
2. **摘要一句话、详情才铺开**。「发现 3 个 PR · 审核 2 个」比一整段 JSON 有用；原始 JSON 折叠在 `<details>` 里，排障时才展开。
3. **跳过的记录也要显示**。停用的仓库每轮留一条 `skipped`，看起来是噪音，但「为什么今天没跑」的答案就在这里面——不记，这个问题永远查不到。

状态区分三个层次，不能混成一个「启用/停用」：`enabled`（配置是否生效）、`polling`（此刻是否正在跑）、`lastOutcome`（上一次的结果）。

入口挂在左侧栏「专家」下方（`ConvoSidebar` 里与「专家中心」并列的第二张卡片），点开在**主区域内**展示，不开新标签页——整页跳走会中断正在进行的对话生成，这跟 P1 要解决的是同一个问题。入口上带今日触发次数，有失败时亮红点。

视图用 `iframe` 内嵌 `schedule.html?embed=1`，不做成独立 React 组件：

- 那页有 850 多行（统计块、流水、筛选、展开详情、倒计时、批量操作、分组、抽屉），重写一遍等于同一套逻辑维护两份，改一处忘一处就出现「独立页面正常、嵌进来看不见结果」。
- 内嵌让「单独打开」和「嵌进来看」共用同一份代码。`?embed=1` 只做两件事：隐藏自己的 `<h1>`、把顶栏压成一条工具栏，其余完全一致。
- 同源 iframe 直接读 `localStorage.theme` 就能跟随主题；再加 `storage` 事件监听，主界面切深浅色时内层秒跟。

**注意**：`server.js` 对 `/api/*` 是逐条白名单转发，新增 Python 路由必须在这里补一条，否则页面能打开但接口 404。

**注意**：`web/public/` 下的文件由 Vite 构建自动拷进 `web/dist/`，不需要手工 `cp`；手工拷过反而容易忘，改完 `public/` 不重新构建就看的是旧文件。

#### 6.11.1 三类「看起来像 bug」的记录

端到端跑真实仓库时，看板上先后出现过三种让人误判的记录。它们的共同点是：**库里没有 bug，展示没把话说全**。

| 记录的样子 | 实际情况 | 处理 |
|---|---|---|
| `成功` 但没有任何结果、没有结束时间 | 调度层已写 `fired`，执行时命中 `_inflight` 复用了上一轮 Task，直接 `return` 导致那条记录永远等不到 `finish_tick` | `poll_repo()` 复用分支补写 `skipped` + 复用原因（`tests/test_schedule.py` 4 条断言） |
| `失败` 但耗时是 `—`、原因写「执行被中断」 | 上一个进程被杀，`finish_tick` 没机会执行，留下 `fired` + `finished_at IS NULL` 的僵尸记录 | 启动时 `sweep_stale_ticks(0)` 无条件收殓（那一刻上个进程已死，不可能还有任务在飞） |
| `成功`、`发现 1 个 PR`、`审核 0 个` | PR 已按 `headSha` 审过，幂等跳过，本轮本来就无事可做 | `_poll_repo_inner` 返回 `pending` 与 `note`，摘要显示「1 个开放 PR 都已审过（提交未变），本轮无需重审」 |

第三条最值得记：**「0」本身没有方向**。同一个 `reviewed: 0`，可能是「已经审过了」，也可能是「模型配置丢了」，还可能是「单轮上限挡住了」。不把区分写进结果，用户只能靠猜——而猜测的结果通常是「定时任务坏了」，然后去重启服务，问题反而被掩盖。记录里必须带上**为什么是 0**。

---

## 7. 明确不抄的部分

| 机制 | 不抄的理由 |
|---|---|
| 存算分离（思考在云、执行在本地） | 单机服务；为了「关机也在线」要多一台云主机，是运维问题不是架构问题 |
| Watchdog 独立 Agent 巡检 | 用 `locked_at` 超时 + `attempt` 计数就够，由 `reaper` 循环承担（6.5）；第二个 LLM 只会引入新的不确定性 |
| 批处理合并消息 | 单用户、人打字级别的 QPS，过度设计 |
| 轻量 Router 模型 | 现有关键词路由（`plugins/interview/intent.py`）零成本、可单测；Router 的本质是「有状态的收件箱」，不是「小模型」 |
| 主-子 Agent 拆解 | 现有 stage（planner/interviewer/grader）是**任务内阶段**，不该和**任务间调度**混为一谈；`contract.py` 已写明这条边界 |

---

## 8. 风险与约束

| 风险 | 应对 |
|---|---|
| 多进程抢工单 | 保持 `uvicorn.run` 单进程（现状如此）；`locked_by + locked_at` 只做崩溃恢复 |
| 闹钟与执行同进程，都是单点 | 不追求进程内高可用；靠 `next_at` + 幂等索引保证重启不丢活不重复，存活交给 OS（见 6.6） |
| 调度层自己出错导致永不触发 | `tick` 每轮都重新扫表，不依赖上一轮的返回值；`fail_streak` + `last_error` 上健康看板 |
| SQLite 写竞争 | 沿用现有「单连接 + `threading.RLock`」；事务只包状态迁移，不包模型调用 |
| 内存 run 表随进程消失 | 状态与正文已落库；启动时把孤儿 `running` 归位 |
| 改造动到审核主链路 | P2 先上 shadow（工单照建、执行仍走旧路径），观察一周再切真执行 |
| 前端 CopilotKit 单例串场 | 已有 `staleMsgIdsRef` / `freshConvoRef` 兜底；P1 后由 run 归属取代，不再依赖消息 id 猜测 |

---

## 9. 总验收

1. 切会话、关页面、刷新浏览器，三个动作下回复都不丢（P1）
2. 进程在审核中途被 `kill -9`，重启后工单自动重跑，且**不重复回帖**（P2 + P4）
3. 10 个仓库同时有 PR，并发模型调用恒 ≤3，同仓库串行（P2）
4. 点「审核这个 PR」，1 秒内看到侦察官亮起；key 无效在 1 秒内报错（P3）
5. 回帖失败按 30s→60s→120s 重试 3 次，仍失败转 `waiting_human`（P4）
6. 服务停机 2 小时后启动：每个仓库**只补跑 1 次**（不是 360 次），且不重复回帖（P2 调度层）
7. 连续跑 24 小时后，`next_at` 相对配置周期无累积漂移；`*-wal` 文件不持续增长（P2 调度层）
8. 所有开关关闭时，行为与改动前完全一致（各期都有 flag）

---

## 附录 A：为什么值得做

改动集中在两个新增模块（`services/chat_runs.py`、`jobs/`），现有插件契约、stage 编排、SSE 端点都不动。做完之后：

- 用户视角：不再需要"守着屏幕等它说完"
- 开发视角：排查问题从"翻日志猜"变成"查一行记录"
- 演进视角：批量出题、整场复盘重跑这类后台任务，接进来只需要加一个 `kind`

---

## 附录 B：这套东西只服务代码审核吗

不是。但要承认一个事实：本文的举例和验收几乎全在审核线上，因为审核是当前**唯一同时具备「长任务 + 定时触发 + 外部副作用」**的现成样本，用它做压力测试最省事。它是样本，不是目标。

### B1 抽象落在哪三个字段

`run` 表里没有「仓库」「PR」「diff」任何一个字，全部落在 `payload_json`。留下的是三个纯形状字段：

| 字段 | 是什么 | 谁定义 |
|---|---|---|
| `kind` | 这类活叫什么 | 业务注册（`review.pr` / `chat.turn` / `interview.batch`） |
| `scope` | 谁跟谁不能同时跑 | 业务决定（`repo_id` / `convo_id` / `batch_id`） |
| `subject_key` | 怎么算「同一件事」 | 业务决定（`owner/name#123@sha` / 空） |

**run 层定义约束的形状，业务定义约束的内容。** 换业务时这三个字段的值变了，表结构不变。

### B2 判据：什么该进 run，什么不该

不要「能进就进」。满足 2 条以上才进：

| # | 条件 | 说明 |
|---|---|---|
| 1 | 单次耗时 > 30s | 超过用户盯着屏幕的耐心 |
| 2 | 有外部副作用 | 回帖、推送、写外部系统——失败必须重试 |
| 3 | 需要跨请求存活 | 关页面 / 切会话 / 进程重启后还得继续 |
| 4 | 需要被定时触发 | 由 schedule 生产 |
| 5 | 失败原因需要事后追溯 | 用户来问「昨天那个怎么没了」 |

一条都不满足的，留在请求内执行，别引入工单。**工单有成本**：多一次写库、多一层排查路径、多一个「为什么它没跑」的问题。

### B3 把面试业务逐项套一遍

| 任务 | 耗时 | 跨请求 | 副作用 | 定时 | 进 run？ |
|---|---|---|---|---|---|
| 聊天单轮 | 10–60s | ✅ 切会话就断 | — | — | **✅ P1 已覆盖** |
| 出题（单题） | 10–30s | 用户在场等 | — | — | ❌ 留在请求内，SSE 够用 |
| 整场复盘重跑 | 1–3min | ✅ | 可选推送 | 可每日 | ✅ |
| **批量出题（20 题）** | 5–15min | ✅ 必须 | — | 可选 | **✅ 最该进的一个** |
| 简历深挖 | 30s–2min | ✅ | — | — | ⚠️ 看规模，超 1min 就进 |
| 代码审核 | ~10min | ✅ | 回帖 | ✅ 轮询 | ✅ 五条全中 |
| 每日错题复习 | — | ✅ | 推送 | ✅ | ✅ |

**结论：面试业务里最该后台化的是「批量出题」，不是审核。** 审核只是碰巧先有了代码。20 道题生成 10 分钟，用户不可能盯着——这跟「审核一个 PR」是同一类问题，用同一张表。

### B4 三种 scope，同一张表

这是通用性的真正考验：三个业务的并发约束语义完全不同。

| kind | scope | 串行的理由 |
|---|---|---|
| `review.pr` | `repo_id` | 同一仓库抢 token、回帖交错 |
| `chat.turn` | `convo_id` | 同一会话两条流并发 → 整场覆盖互相踩 |
| `interview.batch` | `batch_id` | 同一批次内保持题目风格连贯 |

run 层不关心 scope 是仓库还是会话，只要求它是**一个字符串，相同的不能同时 running**。claim SQL 里那句 `NOT EXISTS (... x.scope = r.scope)` 对三者一视同仁。

**顺带修一个现存 bug**：`chat_completions`（`chat.py:61`）不检查该会话是否已在生成，两个浏览器标签页同时发消息会并发写同一个 `convo`，而 `save_convo` 是整场覆盖——两个快照互相覆盖。前端 `isLoading` 只挡同标签页。`scope=convo_id` 顺手就治好了，不需要额外代码。

### B5 过度抽象的红线

通用不等于万能。如果为了「什么都能装」把 `run` 表做成塞满字段的万能表，结果是**什么都装得下、什么都不好用**。边界：

| 放 run 层（形状） | 留插件（内容） |
|---|---|
| 状态机 queued→running→done | 每种 kind 的 stage 名单 |
| 重试 / 退避 / max_attempt | 什么错误算可重试 |
| 全局槽位 + 同 scope 串行 | 幂等键怎么算 |
| 事件流 + 崩溃恢复 | payload/result 的 schema |

判据一句话：**两件事在「失败怎么办」上答案相同，就该共用一层。** 审核失败要重试两次然后等人，批量出题失败也要重试然后等人——同一个形状，所以共用；至于看的是 diff 还是 JD，那是插件的事。

### B6 别提前论证，用 P1 验证

通用性不该靠说服，该靠最便宜的那一步验证：

- **P1（聊天托管）就用这张 run 表**，不另建 chat 专属表。
- 如果 P1 做下来发现必须给 run 加一堆 chat 专属字段 → 说明抽象错了，此时还没动审核，改表成本几乎为零。
- 如果 P1 干净跑通 → 抽象成立，P2 的审核只是换一个 `kind` 和 `scope` 的值。

**P1 是这次抽象的验证用例，成本最低，也最早暴露问题。** 这也是分期顺序把聊天排在审核前面的真正理由——不只是「收益快」，更是「先证伪」。
