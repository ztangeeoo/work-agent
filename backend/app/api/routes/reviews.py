# -*- coding: utf-8 -*-
"""审核报告接口：/api/reviews*、/api/review/events（SSE）

/review/events 是前端审核工作台的实时通道：轮询状态与审核完成都走这里，
订阅时先补发最近一批，页面晚开也不会漏掉刚跑完的报告。
"""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ... import store
from ...services import events, review_client as rv
from ..deps import db_blocked, err, user_key

router = APIRouter(tags=["reviews"])


@router.get("/api/reviews")
async def api_reviews_list(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    repo_id = request.query_params.get("repoId") or ""
    try:
        limit = min(int(request.query_params.get("limit") or 20), 100)
    except Exception:
        limit = 20
    try:
        return {"ok": True, "list": store.list_reviews(user_key(request), repo_id=repo_id, limit=limit)}
    except Exception as e:
        return err("读取审核报告失败：" + str(e), 500)


@router.post("/api/reviews/{rid}/post")
async def api_review_post(request: Request, rid: str):
    """把某份审核意见一键回帖到 PR"""
    blocked = db_blocked()
    if blocked:
        return blocked
    review = store.get_review(rid)
    if not review:
        return err("审核报告不存在：" + rid, 404)
    repo = store.get_repo_by_id(review["repoId"], with_token=True)
    if not repo:
        return err("仓库已被删除", 404)
    if not repo.get("token"):
        return err("该仓库没有配置 Token，无法回帖", 400)
    body = "\n".join([
        review.get("reportMd") or "",
        "",
        "---",
        "🤖 由 AI 代码审核官自动生成（侦察官 → 审核官 → 裁决官）。",
        "请结合业务上下文判断，机器意见仅供参考。",
    ])
    try:
        r = await rv.post_pr_comment(repo, review["prNumber"], body)
        return {"ok": True, "url": r.get("url"), "review": store.mark_review_posted(rid, posted=True)}
    except Exception as e:
        store.mark_review_posted(rid, posted=False, error=str(e))
        return err("回帖失败：" + str(e), 502)


@router.get("/api/review/events")
async def api_review_events(request: Request):
    """审核事件订阅（SSE）：轮询状态 + 审核完成；订阅时先补发最近一批"""
    q: asyncio.Queue = asyncio.Queue()
    for ev in events.recent(30):
        q.put_nowait(ev)
    events.add_client(q)

    async def gen():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            events.remove_client(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })
