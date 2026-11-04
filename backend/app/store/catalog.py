# -*- coding: utf-8 -*-
"""持久层 · 专家与智能体

关系：会话（convo.agent_id） → 智能体（agent） → 专家（expert.mode）

  expert  对外的能力包：面试出题官 / 代码审核官
  agent   专家下的流水线角色：策划官 / 面试官 / 评分官、侦察官 / 审核官 / 裁决官…
          每个专家有且只有一个 is_primary=1 的主智能体，新会话默认绑定它
"""

import json

from .db import init, lock

_AGENT_COLS = "id, expert_id, stage, name, icon, role, is_primary, sort"


def _tags(raw) -> list:
    try:
        val = json.loads(raw or "[]")
        return [str(x) for x in val] if isinstance(val, list) else []
    except Exception:
        return []


def _map_expert(r, agents=None) -> dict:
    return {
        "id": r["id"],
        "mode": r["mode"],
        "name": r["name"],
        "icon": r["icon"] or "",
        "role": r["role"] or "",
        "desc": r["desc"] or "",
        "tags": _tags(r["tags_json"]),
        "cta": r["cta"] or "",
        "sort": r["sort"],
        "agents": agents if agents is not None else [],
    }


def _map_agent(r) -> dict:
    return {
        "id": r["id"],
        "expertId": r["expert_id"],
        "stage": r["stage"],
        "name": r["name"],
        "icon": r["icon"] or "",
        "role": r["role"] or "",
        "primary": bool(r["is_primary"]),
        "sort": r["sort"],
    }


def list_experts(with_agents: bool = True) -> list:
    """专家清单（默认带各自的智能体），前端专家中心直接用这份数据渲染。"""
    db = init()
    with lock:
        rows = db.execute(
            """SELECT id, mode, name, icon, role, desc, tags_json, cta, sort
                 FROM expert WHERE enabled = 1 ORDER BY sort, id"""
        ).fetchall()
        out = []
        for r in rows:
            agents = []
            if with_agents:
                ags = db.execute(
                    f"""SELECT {_AGENT_COLS} FROM agent
                         WHERE expert_id = ? AND enabled = 1 ORDER BY sort, id""",
                    (r["id"],),
                ).fetchall()
                agents = [_map_agent(a) for a in ags]
            out.append(_map_expert(r, agents))
        return out


def get_expert(expert_id: str) -> dict | None:
    db = init()
    with lock:
        r = db.execute(
            """SELECT id, mode, name, icon, role, desc, tags_json, cta, sort
                 FROM expert WHERE id = ?""",
            (str(expert_id or ""),),
        ).fetchone()
        if not r:
            return None
        ags = db.execute(
            f"""SELECT {_AGENT_COLS} FROM agent
                 WHERE expert_id = ? AND enabled = 1 ORDER BY sort, id""",
            (r["id"],),
        ).fetchall()
    return _map_expert(r, [_map_agent(a) for a in ags])


def get_agent(agent_id: str) -> dict | None:
    """单个智能体（附带所属专家的 mode，方便上层直接拿去路由）。"""
    db = init()
    with lock:
        r = db.execute(
            f"""SELECT a.id, a.expert_id, a.stage, a.name, a.icon, a.role, a.is_primary, a.sort,
                       e.mode AS expert_mode, e.name AS expert_name, e.icon AS expert_icon
                  FROM agent a JOIN expert e ON e.id = a.expert_id
                 WHERE a.id = ?""",
            (str(agent_id or ""),),
        ).fetchone()
    if not r:
        return None
    item = _map_agent(r)
    item.update({
        "expertMode": r["expert_mode"],
        "expertName": r["expert_name"],
        "expertIcon": r["expert_icon"] or "",
    })
    return item


def list_agents(expert_id: str | None = None) -> list:
    db = init()
    with lock:
        if expert_id:
            rows = db.execute(
                f"""SELECT {_AGENT_COLS} FROM agent
                     WHERE expert_id = ? AND enabled = 1 ORDER BY sort, id""",
                (str(expert_id),),
            ).fetchall()
        else:
            rows = db.execute(
                f"""SELECT {_AGENT_COLS} FROM agent WHERE enabled = 1 ORDER BY expert_id, sort, id"""
            ).fetchall()
    return [_map_agent(r) for r in rows]


def primary_agent_id(expert_id: str) -> str | None:
    """专家的主智能体 id（新会话默认绑定它）。"""
    db = init()
    with lock:
        r = db.execute(
            "SELECT id FROM agent WHERE expert_id = ? AND is_primary = 1 AND enabled = 1 LIMIT 1",
            (str(expert_id or ""),),
        ).fetchone()
    return r["id"] if r else None


def primary_agent_id_by_mode(mode: str) -> str | None:
    """按老字段 agent_mode（interview / review）取主智能体，用于老数据与老接口兼容。"""
    db = init()
    with lock:
        r = db.execute(
            """SELECT a.id FROM agent a JOIN expert e ON e.id = a.expert_id
                WHERE e.mode = ? AND a.is_primary = 1 AND a.enabled = 1 LIMIT 1""",
            (str(mode or ""),),
        ).fetchone()
    return r["id"] if r else None


def catalog_version() -> str:
    """专家 / 智能体表的版本戳（条数 + 最后修改时间）。

    给「派生自这两张表、又需要缓存」的地方用：版本没变就不用重算。
    改一条 agent 的 name 不会影响归属，但会改 updated_at —— 宁可多算一次，
    也不要出现「库里改了、页面还是旧的」。
    """
    db = init()
    with lock:
        r = db.execute(
            """SELECT
                 (SELECT COUNT(*) FROM expert) AS ec,
                 (SELECT COALESCE(MAX(updated_at), 0) FROM expert) AS et,
                 (SELECT COUNT(*) FROM agent) AS ac,
                 (SELECT COALESCE(MAX(updated_at), 0) FROM agent) AS at"""
        ).fetchone()
    return f"{r['ec']}.{r['et']}.{r['ac']}.{r['at']}"
