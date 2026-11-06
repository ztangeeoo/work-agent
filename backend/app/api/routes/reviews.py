# -*- coding: utf-8 -*-
"""审核报告接口：/api/reviews*、/api/review/events（SSE）

/review/events 是前端审核工作台的实时通道：轮询状态与审核完成都走这里，
订阅时先补发最近一批，页面晚开也不会漏掉刚跑完的报告。
"""

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ... import notify, store
from ...services import events, notify_event, review_client as rv
from ...utils.render import render_comment_body
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
    body = render_comment_body(review)
    try:
        r = await rv.post_pr_comment(repo, review["prNumber"], body)
        saved = store.mark_review_posted(rid, posted=True)
    except Exception as e:
        store.mark_review_posted(rid, posted=False, error=str(e))
        return err("回帖失败：" + str(e), 502)

    # 手动回帖也通知：用户在页面上点「回帖」和自动回帖是同一个动作，
    # 群里看到的应该是同一件事。通知失败不影响回帖结果。
    push = {"ok": False, "error": "no pr"}
    try:
        prx = store.get_pr(review["repoId"], review["prNumber"]) or {}
        event = notify_event.build(
            {
                "number": review["prNumber"],
                "title": prx.get("title") or "",
                "author": prx.get("author") or review.get("author") or "",
                "url": prx.get("url") or review.get("prUrl") or "",
                "headBranch": prx.get("headBranch") or "",
                "baseBranch": prx.get("baseBranch") or "",
            },
            saved or {},
            repo=repo,
            trigger="manual",
        )
        push = await notify.dispatch(event)
    except Exception as e:
        push = {"ok": False, "error": str(e)}

    return {"ok": True, "url": r.get("url"), "review": saved,
            "pushed": bool(push.get("ok")),
            "pushError": "" if push.get("ok") else str(push.get("error") or ""),
            "channels": push.get("sent") or []}


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
