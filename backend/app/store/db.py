# -*- coding: utf-8 -*-
"""持久层 · 连接与建表（标准库 sqlite3，零第三方依赖）

数据落在 <项目根>/data/interview.db：
  - 备份=复制文件，迁移=拷走；DB_PATH 环境变量可覆盖
  - WAL 模式：读写并发友好，崩溃后可恢复
  - 十张表：expert / agent（专家、智能体，会话 → 智能体 → 专家 两级关联）
            convo / msg / msg_fts（会话、消息、全文索引）
            profile_memory（候选人画像，预留）
            repo / pr / review（代码审核：仓库配置、PR 记录、审核报告）
            setting（进程级配置键值，目前存模型配置，重启后自动恢复）
"""

import json
import os
import sqlite3
import threading
import time

from ..config import DATA_DIR

DEFAULT_USER = "local"
MAX_MSGS = 400
MAX_CONVOS = 200

DB_PATH = os.environ.get("DB_PATH") or os.path.join(str(DATA_DIR), "interview.db")

SCHEMA = """
/* ---------- 专家 / 智能体：会话 → 智能体 → 专家 ----------
   专家（expert）= 对外的能力包：面试出题官 / 代码审核官
   智能体（agent）= 真正干活的流水线角色：策划官/面试官/评分官、侦察官/审核官/裁决官…
   一个专家下挂多个智能体，其中 is_primary=1 的是该专家的主智能体（会话默认绑定它）。 */
CREATE TABLE IF NOT EXISTS expert (
  id         TEXT PRIMARY KEY,
  mode       TEXT NOT NULL,                -- 路由键：interview / review（= 原 convo.agent_mode）
  name       TEXT NOT NULL,
  icon       TEXT,
  role       TEXT,
  desc       TEXT,
  tags_json  TEXT,
  cta        TEXT,
  sort       INTEGER NOT NULL DEFAULT 0,
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent (
  id         TEXT PRIMARY KEY,
  expert_id  TEXT NOT NULL REFERENCES expert(id) ON DELETE CASCADE,
  stage      TEXT NOT NULL,                -- 流水线环节键：planner / interviewer / grader / scout / reviewer / judge / advisor
  name       TEXT NOT NULL,                -- 策划官 / 面试官 / 评分官 …
  icon       TEXT,
  role       TEXT,                         -- 一句话职责
  is_primary INTEGER NOT NULL DEFAULT 0,    -- 会话默认绑定的主智能体
  sort       INTEGER NOT NULL DEFAULT 0,
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_expert ON agent(expert_id, sort);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_primary ON agent(expert_id) WHERE is_primary = 1;

CREATE TABLE IF NOT EXISTS convo (
  id           TEXT PRIMARY KEY,
  user_key     TEXT NOT NULL DEFAULT 'local',
  title        TEXT NOT NULL DEFAULT '新会话',
  profile_json TEXT,
  agent_id     TEXT,                       -- 会话绑定的智能体（NULL = 未关联）
  agent_mode   TEXT,                       -- 冗余：由 agent → expert 派生，供老接口/路由使用
  repo_id      TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_convo_user ON convo(user_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS msg (
  id         TEXT NOT NULL,
  convo_id   TEXT NOT NULL REFERENCES convo(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_convo ON msg(convo_id, seq);
/* 只在同一会话内要求消息 id 唯一：
   整场覆盖保存时，同一条消息可能（历史上因切换会话串场）出现在两个会话里，
   若全局唯一，后保存的那个会话会直接 500（UNIQUE constraint failed: msg.id） */
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_id ON msg(convo_id, id);

CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(content, tokenize = 'trigram');

CREATE TRIGGER IF NOT EXISTS msg_ai AFTER INSERT ON msg BEGIN
  INSERT INTO msg_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS msg_ad AFTER DELETE ON msg BEGIN
  DELETE FROM msg_fts WHERE rowid = old.rowid;
END;

CREATE TABLE IF NOT EXISTS profile_memory (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_key     TEXT NOT NULL DEFAULT 'local',
  kind         TEXT NOT NULL,
  topic        TEXT NOT NULL,
  content      TEXT NOT NULL,
  confidence   REAL NOT NULL DEFAULT 0.6,
  source_convo TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_user ON profile_memory(user_key, kind, topic);

/* ---------- 代码审核：仓库配置 / PR 记录 / 审核报告 ---------- */
CREATE TABLE IF NOT EXISTS repo (
  id           TEXT PRIMARY KEY,
  user_key     TEXT NOT NULL DEFAULT 'local',
  url          TEXT NOT NULL,
  provider     TEXT NOT NULL DEFAULT 'github',
  host         TEXT,
  owner        TEXT NOT NULL,
  name         TEXT NOT NULL,
  token        TEXT,
  interval_sec INTEGER NOT NULL DEFAULT 120,
  auto_post    INTEGER NOT NULL DEFAULT 0,
  enabled      INTEGER NOT NULL DEFAULT 1,
  last_poll_at INTEGER,
  last_error   TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_repo_url ON repo(user_key, url);

CREATE TABLE IF NOT EXISTS pr (
  repo_id       TEXT NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
  number        INTEGER NOT NULL,
  title         TEXT,
  author        TEXT,
  url           TEXT,
  base_branch   TEXT,
  head_branch   TEXT,
  head_sha      TEXT NOT NULL DEFAULT '',
  state         TEXT NOT NULL DEFAULT 'open',
  draft         INTEGER NOT NULL DEFAULT 0,
  pr_updated_at INTEGER,
  seen_at       INTEGER,
  PRIMARY KEY (repo_id, number)
);
CREATE INDEX IF NOT EXISTS idx_pr_repo ON pr(repo_id, pr_updated_at DESC);

CREATE TABLE IF NOT EXISTS review (
  id          TEXT PRIMARY KEY,
  repo_id     TEXT NOT NULL REFERENCES repo(id) ON DELETE CASCADE,
  pr_number   INTEGER NOT NULL,
  head_sha    TEXT NOT NULL DEFAULT '',
  risk        TEXT,
  decision    TEXT,
  summary     TEXT,
  report_md   TEXT,
  issues_json TEXT,
  issue_count INTEGER NOT NULL DEFAULT 0,
  stages_json TEXT,
  model       TEXT,
  posted      INTEGER NOT NULL DEFAULT 0,
  post_error  TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_review_uniq ON review(repo_id, pr_number, head_sha);
CREATE INDEX IF NOT EXISTS idx_review_repo ON review(repo_id, created_at DESC);

/* ---------- 进程级设置：键值对 ----------
   只放「重启后还要在」的少量配置。value 统一存 JSON 字符串，
   目前只有 model_cfg（模型 base / key / model）——轮询器靠它审核，
   不能只活在内存里，否则每次重启都会退化成「只轮询不审核」。 */
CREATE TABLE IF NOT EXISTS setting (
  key        TEXT PRIMARY KEY,
  value      TEXT,
  updated_at INTEGER NOT NULL
);
"""

# ------------------------------------------------------------------ 内置专家 / 智能体
# 下面是**兜底出厂配置**：正常路径由 `_sync_catalog()` 从插件清单
# （backend/plugins/*/plugin.yaml）同步出 expert / agent 两张表。
# 这里只在插件目录整个读不到时用（老部署 / PLUGINS_ENABLED=0 / 单测没装插件）。
SEED_EXPERTS = [
    # (id, mode, name, icon, role, desc, tags, cta, sort)
    ("interviewer", "interview", "面试出题官", "面", "AI 技术面试官",
     "按技术方向 / 级别 / 经验 / 行业背景 / 简历智能出题，支持追问与结构化评分复盘。",
     ["智能出题", "简历深挖", "评分复盘"], "开始出题", 1),
    ("reviewer", "review", "代码审核官", "审", "AI Code Reviewer",
     "轮询仓库开放 PR，按正确性 / 安全 / 并发性能 / 可维护性 / 测试五维审核，给出风险等级与合入结论。",
     ["PR 审核", "风险定级", "修复建议"], "开始审核", 2),
]

SEED_AGENTS = [
    # (id, expert_id, stage, name, icon, role, is_primary, sort)
    ("interviewer.planner", "interviewer", "planner", "策划官", "谋",
     "首次出题前规划考察大纲", 0, 1),
    ("interviewer.interviewer", "interviewer", "interviewer", "面试官", "面",
     "出题 / 追问 / 讲解（主智能体）", 1, 2),
    ("interviewer.grader", "interviewer", "grader", "评分官", "评",
     "对候选人回答做结构化评分与复盘", 0, 3),
    ("reviewer.scout", "reviewer", "scout", "侦察官", "侦",
     "拉取 PR、解析 diff 与上下文", 0, 1),
    ("reviewer.reviewer", "reviewer", "reviewer", "审核官", "审",
     "五维审核、定风险等级与合入结论（主智能体）", 1, 2),
    ("reviewer.judge", "reviewer", "judge", "裁决官", "裁",
     "汇总各维度结论并给出最终裁决", 0, 3),
    ("reviewer.advisor", "reviewer", "advisor", "审核答疑", "答",
     "基于审核报告回答追问", 0, 4),
]

lock = threading.RLock()
_conn = None
_available = True
_unavailable_reason = ""


def now() -> int:
    return int(time.time() * 1000)


def _ensure_convo_columns(conn: sqlite3.Connection) -> None:
    """老库补列：会话与专家/智能体绑定所需的 agent_id / agent_mode / repo_id（幂等）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(convo)").fetchall()}
    if "agent_id" not in cols:
        conn.execute("ALTER TABLE convo ADD COLUMN agent_id TEXT")
    if "agent_mode" not in cols:
        conn.execute("ALTER TABLE convo ADD COLUMN agent_mode TEXT")
    if "repo_id" not in cols:
        conn.execute("ALTER TABLE convo ADD COLUMN repo_id TEXT")


def _seed_catalog(conn: sqlite3.Connection) -> None:
    """插件清单读不到时的兜底：灌入内置专家与智能体（幂等，不覆盖已有记录）。"""
    ts = now()
    for (eid, mode, name, icon, role, desc, tags, cta, sort) in SEED_EXPERTS:
        conn.execute(
            """INSERT OR IGNORE INTO expert
                 (id, mode, name, icon, role, desc, tags_json, cta, sort, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (eid, mode, name, icon, role, desc,
             json.dumps(tags, ensure_ascii=False), cta, sort, ts, ts),
        )
    for (aid, eid, stage, name, icon, role, primary, sort) in SEED_AGENTS:
        conn.execute(
            """INSERT OR IGNORE INTO agent
                 (id, expert_id, stage, name, icon, role, is_primary, sort, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (aid, eid, stage, name, icon, role, primary, sort, ts, ts),
        )


def _plugin_catalog() -> dict | None:
    """从插件清单收集 expert / agent 两份记录；一个插件都读不到时返回 None"""
    try:
        from ..agents import registry
        plugins = registry.all_plugins()
    except Exception:
        return None
    experts = [p.expert_record() for p in plugins]
    agents = [a for p in plugins for a in p.agent_records()]
    if not experts:
        return None
    return {"experts": experts, "agents": agents}


def _sync_catalog(conn: sqlite3.Connection) -> None:
    """把 expert / agent 同步成**插件清单的投影**（幂等，每次启动跑一次）

    覆盖规则分两类，不是一刀切：

      - **结构字段**（mode / expert_id / stage / is_primary / sort / enabled）
        每次启动以插件为准。加一个角色只加 plugin.yaml，库里自动出现。
      - **文案字段**（name / icon / role / desc / tags / cta）只在首次建档时写入，
        之后**以库为准**。延续项目原本的工作流：改文案直接改库，重启不覆盖；
        想让 yaml 的文案生效，把库里那行删掉即可。

    插件里已经不存在的专家 / 角色 → `enabled = 0` 软停用，不删行：历史会话的
    convo.agent_id 还指得到，`/api/experts` 不再列出，避免「前端能点、后端没有
    这个 mode」的 500。

    整个同步包在 SAVEPOINT 里：yaml 写错（比如两个 primary）时回滚到兜底出厂
    配置并记日志，不能让一个清单错误把整个服务拖成不可用。
    """
    from ..core.log import log

    catalog = _plugin_catalog()
    if not catalog:
        log("插件清单读不到，专家表沿用库里的数据（兜底灌入内置配置）")
        _seed_catalog(conn)
        return

    ts = now()
    expert_ids = [e["id"] for e in catalog["experts"]]
    agent_ids = [a["id"] for a in catalog["agents"]]

    conn.execute("SAVEPOINT sync_catalog")
    try:
        # 主智能体是部分唯一索引（WHERE is_primary = 1），先全部清零再按清单设，
        # 否则「换个主智能体」会在 UPDATE 中途撞唯一约束。
        conn.executemany(
            "UPDATE agent SET is_primary = 0 WHERE expert_id = ?",
            [(eid,) for eid in expert_ids],
        )

        for e in catalog["experts"]:
            conn.execute(
                """INSERT INTO expert
                     (id, mode, name, icon, role, desc, tags_json, cta, sort,
                      enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     mode       = excluded.mode,
                     sort       = excluded.sort,
                     enabled    = 1,
                     updated_at = excluded.updated_at""",
                (e["id"], e["mode"], e["name"], e["icon"], e["role"], e["desc"],
                 json.dumps(e["tags"], ensure_ascii=False), e["cta"], e["sort"], ts, ts),
            )

        for a in catalog["agents"]:
            conn.execute(
                """INSERT INTO agent
                     (id, expert_id, stage, name, icon, role, is_primary, sort,
                      enabled, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     expert_id  = excluded.expert_id,
                     stage      = excluded.stage,
                     is_primary = excluded.is_primary,
                     sort       = excluded.sort,
                     enabled    = 1,
                     updated_at = excluded.updated_at""",
                (a["id"], a["expert_id"], a["stage"], a["name"], a["icon"], a["role"],
                 1 if a["primary"] else 0, a["sort"], ts, ts),
            )

        # 插件里已经没有的 → 软停用（保留行，历史会话仍能解析出名字）
        conn.execute(
            "UPDATE expert SET enabled = 0, updated_at = ? WHERE id NOT IN ("
            + ",".join("?" * len(expert_ids)) + ")",
            (ts, *expert_ids),
        )
        conn.execute(
            "UPDATE agent SET enabled = 0, updated_at = ? WHERE id NOT IN ("
            + ",".join("?" * len(agent_ids)) + ")",
            (ts, *agent_ids),
        )
        conn.execute("RELEASE SAVEPOINT sync_catalog")
    except Exception as e:
        conn.execute("ROLLBACK TO SAVEPOINT sync_catalog")
        conn.execute("RELEASE SAVEPOINT sync_catalog")
        log(f"专家表同步失败（{type(e).__name__}: {e}），回落到内置出厂配置")
        _seed_catalog(conn)


def _backfill_agent_links(conn: sqlite3.Connection) -> None:
    """把老会话的 agent_mode 与新会话的 agent_id 对齐（幂等）。

    - 老记录只有 agent_mode → 补上该专家的主智能体
    - 只写了 agent_id 的记录 → 由 agent → expert 派生出 agent_mode
    两边都为空（未关联专家的会话）则保持为空。
    """
    conn.execute(
        """UPDATE convo SET agent_id = (
               SELECT a.id FROM agent a JOIN expert e ON e.id = a.expert_id
                WHERE e.mode = convo.agent_mode AND a.is_primary = 1 LIMIT 1)
            WHERE (agent_id IS NULL OR agent_id = '')
              AND agent_mode IS NOT NULL AND agent_mode <> ''"""
    )
    conn.execute(
        """UPDATE convo SET agent_mode = (
               SELECT e.mode FROM agent a JOIN expert e ON e.id = a.expert_id
                WHERE a.id = convo.agent_id LIMIT 1)
            WHERE (agent_mode IS NULL OR agent_mode = '')
              AND agent_id IS NOT NULL AND agent_id <> ''"""
    )


def _ensure_msg_index(conn: sqlite3.Connection) -> None:
    """老库里 idx_msg_id 是「全表唯一」，改成「同一会话内唯一」（幂等）。"""
    for r in conn.execute("PRAGMA index_list(msg)").fetchall():
        if r["name"] != "idx_msg_id":
            continue
        cols = [c["name"] for c in conn.execute("PRAGMA index_info('idx_msg_id')").fetchall()]
        if cols == ["id"]:
            conn.execute("DROP INDEX IF EXISTS idx_msg_id")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_id ON msg(convo_id, id)")
        return


def init() -> sqlite3.Connection:
    """建立（并缓存）连接；首次会建表并打开 WAL。"""
    global _conn, _available, _unavailable_reason
    if _conn is not None:
        return _conn
    with lock:
        if _conn is not None:
            return _conn
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.executescript(SCHEMA)
            _ensure_convo_columns(conn)
            _ensure_msg_index(conn)
            _sync_catalog(conn)
            _backfill_agent_links(conn)
            conn.commit()
            _conn = conn
            _available = True
        except Exception as e:  # 运行环境不支持时上层降级
            _available = False
            _unavailable_reason = str(e)
            raise
    return _conn


def available() -> bool:
    try:
        init()
        return _available
    except Exception:
        return False


def connected() -> bool:
    """连接是否已经建立，**不会触发建库**。

    给「库已就绪就用它、否则回退默认值」的调用方用：技能归属这类派生数据
    不应该为了读一次而主动拉起一个数据库文件。
    """
    return _conn is not None and _available


def unavailable_reason() -> str:
    return _unavailable_reason


def close():
    global _conn
    with lock:
        if _conn is None:
            return
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


def stats() -> dict:
    db = init()
    with lock:
        c = db.execute("SELECT COUNT(*) AS n FROM convo").fetchone()["n"]
        m = db.execute("SELECT COUNT(*) AS n FROM msg").fetchone()["n"]
        try:
            experts = db.execute("SELECT COUNT(*) AS n FROM expert").fetchone()["n"]
            agents = db.execute("SELECT COUNT(*) AS n FROM agent").fetchone()["n"]
            repos = db.execute("SELECT COUNT(*) AS n FROM repo").fetchone()["n"]
            reviews = db.execute("SELECT COUNT(*) AS n FROM review").fetchone()["n"]
        except Exception:
            experts = agents = repos = reviews = 0
    size_kb = 0
    try:
        size_kb = round(os.path.getsize(DB_PATH) / 1024)
    except Exception:
        pass
    return {"convos": c, "messages": m, "experts": experts, "agents": agents,
            "repos": repos, "reviews": reviews, "path": DB_PATH, "sizeKB": size_kb}
