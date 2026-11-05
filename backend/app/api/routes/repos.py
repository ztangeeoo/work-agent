# -*- coding: utf-8 -*-
"""仓库配置接口：/api/repos*

仓库 = 一个代码托管地址 + Token + 轮询间隔。地址解析与平台探测交给
services/review_client（GitHub / GitLab / 自建实例）。
"""

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import store
from ...services import review_client as rv
from ...state import get_poller
from ..deps import db_blocked, err, swallow, user_key

router = APIRouter(tags=["repos"])


def _scheduler():
    """轮询调度器状态：没启动 / 循环挂了 都要让前端看得见"""
    try:
        p = get_poller()
    except Exception:
        return {"running": False, "tickSec": 0}
    return {"running": bool(getattr(p, "scheduler_running", False)),
            "tickSec": int(getattr(p, "tick_sec", 0) or 0)}


def _runtime_of(repo_id: str) -> dict:
    try:
        return get_poller().runtime_status(repo_id) or {}
    except Exception:
        return {}


def _decorate(repo: dict) -> dict:
    """给仓库补上「轮询进行中」这类只存在于进程内存的实时状态

    - polling / phase / reviewingPr：此刻是否正在拉 PR 或审核某个 PR
    - nextPollAt：按 intervalSec 推算的下一轮时间（0 = 没启用或还没轮询过）
    """
    rt = _runtime_of(repo["id"])
    interval = int(repo.get("intervalSec") or 120)
    last = int(repo.get("lastPollAt") or 0)
    out = dict(repo)
    out["polling"] = bool(rt.get("polling"))
    out["phase"] = rt.get("phase") or ""
    out["reviewingPr"] = rt.get("reviewingPr")
    out["startedAt"] = int(rt.get("startedAt") or 0)
    out["nextPollAt"] = (last + interval * 1000) if (repo.get("enabled") and last) else 0
    return out


@router.get("/api/repos")
async def api_repos_list(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        list_ = [_decorate(r) for r in store.list_repos(user_key(request))]
        return {"ok": True, "list": list_, "scheduler": _scheduler()}
    except Exception as e:
        return err("仓库操作失败：" + str(e), 500)


@router.post("/api/repos")
async def api_repos_save(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        body = await request.json()
    except Exception as e:
        return err("请求体解析失败：" + str(e), 400)
    try:
        parsed = rv.parse_repo_url(body.get("url"), body.get("provider"))
    except Exception as e:
        return err(str(e), 400)
    # 用户没显式选平台时，对自建实例探测一次
    # （GitLab 走 /api/v4，Gitee 含企业版走 /api/v5，GitHub 企业版走 /api/v3，猜错就是 404）
    # 注意：探测只借用 token 发请求，不能把 token 写回 parsed —— save_repo 见到 token='' 会清空已存的 Token
    if not str(body.get("provider") or "").strip():
        try:
            probe = await rv.ensure_provider({**parsed, "token": body.get("token") or ""})
            parsed["provider"] = probe.get("provider") or parsed["provider"]
        except Exception:
            pass
    try:
        repo = store.save_repo({**body, **parsed}, user_key(request))
        return {"ok": True, "repo": repo}
    except Exception as e:
        return err("仓库操作失败：" + str(e), 500)


@router.delete("/api/repos/{rid}")
async def api_repos_delete(request: Request, rid: str):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        ok = store.delete_repo(rid, user_key(request))
        return {"ok": True} if ok else err("仓库不存在：" + rid, 404)
    except Exception as e:
        return err("仓库操作失败：" + str(e), 500)


@router.post("/api/repos/{rid}/test")
async def api_repos_test(request: Request, rid: str):
    """测试地址 / Token 是否可用"""
    blocked = db_blocked()
    if blocked:
        return blocked
    repo = store.get_repo(rid, user_key(request), with_token=True)
    if not repo:
        return err("仓库不存在：" + rid, 404)
    try:
        r = await rv.test_repo(repo)
        # test_repo 里会把自建实例的 provider 校准好，变化就回写
        try:
            store.set_repo_provider(rid, repo.get("provider") or "")
        except Exception:
            pass
        store.touch_repo(rid, error="")
        return {"ok": True, **r}
    except Exception as e:
        store.touch_repo(rid, error=str(e))
        return err(str(e), 502)


@router.post("/api/repos/{rid}/toggle")
async def api_repos_toggle(request: Request, rid: str):
    """暂停 / 恢复自动轮询 —— 头部那个「轮询中」开关调它"""
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        body = await request.json()
    except Exception:
        body = {}
    if "enabled" in body:
        enabled = bool(body.get("enabled"))
    else:
        cur = store.get_repo(rid, user_key(request))
        if not cur:
            return err("仓库不存在：" + rid, 404)
        enabled = not cur.get("enabled")
    repo = store.set_repo_enabled(rid, enabled, user_key(request))
    if not repo:
        return err("仓库不存在：" + rid, 404)
    return {"ok": True, "repo": _decorate(repo), "scheduler": _scheduler()}


@router.post("/api/repos/{rid}/poll")
async def api_repos_poll(request: Request, rid: str):
    """立即轮询：默认异步触发（202），结果走 /api/review/events；?wait=1 阻塞等待"""
    blocked = db_blocked()
    if blocked:
        return blocked
    if not store.get_repo(rid, user_key(request)):
        return err("仓库不存在：" + rid, 404)
    force = request.query_params.get("force") != "0"
    task = asyncio.create_task(get_poller().poll_repo(rid, force=force))
    task.add_done_callback(swallow)
    if request.query_params.get("wait") != "1":
        return JSONResponse({"ok": True, "started": True}, status_code=202)
    try:
        return {"ok": True, **(await task)}
    except Exception as e:
        return err(str(e), 502)


@router.get("/api/repos/{rid}/prs")
async def api_repos_prs(request: Request, rid: str):
    blocked = db_blocked()
    if blocked:
        return blocked
    repo = store.get_repo(rid, user_key(request), with_token=False)
    if not repo:
        return err("仓库不存在：" + rid, 404)
    try:
        limit = min(int(request.query_params.get("limit") or 20), 100)
    except Exception:
        limit = 20
    try:
        return {"ok": True, "repo": repo, "list": store.list_prs(rid, limit=limit)}
    except Exception as e:
        return err("读取 PR 列表失败：" + str(e), 500)


@router.post("/api/repos/{rid}/prs/{number}/review")
async def api_repos_review_pr(request: Request, rid: str, number: int):
    """立即审核单个 PR"""
    blocked = db_blocked()
    if blocked:
        return blocked
    if not store.get_repo(rid, user_key(request)):
        return err("仓库不存在：" + rid, 404)
    try:
        return {"ok": True, "review": await get_poller().review_pr(rid, number)}
    except Exception as e:
        return err(str(e), 502)
