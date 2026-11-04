# -*- coding: utf-8 -*-
"""专家与智能体接口：/api/experts、/api/agents

前端专家中心完全按这份数据渲染（库不可用时前端会回退到内置常量）：
    GET /api/experts              专家清单（每个专家带自己的智能体列表）
    GET /api/experts?agents=0     只要专家本身
    GET /api/experts/{id}         单个专家
    GET /api/agents?expertId=xx   智能体清单（可按专家过滤）
"""

from fastapi import APIRouter, Request

from ... import store
from ..deps import db_blocked, err

router = APIRouter(tags=["catalog"])


@router.get("/api/experts")
async def api_experts_list(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    with_agents = request.query_params.get("agents") != "0"
    try:
        return {"ok": True, "dbAvailable": True, "list": store.list_experts(with_agents=with_agents)}
    except Exception as e:
        return err("读取专家失败：" + str(e), 500)


@router.get("/api/experts/{eid}")
async def api_expert_get(eid: str):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        item = store.get_expert(eid)
        return {"ok": True, "expert": item} if item else err("专家不存在：" + eid, 404)
    except Exception as e:
        return err("读取专家失败：" + str(e), 500)


@router.get("/api/agents")
@router.get("/api/agents")
async def api_agents_list(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    expert_id = (request.query_params.get("expertId") or "").strip() or None
    try:
        return {"ok": True, "dbAvailable": True, "list": store.list_agents(expert_id)}
    except Exception as e:
        return err("读取智能体失败：" + str(e), 500)
