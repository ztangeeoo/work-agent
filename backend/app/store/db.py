# -*- coding: utf-8 -*-
"""持久层 · 连接与建表（标准库 sqlite3，零第三方依赖）

数据落在 <项目根>/data/interview.db：
  - 备份=复制文件，迁移=拷走；DB_PATH 环境变量可覆盖
  - WAL 模式：读写并发友好，崩溃后可恢复
  - 七张表：convo / msg / msg_fts（会话、消息、全文索引）
            profile_memory（候选人画像，预留）
            repo / pr / review（代码审核：仓库配置、PR 记录、审核报告）
"""

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
CREATE TABLE IF NOT EXISTS convo (
  id           TEXT PRIMARY KEY,
  user_key     TEXT NOT NULL DEFAULT 'local',
  title        TEXT NOT NULL DEFAULT '新会话',
  profile_json TEXT,
  agent_mode   TEXT,
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_id ON msg(id);

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_review_uniq ON review(repo_id, pr_number, head_sha);
CREATE INDEX IF NOT EXISTS idx_review_repo ON review(repo_id, created_at DESC);
"""

lock = threading.RLock()
_conn = None
_available = True
_unavailable_reason = ""


def now() -> int:
    return int(time.time() * 1000)


def _ensure_convo_columns(conn: sqlite3.Connection) -> None:
    """老库补列：会话与专家绑定所需的 agent_mode / repo_id（幂等）。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(convo)").fetchall()}
    if "agent_mode" not in cols:
        conn.execute("ALTER TABLE convo ADD COLUMN agent_mode TEXT")
    if "repo_id" not in cols:
        conn.execute("ALTER TABLE convo ADD COLUMN repo_id TEXT")


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
            repos = db.execute("SELECT COUNT(*) AS n FROM repo").fetchone()["n"]
            reviews = db.execute("SELECT COUNT(*) AS n FROM review").fetchone()["n"]
        except Exception:
            repos = reviews = 0
    size_kb = 0
    try:
        size_kb = round(os.path.getsize(DB_PATH) / 1024)
    except Exception:
        pass
    return {"convos": c, "messages": m, "repos": repos, "reviews": reviews, "path": DB_PATH, "sizeKB": size_kb}
