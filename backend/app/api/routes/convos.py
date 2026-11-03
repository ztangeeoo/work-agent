# -*- coding: utf-8 -*-
"""会话与检索接口：/api/convos*、/api/search

会话是「整场覆盖」保存的（消息量小，行为可预测），前端每次保存整份 messages。
"""

from fastapi import APIRouter, Request

from ... import store
from ..deps import db_blocked, err, user_key

router = APIRouter(tags=["convos"])


@router.get("/api/convos")
async def api_convos_list(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    qp = request.query_params
    try:
        limit = min(int(qp.get("limit") or 200), 200)
    except Exception:
        limit = 200
    with_messages = qp.get("messages") != "0"
    try:
        return {"ok": True, "dbAvailable": True,
                "list": store.list_convos(user_key(request), limit=limit, with_messages=with_messages)}
    except Exception as e:
        return err("数据库操作失败：" + str(e), 500)


@router.post("/api/convos")
async def api_convos_create(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        body = await request.json()
        return {"ok": True, **store.save_convo(body, user_key(request))}
    except Exception as e:
        return err("数据库操作失败：" + str(e), 500)


@router.get("/api/convos/{cid}")
async def api_convo_get(request: Request, cid: str):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        conv = store.get_convo(cid, user_key(request))
        return {"ok": True, "convo": conv} if conv else err("会话不存在：" + cid, 404)
    except Exception as e:
        return err("数据库操作失败：" + str(e), 500)


@router.post("/api/convos/{cid}")
async def api_convo_save(request: Request, cid: str):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        body = await request.json()
        return {"ok": True, **store.save_convo({**body, "id": cid}, user_key(request))}
    except Exception as e:
        return err("数据库操作失败：" + str(e), 500)


@router.delete("/api/convos/{cid}")
async def api_convo_delete(request: Request, cid: str):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        removed = store.delete_convo(cid, user_key(request))
        return {"ok": True} if removed else err("会话不存在：" + cid, 404)
    except Exception as e:
        return err("数据库操作失败：" + str(e), 500)


@router.get("/api/search")
async def api_search(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    q = request.query_params.get("q") or ""
    try:
        limit = min(int(request.query_params.get("limit") or 30), 100)
    except Exception:
        limit = 30
    if not q.strip():
        return {"ok": True, "query": q, "results": []}
    try:
        return {"ok": True, "query": q, "results": store.search_messages(q, user=user_key(request), limit=limit)}
    except Exception as e:
        return err("检索失败：" + str(e), 500)
