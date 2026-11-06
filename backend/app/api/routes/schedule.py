# -*- coding: utf-8 -*-
"""定时任务接口：/api/schedule*

回答两个问题，分别对应两组接口：
  1.「每个仓库什么时候跑、上次跑得怎么样」→ GET /api/schedule
  2.「每一次执行到底发生了什么」        → GET /api/schedule/{id}/ticks

批量操作只有两个动作，也刻意只有两个：
  改配置（POST /api/schedule/batch）、编成一组（POST /api/schedule/group）。
  组的意义是共享一个节拍器——不编组时每个仓库按自己的节奏走，
  编组之后「一个到点，全组一起触发」，这才是「一起执行」。
"""

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import store
from ...jobs import schedule as jobs
from ...state import get_poller
from ..deps import db_blocked, err, user_key

router = APIRouter(tags=["schedule"])

# 手动「全部执行」时的并发上限。现在没有全局槽位，先在这里兜一个：
# 串行会让用户等太久，完全不限会把模型额度和 GitHub rate limit 打爆。
MANUAL_CONCURRENCY = 2


def _today_start_ms() -> int:
    """本地时区今天 0 点。用本地而不是 UTC——用户看的是墙上的钟。"""
    t = time.localtime()
    return int(time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)


def _tick_decorate(ticks: list, user: str) -> list:
    """给执行记录补上仓库名和「这一轮贴出去的评论」

    仓库名：页面上的流水不该只显示一串 uuid。
    评论：执行历史要能筛「有评论」的轮次，靠 review.tick_id 直连，
        不能按时间窗猜——同仓库的复用执行（skipped）横跨了别人的整个
        审核过程，时间上完全罩得住，会把没贴评论的一轮也算进去。
    """
    sched = {s["id"]: s for s in store.list_schedules(user)}
    repos = {r["id"]: r for r in store.list_repos(user)}
    for t in ticks:
        s = sched.get(t["scheduleId"]) or {}
        rid = s.get("scope") or ""
        if not rid and ":" in str(t["scheduleId"]):
            rid = str(t["scheduleId"]).split(":", 1)[1]
        repo = repos.get(rid) or {}
        t["repoId"] = rid
        t["owner"] = repo.get("owner") or ""
        t["name"] = repo.get("name") or ""

    posts = store.posted_map([t.get("id") for t in ticks])
    for t in ticks:
        got = posts.get(int(t["id"])) or []
        t["postedReviews"] = got
        t["commentCount"] = len(got)
        t["hasComment"] = bool(got)
    return ticks


def _scheduler_state() -> dict:
    try:
        p = get_poller()
    except Exception:
        return {"running": False, "tickSec": 0}
    return {
        "running": bool(getattr(p, "scheduler_running", False)),
        "tickSec": int(getattr(p, "tick_sec", 0) or 0),
    }


def _runtime_of(repo_id: str) -> dict:
    try:
        return get_poller().runtime_status(repo_id) or {}
    except Exception:
        return {}


def _decorate(items: list, repos: list, groups: list, user: str) -> list:
    """把调度项补成前端能直接画的形状：仓库名、分组名、上次结果、近 7 天统计"""
    repo_map = {r["id"]: r for r in repos}
    group_map = {g["id"]: g for g in groups}
    latest = store.latest_ticks()
    stats = store.tick_stats_bulk(7)
    out = []
    for s in items:
        repo = repo_map.get(s["scope"]) or {}
        rt = _runtime_of(s["scope"])
        last = latest.get(s["id"]) or {}
        out.append({
            **s,
            "repoId": s["scope"],
            "owner": repo.get("owner") or "",
            "name": repo.get("name") or "",
            "url": repo.get("url") or "",
            "repoEnabled": bool(repo.get("enabled")),
            "provider": repo.get("provider") or "github",
            "groupName": (group_map.get(s["groupId"]) or {}).get("name") or "",
            "polling": bool(rt.get("polling")),
            "phase": rt.get("phase") or "",
            "lastOutcome": last.get("outcome") or "",
            "lastFiredAt": last.get("firedAt") or 0,
            "lastDurationMs": last.get("durationMs"),
            "lastReason": last.get("reason") or "",
            "lastResult": last.get("result"),
            "stats": stats.get(s["id"]) or {"fired": 0, "failed": 0, "skipped": 0,
                                            "running": 0, "total": 0, "avgMs": 0},
        })
    return out


@router.get("/api/schedule")
async def api_schedule_list(request: Request):
    blocked = db_blocked()
    if blocked:
        return blocked
    user = user_key(request)
    try:
        groups = store.list_groups(user)
        items = store.list_schedules(user)
        repos = store.list_repos(user)
        decorated = _decorate(items, repos, groups, user)
        group_out = []
        for g in groups:
            members = [i for i in decorated if i["groupId"] == g["id"]]
            group_out.append({**g, "memberCount": len(members),
                              "memberIds": [m["repoId"] for m in members]})
        return {"ok": True, "groups": group_out, "list": decorated,
                "scheduler": _scheduler_state(),
                "summary": {"today": store.tick_summary(_today_start_ms()),
                            "all": store.tick_summary(None)},
                "todayStart": _today_start_ms()}
    except Exception as e:
        return err("读取定时任务失败：" + str(e), 500)


@router.get("/api/schedule/ticks")
async def api_schedule_ticks_recent(request: Request):
    """全局最近的执行记录（首页/看板用）

    带仓库名和今日统计，页面一次请求就能把「流水 + 概要」都画出来。
    """
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        limit = int(request.query_params.get("limit") or 50)
    except Exception:
        limit = 50
    # 前端筛选都在本地做，唯独「有评论」放在服务端：评论很稀疏，
    # 取完最近 N 条再过滤会把更早的那几次全丢掉。
    only_comment = str(request.query_params.get("comment") or "") in ("1", "true", "yes")
    user = user_key(request)
    try:
        ticks = _tick_decorate(store.list_ticks(None, limit, only_comment=only_comment), user)
        return {"ok": True, "list": ticks, "onlyComment": only_comment,
                "summary": store.tick_summary(_today_start_ms()),
                "todayStart": _today_start_ms()}
    except Exception as e:
        return err("读取执行记录失败：" + str(e), 500)


@router.get("/api/schedule/{sid}/ticks")
async def api_schedule_ticks(request: Request, sid: str):
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        limit = int(request.query_params.get("limit") or 50)
    except Exception:
        limit = 50
    only_comment = str(request.query_params.get("comment") or "") in ("1", "true", "yes")
    user = user_key(request)
    try:
        return {
            "ok": True,
            "list": _tick_decorate(store.list_ticks(sid, limit, only_comment=only_comment), user),
            "stats": store.tick_stats(sid, 7),
            "schedule": store.get_schedule(sid),
        }
    except Exception as e:
        return err("读取执行记录失败：" + str(e), 500)


@router.post("/api/schedule/batch")
async def api_schedule_batch(request: Request):
    """批量改多个仓库的调度配置——「多个仓库一起配置」

    入参用 repoId（前端手上就是这个），内部换成 schedule id。
    """
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        data = await request.json()
    except Exception as e:
        return err("请求体解析失败：" + str(e))
    repo_ids = [str(i) for i in (data.get("repoIds") or []) if str(i).strip()]
    if not repo_ids:
        return err("缺少 repoIds")
    user = user_key(request)
    sids = [store.repo_schedule_id(r) for r in repo_ids]

    # 节奏同时写回 repo 表：仓库配置页和历史字段还在用它，两边不能打架
    if data.get("everySec") is not None:
        try:
            every = max(30, min(int(data["everySec"]), 86400))
        except Exception:
            return err("everySec 不合法")
        try:
            store.sync_repo_interval(repo_ids, every, user)
        except Exception as e:
            return err("更新仓库间隔失败：" + str(e), 500)

    try:
        n = store.batch_update_schedules(sids, data, user)
        return {"ok": True, "updated": n,
                "list": [s for s in store.list_schedules(user) if s["id"] in set(sids)]}
    except Exception as e:
        return err("批量配置失败：" + str(e), 500)


@router.post("/api/schedule/group")
async def api_schedule_group(request: Request):
    """建组 / 改组：把多个仓库绑到一个节拍器上，实现「一起触发」"""
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        data = await request.json()
    except Exception as e:
        return err("请求体解析失败：" + str(e))
    user = user_key(request)
    try:
        g = store.save_group(data, user)
        repo_ids = [str(i) for i in (data.get("repoIds") or []) if str(i).strip()]
        if repo_ids:
            store.bind_group([store.repo_schedule_id(r) for r in repo_ids], g["id"], user)
        members = store.list_by_group(g["id"])
        return {"ok": True, "group": {**g, "memberCount": len(members),
                                      "memberIds": [m["scope"] for m in members]}}
    except Exception as e:
        return err("保存分组失败：" + str(e), 500)


@router.delete("/api/schedule/group/{gid}")
async def api_schedule_group_delete(request: Request, gid: str):
    """删组只解绑，不删成员——删个分组不该把仓库轮询一起删了"""
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        return {"ok": True, "deleted": store.delete_group(gid)}
    except Exception as e:
        return err("删除分组失败：" + str(e), 500)


@router.post("/api/schedule/run")
async def api_schedule_run(request: Request):
    """立即执行（单个或多个）。走和定时同一条记录链路，历史里标 source=manual"""
    blocked = db_blocked()
    if blocked:
        return blocked
    try:
        data = await request.json()
    except Exception as e:
        return err("请求体解析失败：" + str(e))
    repo_ids = [str(i) for i in (data.get("repoIds") or []) if str(i).strip()]
    if not repo_ids:
        return err("缺少 repoIds")

    try:
        poller = get_poller()
    except Exception as e:
        return err("调度器未就绪：" + str(e), 503)

    sem = asyncio.Semaphore(MANUAL_CONCURRENCY)

    async def one(rid: str) -> dict:
        sid = store.repo_schedule_id(rid)
        ts = store.now()
        tick_id = store.log_tick(sid, ts, ts, "running", "手动触发", source="manual")
        async with sem:
            try:
                result = await poller.poll_repo(rid, force=True)
                store.finish_tick(tick_id, "fired", result,
                                  duration_ms=store.now() - ts)
                return {"repoId": rid, "ok": True, "result": result}
            except Exception as e:
                store.finish_tick(tick_id, "failed", None, str(e),
                                  duration_ms=store.now() - ts)
                return {"repoId": rid, "ok": False, "error": str(e)}

    results = await asyncio.gather(*[one(r) for r in repo_ids])
    return {"ok": True, "list": list(results)}
