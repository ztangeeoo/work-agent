# -*- coding: utf-8 -*-
"""持久层 · 会话与消息（含 FTS5 中文全文检索）

会话是「整场覆盖」保存的（消息量小，行为可预测），前端每次保存整份 messages。

会话与专家的关系不直接存储，而是走两级：
    convo.agent_id → agent（智能体） → expert（专家）
读取时 JOIN 出专家/智能体信息，写入时可以只给 agentId，agent_mode 由专家派生。
"""

import json

from . import catalog
from .db import DEFAULT_USER, MAX_CONVOS, MAX_MSGS, init, lock, now

# 会话查询统一带上「智能体 → 专家」两级关联，前端一次拿全，不用二次请求
_CONVO_SELECT = """
SELECT c.id, c.title, c.profile_json, c.agent_id, c.agent_mode, c.repo_id,
       c.created_at, c.updated_at,
       a.name AS agent_name, a.icon AS agent_icon, a.stage AS agent_stage,
       e.id AS expert_id, e.name AS expert_name, e.icon AS expert_icon,
       COALESCE(e.mode, c.agent_mode) AS mode
  FROM convo c
  LEFT JOIN agent  a ON a.id = c.agent_id
  LEFT JOIN expert e ON e.id = a.expert_id
"""


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
        # 会话绑定的智能体（进程内先绑它），NULL = 还没关联专家/智能体
        "agentId": r["agent_id"] or None,
        "agentName": r["agent_name"] or "",
        "agentStage": r["agent_stage"] or "",
        # 智能体所属的专家：mode 是给路由 / 界面布局用的冗余字段
        "expertId": r["expert_id"] or None,
        "expertName": r["expert_name"] or "",
        "expertIcon": r["expert_icon"] or "",
        "mode": r["mode"] or None,
        # 审核会话绑定的仓库（专家配置的一部分）
        "repoId": r["repo_id"] or None,
    }


def list_convos(user: str = DEFAULT_USER, limit: int = MAX_CONVOS, with_messages: bool = True) -> list:
    """会话列表（默认带消息，前端切换会话无需二次请求）"""
    db = init()
    with lock:
        rows = db.execute(
            _CONVO_SELECT + " WHERE c.user_key = ? ORDER BY c.updated_at DESC LIMIT ?",
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
    with lock:
        r = db.execute(
            _CONVO_SELECT + " WHERE c.id = ? AND c.user_key = ?",
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


def resolve_binding(data: dict) -> tuple[str | None, str | None]:
    """把前端传来的绑定信息归一成 (agent_id, agent_mode)。

    优先 agentId（会话 → 智能体），mode 只作为老接口的兼容兜底：
      - 给了 agentId → agent_mode 由它的专家派生，保证两级关系一致
      - 只给 mode     → 取该专家的主智能体
      - 都没给        → 未关联，两者都为 NULL
    """
    agent_id = str((data or {}).get("agentId") or (data or {}).get("agent_id") or "").strip() or None
    mode = str((data or {}).get("mode") or "").strip() or None

    if agent_id:
        ag = catalog.get_agent(agent_id)
        if ag:
            return ag["id"], ag.get("expertMode")
        agent_id = None          # 未知智能体：丢弃，退回按 mode 解析
    if mode:
        return catalog.primary_agent_id_by_mode(mode), mode
    return None, None


def save_convo(data: dict, user: str = DEFAULT_USER) -> dict:
    """保存整场会话（覆盖式，事务内完成）"""
    cid = str((data or {}).get("id") or "").strip()
    if not cid:
        raise ValueError("缺少会话 id")
    agent_id, agent_mode = resolve_binding(data)
    db = init()
    ts = now()
    msgs = (data.get("messages") if isinstance(data.get("messages"), list) else [])[-MAX_MSGS:]
    title = str(data.get("title") or "新会话")[:200]
    profile_json = json.dumps(data.get("profile"), ensure_ascii=False) if data.get("profile") else None
    repo_id = str(data.get("repoId") or "").strip() or None
    created_at = int(data.get("createdAt") or ts)
    updated_at = int(data.get("updatedAt") or ts)

    with lock:
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO convo (id, user_key, title, profile_json, agent_id, agent_mode, repo_id, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                        title        = excluded.title,
                        profile_json = excluded.profile_json,
                        agent_id     = excluded.agent_id,
                        agent_mode   = excluded.agent_mode,
                        repo_id      = excluded.repo_id,
                        updated_at   = excluded.updated_at""",
                (cid, user, title, profile_json, agent_id, agent_mode, repo_id, created_at, updated_at),
            )
            db.execute("DELETE FROM msg WHERE convo_id = ?", (cid,))
            seen_ids = set()
            for i, m in enumerate(msgs):
                mid = str(m.get("id") or f"{cid}_{i}")
                # 同一会话内 id 重复时补序号，避免整场保存败在唯一索引上
                if mid in seen_ids:
                    mid = f"{mid}_{i}"
                seen_ids.add(mid)
                db.execute(
                    "INSERT INTO msg (id, convo_id, seq, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        mid,
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


def clear_messages(cid: str, user: str = DEFAULT_USER) -> int:
    """清空一场会话的消息，保留会话本身（标题 / 绑定 / 画像都不动）。

    单独做一个接口，是因为「覆盖式保存一个空 messages」在前端有去抖和时序竞争：
    只要 agent 把旧消息回填一次，空写就会被非空覆盖，清空看起来像没生效。
    """
    db = init()
    with lock:
        own = db.execute(
            "SELECT 1 FROM convo WHERE id = ? AND user_key = ?", (str(cid), user)
        ).fetchone()
        if not own:
            return 0
        try:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("DELETE FROM msg WHERE convo_id = ?", (str(cid),))
            db.execute("UPDATE convo SET updated_at = ? WHERE id = ?", (now(), str(cid)))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise
    return int(cur.rowcount or 0)


def delete_convo(cid: str, user: str = DEFAULT_USER) -> bool:
    db = init()
    with lock:
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

