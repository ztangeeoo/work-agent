# -*- coding: utf-8 -*-
"""
持久层：单文件 SQLite（标准库 sqlite3，零第三方依赖）

从 db.js 逐字段迁移而来，对外 JSON 契约（camelCase）与前端完全一致。

设计要点
  - 数据落在 data/interview.db，备份=复制文件，迁移=拷走
  - WAL 模式：读写并发友好，崩溃后可恢复
  - FTS5 + trigram 分词：中文全文检索（"缓存穿透"这种 3 字词可命中）
  - 所有写操作走事务，会话保存采用「整会话覆盖」策略（消息量小，行为可预测）
  - 七张表：
      convo / msg / msg_fts（会话、消息、全文索引）
      profile_memory（候选人画像，预留）
      repo / pr / review（代码审核：仓库配置、PR 记录、审核报告）
"""

import os
import json
import time
import uuid
import sqlite3
import threading

DEFAULT_USER = "local"
MAX_MSGS = 400
MAX_CONVOS = 200

ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH") or os.path.join(ROOT, "data", "interview.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS convo (
  id           TEXT PRIMARY KEY,
  user_key     TEXT NOT NULL DEFAULT 'local',
  title        TEXT NOT NULL DEFAULT '新会话',
  profile_json TEXT,
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

_lock = threading.RLock()
_conn = None
_available = True
_unavailable_reason = ""


def _now() -> int:
    return int(time.time() * 1000)


def init() -> sqlite3.Connection:
    """建立（并缓存）连接；首次会建表并打开 WAL。"""
    global _conn, _available, _unavailable_reason
    if _conn is not None:
        return _conn
    with _lock:
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
    with _lock:
        if _conn is None:
            return
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


# ==================================================================
# 会话与消息
# ==================================================================

def _map_convo(r) -> dict:
    profile = None
    if r["profile_json"]:
        try:
            profile = json.loads(r["profile_json"])
        except Exception:
            profile = None
    return {
        "id": r["id"],
        "title": r["title"],
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
        "profile": profile,
    }


def list_convos(user: str = DEFAULT_USER, limit: int = MAX_CONVOS, with_messages: bool = True) -> list:
    """会话列表（默认带消息，前端切换会话无需二次请求）"""
    db = init()
    with _lock:
        rows = db.execute(
            """SELECT id, title, profile_json, created_at, updated_at
                 FROM convo WHERE user_key = ?
                ORDER BY updated_at DESC LIMIT ?""",
            (user, limit),
        ).fetchall()
        if not with_messages:
            return [_map_convo(r) for r in rows]

        out = []
        for r in rows:
            msgs = db.execute(
                "SELECT id, role, content FROM msg WHERE convo_id = ? ORDER BY seq ASC",
                (r["id"],),
            ).fetchall()
            out.append({
                **_map_convo(r),
                "messages": [{"id": m["id"], "role": m["role"], "content": m["content"]} for m in msgs],
            })
        return out


def get_convo(cid: str, user: str = DEFAULT_USER) -> dict | None:
    db = init()
    with _lock:
        r = db.execute(
            """SELECT id, title, profile_json, created_at, updated_at
                 FROM convo WHERE id = ? AND user_key = ?""",
            (cid, user),
        ).fetchone()
        if not r:
            return None
        msgs = db.execute(
            "SELECT id, role, content FROM msg WHERE convo_id = ? ORDER BY seq ASC",
            (cid,),
        ).fetchall()
    return {
        **_map_convo(r),
        "messages": [{"id": m["id"], "role": m["role"], "content": m["content"]} for m in msgs],
    }


def save_convo(data: dict, user: str = DEFAULT_USER) -> dict:
    """保存整场会话（覆盖式，事务内完成）"""
    cid = str((data or {}).get("id") or "").strip()
    if not cid:
        raise ValueError("缺少会话 id")
    db = init()
    now = _now()
    msgs = (data.get("messages") if isinstance(data.get("messages"), list) else [])[-MAX_MSGS:]
    title = str(data.get("title") or "新会话")[:200]
    profile_json = json.dumps(data.get("profile"), ensure_ascii=False) if data.get("profile") else None
    created_at = int(data.get("createdAt") or now)
    updated_at = int(data.get("updatedAt") or now)

    with _lock:
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO convo (id, user_key, title, profile_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                        title        = excluded.title,
                        profile_json = excluded.profile_json,
                        updated_at   = excluded.updated_at""",
                (cid, user, title, profile_json, created_at, updated_at),
            )
            db.execute("DELETE FROM msg WHERE convo_id = ?", (cid,))
            for i, m in enumerate(msgs):
                db.execute(
                    "INSERT INTO msg (id, convo_id, seq, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        str(m.get("id") or f"{cid}_{i}"),
                        cid,
                        i,
                        "user" if m.get("role") == "user" else "assistant",
                        str("" if m.get("content") is None else m.get("content")),
                        created_at + i,
                    ),
                )
            # 上限保护：只保留最近 MAX_CONVOS 场，避免库无限增长
            db.execute(
                """DELETE FROM convo WHERE user_key = ? AND id NOT IN (
                     SELECT id FROM convo WHERE user_key = ? ORDER BY updated_at DESC LIMIT ?
                   )""",
                (user, user, MAX_CONVOS),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return {"id": cid, "count": len(msgs)}


def delete_convo(cid: str, user: str = DEFAULT_USER) -> bool:
    db = init()
    with _lock:
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM msg WHERE convo_id = ?", (cid,))
            cur = db.execute("DELETE FROM convo WHERE id = ? AND user_key = ?", (cid, user))
            db.execute("COMMIT")
            return cur.rowcount > 0
        except Exception:
            db.execute("ROLLBACK")
            raise


def search_messages(q: str, user: str = DEFAULT_USER, limit: int = 30) -> list:
    """
    跨会话全文检索。
    trigram 分词要求 >= 3 字符：单串或全为长词时走 FTS5，
    含短词（如「雪崩」「击穿」）时逐词 LIKE AND，兼顾中文短词召回。
    """
    db = init()
    query = str(q or "").strip()
    if not query:
        return []

    terms = [t for t in query.split() if t]
    like_terms = terms or [query]
    rows = []

    # 主路径：FTS5（所有词都 >= 3 字符）
    if terms and all(len(t) >= 3 for t in terms):
        expr = " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)
        try:
            rows = db.execute(
                """SELECT m.id, m.convo_id, m.role, m.content, m.created_at, c.title,
                          snippet(msg_fts, 0, char(2), char(3), '…', 30) AS snip
                     FROM msg_fts
                     JOIN msg   m ON m.rowid = msg_fts.rowid
                     JOIN convo c ON c.id = m.convo_id
                    WHERE msg_fts MATCH ? AND c.user_key = ?
                    ORDER BY m.created_at DESC LIMIT ?""",
                (expr, user, limit),
            ).fetchall()
        except Exception:
            rows = []

    # 回退：逐词 LIKE AND（短词、或 FTS 无命中）
    if not rows:
        conds = " AND ".join(["m.content LIKE ? ESCAPE '\\'"] * len(like_terms))
        params = ["%" + t.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%" for t in like_terms]
        rows = db.execute(
            f"""SELECT m.id, m.convo_id, m.role, m.content, m.created_at, c.title,
                       substr(m.content, 1, 140) AS snip
                  FROM msg m JOIN convo c ON c.id = m.convo_id
                 WHERE c.user_key = ? AND {conds}
                 ORDER BY m.created_at DESC LIMIT ?""",
            (user, *params, limit),
        ).fetchall()

    return [
        {
            "id": r["id"],
            "convoId": r["convo_id"],
            "convoTitle": r["title"],
            "role": r["role"],
            # snippet 里的 \x02/\x03 是命中词边界标记，前端按需渲染高亮
            "snippet": str(r["snip"] or "").replace("\x02", "").replace("\x03", ""),
            "createdAt": r["created_at"],
        }
        for r in rows
    ]


# ==================================================================
# 代码审核：仓库 / PR / 审核报告
# ==================================================================

def _map_repo(r, with_token: bool = False) -> dict | None:
    if not r:
        return None
    out = {
        "id": r["id"],
        "url": r["url"],
        "provider": r["provider"],
        "host": r["host"] or "",
        "owner": r["owner"],
        "name": r["name"],
        "intervalSec": r["interval_sec"],
        "autoPost": bool(r["auto_post"]),
        "enabled": bool(r["enabled"]),
        "lastPollAt": r["last_poll_at"] or 0,
        "lastError": r["last_error"] or "",
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
        # token 一律不下发给前端，只告诉前端「有没有配」
        "hasToken": bool(r["token"]),
    }
    if with_token:
        out["token"] = r["token"] or ""
    keys = r.keys()
    if "open_prs" in keys:
        out["openPrs"] = r["open_prs"]
    if "review_count" in keys:
        out["reviewCount"] = r["review_count"]
    return out


def list_repos(user: str = DEFAULT_USER) -> list:
    """仓库列表（默认不含 token）"""
    db = init()
    with _lock:
        rows = db.execute(
            """SELECT r.*,
                      (SELECT COUNT(*) FROM pr p WHERE p.repo_id = r.id AND p.state = 'open') AS open_prs,
                      (SELECT COUNT(*) FROM review v WHERE v.repo_id = r.id) AS review_count
                 FROM repo r WHERE r.user_key = ? ORDER BY r.created_at ASC""",
            (user,),
        ).fetchall()
    return [_map_repo(r) for r in rows]


def get_repo(rid: str, user: str = DEFAULT_USER, with_token: bool = True) -> dict | None:
    db = init()
    with _lock:
        r = db.execute("SELECT * FROM repo WHERE id = ? AND user_key = ?", (str(rid or ""), user)).fetchone()
    return _map_repo(r, with_token) if r else None


def get_repo_by_id(rid: str, with_token: bool = True) -> dict | None:
    """轮询器用：按 id 取仓库，不限定用户"""
    db = init()
    with _lock:
        r = db.execute("SELECT * FROM repo WHERE id = ?", (str(rid or ""),)).fetchone()
    return _map_repo(r, with_token) if r else None


def list_all_repos() -> list:
    """轮询器用：所有仓库（含 token）"""
    db = init()
    with _lock:
        rows = db.execute("SELECT * FROM repo ORDER BY created_at ASC").fetchall()
    return [_map_repo(r, True) for r in rows]


def save_repo(data: dict, user: str = DEFAULT_USER) -> dict:
    """
    新建 / 更新仓库配置。
      - 同一用户同一地址只保留一条（重复添加等于改配置）
      - token 传 None 表示「不改」，传空串表示「清除」
    """
    db = init()
    now = _now()
    url = str(data.get("url") or "").strip()
    if not url:
        raise ValueError("缺少仓库地址")
    if not data.get("owner") or not data.get("name"):
        raise ValueError("仓库地址解析失败（需要形如 https://github.com/owner/repo）")

    rid = str(data.get("id") or "").strip()
    with _lock:
        if not rid:
            exist = db.execute("SELECT id FROM repo WHERE user_key = ? AND url = ?", (user, url)).fetchone()
            rid = exist["id"] if exist else "repo_" + uuid.uuid4().hex[:12]
        old = db.execute("SELECT token FROM repo WHERE id = ? AND user_key = ?", (rid, user)).fetchone()
        if (not old) and data.get("id"):
            raise ValueError("仓库不存在：" + rid)

        if data.get("token") is None:
            token = (old["token"] if old else "") or ""
        else:
            token = str(data.get("token") or "").strip()

        try:
            interval = min(max(int(data.get("intervalSec") or 120), 30), 3600)
        except Exception:
            interval = 120
        provider = "gitlab" if str(data.get("provider") or "github").lower() == "gitlab" else "github"
        enabled = 1 if data.get("enabled") is None else (1 if data.get("enabled") else 0)

        db.execute(
            """INSERT INTO repo (id, user_key, url, provider, host, owner, name, token,
                                 interval_sec, auto_post, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url, provider = excluded.provider, host = excluded.host,
                    owner = excluded.owner, name = excluded.name, token = excluded.token,
                    interval_sec = excluded.interval_sec, auto_post = excluded.auto_post,
                    enabled = excluded.enabled, updated_at = excluded.updated_at""",
            (
                rid, user, url, provider, str(data.get("host") or ""), str(data.get("owner")), str(data.get("name")),
                token, interval, 1 if data.get("autoPost") else 0, enabled, now, now,
            ),
        )
        db.commit()
    return get_repo(rid, user, with_token=False)


def delete_repo(rid: str, user: str = DEFAULT_USER) -> bool:
    db = init()
    with _lock:
        cur = db.execute("DELETE FROM repo WHERE id = ? AND user_key = ?", (str(rid or ""), user))
        db.commit()
        return cur.rowcount > 0


def touch_repo(rid: str, at: int | None = None, error: str = ""):
    """记录一次轮询结果（时间戳 / 错误信息）"""
    db = init()
    with _lock:
        db.execute(
            "UPDATE repo SET last_poll_at = ?, last_error = ? WHERE id = ?",
            (_now() if at is None else int(at), str(error or ""), str(rid)),
        )
        db.commit()


def upsert_pr(repo_id: str, pr: dict) -> dict | None:
    """写入 / 更新一条 PR（按 head_sha 判断是否有新提交）"""
    db = init()
    now = _now()
    with _lock:
        db.execute(
            """INSERT INTO pr (repo_id, number, title, author, url, base_branch, head_branch,
                               head_sha, state, draft, pr_updated_at, seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(repo_id, number) DO UPDATE SET
                    title = excluded.title, author = excluded.author, url = excluded.url,
                    base_branch = excluded.base_branch, head_branch = excluded.head_branch,
                    head_sha = excluded.head_sha, state = excluded.state, draft = excluded.draft,
                    pr_updated_at = excluded.pr_updated_at, seen_at = excluded.seen_at""",
            (
                str(repo_id), int(pr.get("number") or 0), str(pr.get("title") or ""), str(pr.get("author") or ""),
                str(pr.get("url") or ""), str(pr.get("baseBranch") or ""), str(pr.get("headBranch") or ""),
                str(pr.get("headSha") or ""), str(pr.get("state") or "open"), 1 if pr.get("draft") else 0,
                int(pr.get("updatedAt") or now), now,
            ),
        )
        db.commit()
    return get_pr(repo_id, pr.get("number"))


def list_prs(repo_id: str, limit: int = 20, include_closed: bool = False) -> list:
    """PR 列表：附带「当前 head 是否已审」的结论"""
    db = init()
    where = "" if include_closed else "AND p.state = 'open'"
    with _lock:
        rows = db.execute(
            f"""SELECT p.*, v.id AS review_id, v.risk AS review_risk, v.decision AS review_decision,
                       v.issue_count, v.created_at AS reviewed_at
                  FROM pr p
                  LEFT JOIN review v
                         ON v.repo_id = p.repo_id AND v.pr_number = p.number AND v.head_sha = p.head_sha
                 WHERE p.repo_id = ? {where}
                 ORDER BY p.pr_updated_at DESC LIMIT ?""",
            (str(repo_id), min(int(limit or 20), 100)),
        ).fetchall()
    return [
        {
            "repoId": r["repo_id"],
            "number": r["number"],
            "title": r["title"],
            "author": r["author"],
            "url": r["url"],
            "baseBranch": r["base_branch"],
            "headBranch": r["head_branch"],
            "headSha": r["head_sha"],
            "state": r["state"],
            "draft": bool(r["draft"]),
            "updatedAt": r["pr_updated_at"],
            "seenAt": r["seen_at"],
            "review": (
                {
                    "id": r["review_id"], "risk": r["review_risk"], "decision": r["review_decision"],
                    "issueCount": r["issue_count"], "createdAt": r["reviewed_at"],
                }
                if r["review_id"] else None
            ),
        }
        for r in rows
    ]


def get_pr(repo_id: str, number) -> dict | None:
    db = init()
    with _lock:
        r = db.execute(
            "SELECT * FROM pr WHERE repo_id = ? AND number = ?",
            (str(repo_id), int(number or 0)),
        ).fetchone()
    if not r:
        return None
    return {
        "repoId": r["repo_id"], "number": r["number"], "title": r["title"], "author": r["author"], "url": r["url"],
        "baseBranch": r["base_branch"], "headBranch": r["head_branch"], "headSha": r["head_sha"],
        "state": r["state"], "draft": bool(r["draft"]), "updatedAt": r["pr_updated_at"], "seenAt": r["seen_at"],
    }


def _map_review(r) -> dict | None:
    if not r:
        return None
    try:
        issues = json.loads(r["issues_json"]) if r["issues_json"] else []
    except Exception:
        issues = []
    try:
        stages = json.loads(r["stages_json"]) if r["stages_json"] else []
    except Exception:
        stages = []
    keys = r.keys()
    return {
        "id": r["id"],
        "repoId": r["repo_id"],
        "repoUrl": (r["repo_url"] if "repo_url" in keys else "") or "",
        "owner": (r["owner"] if "owner" in keys else "") or "",
        "name": (r["name"] if "name" in keys else "") or "",
        "prNumber": r["pr_number"],
        "headSha": r["head_sha"],
        "title": (r["title"] if "title" in keys else "") or "",
        "author": (r["author"] if "author" in keys else "") or "",
        "prUrl": (r["pr_url"] if "pr_url" in keys else "") or "",
        "risk": r["risk"] or "low",
        "decision": r["decision"] or "comment",
        "summary": r["summary"] or "",
        "reportMd": r["report_md"] or "",
        "issues": issues,
        "issueCount": r["issue_count"],
        "stages": stages,
        "model": r["model"] or "",
        "posted": bool(r["posted"]),
        "postError": r["post_error"] or "",
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
    }


def save_review(data: dict) -> dict | None:
    """保存审核报告：同一 PR 同一 head 只留一条（重复轮询不刷屏）"""
    db = init()
    repo_id = str(data.get("repoId") or "")
    pr_number = int(data.get("prNumber") or 0)
    head_sha = str(data.get("headSha") or "")
    if not repo_id or not pr_number:
        raise ValueError("缺少 repoId / prNumber")

    with _lock:
        exist = db.execute(
            "SELECT id FROM review WHERE repo_id = ? AND pr_number = ? AND head_sha = ?",
            (repo_id, pr_number, head_sha),
        ).fetchone()
        rid = exist["id"] if exist else (data.get("id") or "rev_" + uuid.uuid4().hex[:12])
        now = _now()
        issues = data.get("issues") or []
        db.execute(
            """INSERT INTO review (id, repo_id, pr_number, head_sha, risk, decision, summary, report_md,
                                   issues_json, issue_count, stages_json, model, posted, post_error,
                                   created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                    risk = excluded.risk, decision = excluded.decision, summary = excluded.summary,
                    report_md = excluded.report_md, issues_json = excluded.issues_json,
                    issue_count = excluded.issue_count, stages_json = excluded.stages_json,
                    model = excluded.model, updated_at = excluded.updated_at""",
            (
                rid, repo_id, pr_number, head_sha,
                str(data.get("risk") or "low"), str(data.get("decision") or "comment"),
                str(data.get("summary") or ""), str(data.get("reportMd") or ""),
                json.dumps(issues, ensure_ascii=False), int(data.get("issueCount") or len(issues)),
                json.dumps(data.get("stages") or [], ensure_ascii=False), str(data.get("model") or ""),
                1 if data.get("posted") else 0, str(data.get("postError") or ""),
                now, now,
            ),
        )
        db.commit()
    return get_review(rid)


_REVIEW_SELECT = """SELECT v.*, r.url AS repo_url, r.owner, r.name, p.title, p.author, p.url AS pr_url
                      FROM review v
                      JOIN repo r ON r.id = v.repo_id
                      LEFT JOIN pr p ON p.repo_id = v.repo_id AND p.number = v.pr_number"""


def get_review(rid: str) -> dict | None:
    db = init()
    with _lock:
        r = db.execute(_REVIEW_SELECT + " WHERE v.id = ?", (str(rid or ""),)).fetchone()
    return _map_review(r)


def find_review(repo_id: str, pr_number, head_sha: str) -> dict | None:
    db = init()
    with _lock:
        r = db.execute(
            "SELECT id FROM review WHERE repo_id = ? AND pr_number = ? AND head_sha = ?",
            (str(repo_id), int(pr_number or 0), str(head_sha or "")),
        ).fetchone()
    return get_review(r["id"]) if r else None


def list_reviews(user: str = DEFAULT_USER, repo_id: str = "", limit: int = 20) -> list:
    """审核报告列表（跨仓库，供审核工作台展示）"""
    db = init()
    lim = min(int(limit or 20), 100)
    with _lock:
        if repo_id:
            rows = db.execute(
                _REVIEW_SELECT + " WHERE r.user_key = ? AND v.repo_id = ? ORDER BY v.created_at DESC LIMIT ?",
                (user, str(repo_id), lim),
            ).fetchall()
        else:
            rows = db.execute(
                _REVIEW_SELECT + " WHERE r.user_key = ? ORDER BY v.created_at DESC LIMIT ?",
                (user, lim),
            ).fetchall()
    return [_map_review(r) for r in rows]


def mark_review_posted(rid: str, posted: bool = True, error: str = "") -> dict | None:
    db = init()
    with _lock:
        db.execute(
            "UPDATE review SET posted = ?, post_error = ?, updated_at = ? WHERE id = ?",
            (1 if posted else 0, str(error or ""), _now(), str(rid)),
        )
        db.commit()
    return get_review(rid)


def stats() -> dict:
    db = init()
    with _lock:
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
