# -*- coding: utf-8 -*-
"""持久层 · 会话与消息（含 FTS5 中文全文检索）"""

import json

from .db import DEFAULT_USER, MAX_CONVOS, MAX_MSGS, init, lock, now


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
    with lock:
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
    with lock:
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
    ts = now()
    msgs = (data.get("messages") if isinstance(data.get("messages"), list) else [])[-MAX_MSGS:]
    title = str(data.get("title") or "新会话")[:200]
    profile_json = json.dumps(data.get("profile"), ensure_ascii=False) if data.get("profile") else None
    created_at = int(data.get("createdAt") or ts)
    updated_at = int(data.get("updatedAt") or ts)

    with lock:
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

