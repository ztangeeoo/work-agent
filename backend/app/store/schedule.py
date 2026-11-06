# -*- coding: utf-8 -*-
"""定时调度仓储：组 / 调度项 / 触发记录

分工（与 jobs/schedule.py 的边界）：
  - 本文件只做 SQL：读写、CAS 推进、批量更新。不含任何「下次该几点」的算法。
  - 算法（累加防漂移、落后重置、确定性 jitter、catch_up）在 jobs/schedule.py，
    因为它需要纯粹可单测、不碰连接。

为什么 next_at 要落库
  以前前端用 `lastPollAt + intervalSec` 现算「下次轮询时间」，看着够用，但它
  表达不了三件事：jitter 把触发时刻错开、停机期间错过了几次、这次为什么被跳过。
  落库之后这些都有地方存，也能被历史记录对上。
"""

import json

from .db import DEFAULT_USER, init, lock, now

# 仓库轮询的调度项 id 约定；将来 chat / batch 换前缀即可
REPO_KIND = "repo.poll"


def repo_schedule_id(repo_id: str) -> str:
    return "%s:%s" % (REPO_KIND, str(repo_id))


# ------------------------------------------------------------------ 行映射

def _sched_row(r) -> dict:
    return {
        "id": r["id"],
        "kind": r["kind"],
        "scope": r["scope"],
        "groupId": r["group_id"],
        "everySec": int(r["every_sec"] or 0),
        "jitterPct": int(r["jitter_pct"] or 0),
        "nextAt": int(r["next_at"] or 0),
        "lastAt": int(r["last_at"] or 0),
        "catchUp": r["catch_up"] or "skip",
        "maxBacklog": int(r["max_backlog"] or 1),
        "enabled": bool(r["enabled"]),
        "failStreak": int(r["fail_streak"] or 0),
        "lastError": r["last_error"] or "",
        "createdAt": int(r["created_at"] or 0),
        "updatedAt": int(r["updated_at"] or 0),
    }


def _group_row(r) -> dict:
    return {
        "id": r["id"],
        "name": r["name"] or "",
        "everySec": int(r["every_sec"]) if r["every_sec"] is not None else None,
        "jitterPct": int(r["jitter_pct"] or 0),
        "enabled": bool(r["enabled"]),
        "nextAt": int(r["next_at"] or 0),
        "lastAt": int(r["last_at"] or 0),
        "createdAt": int(r["created_at"] or 0),
        "updatedAt": int(r["updated_at"] or 0),
    }


def _tick_row(r) -> dict:
    result = None
    if r["result_json"]:
        try:
            result = json.loads(r["result_json"])
        except Exception:
            result = None
    return {
        "id": int(r["id"]),
        "scheduleId": r["schedule_id"],
        "groupId": r["group_id"],
        "kind": r["kind"] or "",
        "source": r["source"] or "tick",
        "plannedAt": int(r["planned_at"] or 0),
        "firedAt": int(r["fired_at"] or 0),
        "startedAt": int(r["started_at"]) if r["started_at"] is not None else None,
        "finishedAt": int(r["finished_at"]) if r["finished_at"] is not None else None,
        "outcome": r["outcome"],
        "reason": r["reason"] or "",
        "runId": r["run_id"],
        "result": result,
        "durationMs": int(r["duration_ms"]) if r["duration_ms"] is not None else None,
    }


# ------------------------------------------------------------------ 调度项

def get_schedule(sid: str) -> dict | None:
    db = init()
    with lock:
        r = db.execute("SELECT * FROM schedule WHERE id = ?", (str(sid),)).fetchone()
    return _sched_row(r) if r else None


def list_schedules(user: str = DEFAULT_USER) -> list:
    db = init()
    with lock:
        rows = db.execute(
            "SELECT * FROM schedule WHERE user_key = ? ORDER BY created_at", (user,)
        ).fetchall()
    return [_sched_row(r) for r in rows]


def list_by_group(group_id: str) -> list:
    db = init()
    with lock:
        rows = db.execute(
            "SELECT * FROM schedule WHERE group_id = ? ORDER BY created_at", (str(group_id),)
        ).fetchall()
    return [_sched_row(r) for r in rows]


def upsert_repo_schedule(repo: dict, user: str = DEFAULT_USER, sync_interval: bool = False) -> dict:
    """为一个仓库建/更新调度项（幂等）

    sync_interval=False（启动迁移）：只同步 enabled，不动节奏——库里可能被人
    手工调过，启动时的批量同步不该把它抹掉。
    sync_interval=True（用户刚在仓库弹窗里改了间隔）：节奏以 repo 为准覆盖。
    """
    sid = repo_schedule_id(repo["id"])
    every = max(30, min(int(repo.get("intervalSec") or 120), 3600))
    ts = now()
    db = init()
    with lock:
        old = db.execute("SELECT * FROM schedule WHERE id = ?", (sid,)).fetchone()
        if old:
            if sync_interval:
                db.execute(
                    "UPDATE schedule SET enabled = ?, every_sec = ?, updated_at = ? WHERE id = ?",
                    (1 if repo.get("enabled") else 0, every, ts, sid),
                )
            else:
                db.execute(
                    "UPDATE schedule SET enabled = ?, updated_at = ? WHERE id = ?",
                    (1 if repo.get("enabled") else 0, ts, sid),
                )
        else:
            # 首次建：next_at 立刻到期，让启动后能马上跑一轮；之后由 CAS 推进
            db.execute(
                """INSERT INTO schedule (id, user_key, kind, scope, group_id, every_sec,
                                         jitter_pct, next_at, catch_up, max_backlog,
                                         enabled, created_at, updated_at)
                        VALUES (?, ?, ?, ?, NULL, ?, 0, ?, 'skip', 1, ?, ?, ?)""",
                (sid, user, REPO_KIND, str(repo["id"]), every, ts,
                 1 if repo.get("enabled") else 0, ts, ts),
            )
        db.commit()
    return get_schedule(sid)


def save_schedule(sid: str, data: dict) -> dict | None:
    """改单个调度项：节奏 / jitter / catch_up / 开关"""
    cur = get_schedule(sid)
    if not cur:
        return None
    every = data.get("everySec", cur["everySec"])
    try:
        every = max(30, min(int(every or cur["everySec"] or 120), 86400))
    except Exception:
        every = cur["everySec"]
    jitter = data.get("jitterPct", cur["jitterPct"])
    try:
        jitter = max(0, min(int(jitter or 0), 50))
    except Exception:
        jitter = cur["jitterPct"]
    catch_up = str(data.get("catchUp") or cur["catchUp"])
    if catch_up not in ("skip", "once", "all"):
        catch_up = cur["catchUp"]
    enabled = cur["enabled"] if data.get("enabled") is None else bool(data.get("enabled"))

    db = init()
    with lock:
        db.execute(
            """UPDATE schedule
                  SET every_sec = ?, jitter_pct = ?, catch_up = ?, enabled = ?, updated_at = ?
                WHERE id = ?""",
            (every, jitter, catch_up, 1 if enabled else 0, now(), str(sid)),
        )
        db.commit()
    return get_schedule(sid)


def batch_update_schedules(ids: list, data: dict, user: str = DEFAULT_USER) -> int:
    """批量改多个调度项——「多个仓库一起配置」的落点

    只改传进来的字段，没传的保持原样。返回受影响行数。
    """
    ids = [str(i) for i in (ids or []) if str(i).strip()]
    if not ids:
        return 0
    sets, args = [], []
    if data.get("everySec") is not None:
        try:
            sets.append("every_sec = ?")
            args.append(max(30, min(int(data["everySec"]), 86400)))
        except Exception:
            pass
    if data.get("enabled") is not None:
        sets.append("enabled = ?")
        args.append(1 if data.get("enabled") else 0)
    if data.get("jitterPct") is not None:
        try:
            sets.append("jitter_pct = ?")
            args.append(max(0, min(int(data["jitterPct"]), 50)))
        except Exception:
            pass
    if data.get("catchUp"):
        cu = str(data["catchUp"])
        if cu in ("skip", "once", "all"):
            sets.append("catch_up = ?")
            args.append(cu)
    if not sets:
        return 0
    sets.append("updated_at = ?")
    args.append(now())
    holder = ",".join("?" * len(ids))
    sql = "UPDATE schedule SET %s WHERE user_key = ? AND id IN (%s)" % (", ".join(sets), holder)
    db = init()
    with lock:
        cur = db.execute(sql, (*args, user, *ids))
        db.commit()
        return int(cur.rowcount or 0)


def bind_group(ids: list, group_id: str | None, user: str = DEFAULT_USER) -> int:
    """把多个调度项绑到同一个组（或解绑）"""
    ids = [str(i) for i in (ids or []) if str(i).strip()]
    if not ids:
        return 0
    holder = ",".join("?" * len(ids))
    db = init()
    with lock:
        cur = db.execute(
            "UPDATE schedule SET group_id = ?, updated_at = ? WHERE user_key = ? AND id IN (%s)" % holder,
            (group_id, now(), user, *ids),
        )
        db.commit()
        return int(cur.rowcount or 0)


def delete_schedule(sid: str) -> bool:
    db = init()
    with lock:
        cur = db.execute("DELETE FROM schedule WHERE id = ?", (str(sid),))
        db.commit()
        return bool(cur.rowcount)


def sync_repo_interval(repo_ids: list, every_sec: int, user: str = DEFAULT_USER) -> int:
    """批量改仓库的 interval_sec

    仓库配置页与左侧列表仍在读 repo.interval_sec / last_poll_at，
    调度项改了节奏必须同步回写，否则两处显示不一致（改完还是「每 2 分钟」）。
    """
    repo_ids = [str(i) for i in (repo_ids or []) if str(i).strip()]
    if not repo_ids:
        return 0
    holder = ",".join("?" * len(repo_ids))
    db = init()
    with lock:
        cur = db.execute(
            "UPDATE repo SET interval_sec = ?, updated_at = ? WHERE user_key = ? AND id IN (%s)" % holder,
            (int(every_sec), now(), user, *repo_ids),
        )
        db.commit()
        return int(cur.rowcount or 0)


# ------------------------------------------------------------------ 到期与推进

def due_schedules(at: int | None = None, user: str = DEFAULT_USER) -> list:
    """独立节奏（无组）的到期项"""
    ts = now() if at is None else int(at)
    db = init()
    with lock:
        rows = db.execute(
            """SELECT * FROM schedule
                WHERE user_key = ? AND enabled = 1 AND group_id IS NULL AND next_at <= ?
                ORDER BY next_at""",
            (user, ts),
        ).fetchall()
    return [_sched_row(r) for r in rows]


def due_groups(at: int | None = None, user: str = DEFAULT_USER) -> list:
    """到期的组（组只在自己配了 every_sec 时参与节拍）"""
    ts = now() if at is None else int(at)
    db = init()
    with lock:
        rows = db.execute(
            """SELECT * FROM schedule_group
                WHERE user_key = ? AND enabled = 1
                  AND every_sec IS NOT NULL AND next_at IS NOT NULL AND next_at <= ?
                ORDER BY next_at""",
            (user, ts),
        ).fetchall()
    return [_group_row(r) for r in rows]


def claim_schedule(sid: str, expect_next_at: int, new_next_at: int) -> bool:
    """CAS 推进：只有 next_at 仍是读到的那个值才成功

    退回 rowcount 而不是 RETURNING：计算新 next_at 需要 jitter / catch_up 逻辑，
    塞进一条 CASE SQL 会既难读又难测。CAS 保证同一时刻只有一个触发者，
    抢输的那个返回 False，安静跳过即可。
    """
    db = init()
    with lock:
        cur = db.execute(
            """UPDATE schedule
                  SET last_at = next_at, next_at = ?, updated_at = ?
                WHERE id = ? AND next_at = ?""",
            (int(new_next_at), now(), str(sid), int(expect_next_at)),
        )
        db.commit()
        return bool(cur.rowcount)


def claim_group(gid: str, expect_next_at: int, new_next_at: int) -> bool:
    db = init()
    with lock:
        cur = db.execute(
            """UPDATE schedule_group
                  SET last_at = next_at, next_at = ?, updated_at = ?
                WHERE id = ? AND next_at = ?""",
            (int(new_next_at), now(), str(gid), int(expect_next_at)),
        )
        db.commit()
        return bool(cur.rowcount)


def reset_next_at(sid: str, next_at: int) -> None:
    """让「立即执行一次」也能走同一条链路（把 next_at 提前到 now）"""
    db = init()
    with lock:
        db.execute(
            "UPDATE schedule SET next_at = ?, updated_at = ? WHERE id = ?",
            (int(next_at), now(), str(sid)),
        )
        db.commit()


def bump_fail(sid: str, error: str) -> None:
    db = init()
    with lock:
        db.execute(
            """UPDATE schedule SET fail_streak = fail_streak + 1, last_error = ?, updated_at = ?
                WHERE id = ?""",
            (str(error or "")[:500], now(), str(sid)),
        )
        db.commit()


def clear_fail(sid: str) -> None:
    db = init()
    with lock:
        db.execute(
            """UPDATE schedule SET fail_streak = 0, last_error = '', updated_at = ?
                WHERE id = ?""",
            (now(), str(sid)),
        )
        db.commit()


# ------------------------------------------------------------------ 组

def get_group(gid: str) -> dict | None:
    db = init()
    with lock:
        r = db.execute("SELECT * FROM schedule_group WHERE id = ?", (str(gid),)).fetchone()
    return _group_row(r) if r else None


def list_groups(user: str = DEFAULT_USER) -> list:
    db = init()
    with lock:
        rows = db.execute(
            "SELECT * FROM schedule_group WHERE user_key = ? ORDER BY created_at", (user,)
        ).fetchall()
    return [_group_row(r) for r in rows]


def save_group(data: dict, user: str = DEFAULT_USER) -> dict:
    """建/改组。`everySec` 传 None 表示「成员各用各的节奏」，组只做归类。"""
    gid = str(data.get("id") or "").strip()
    if not gid:
        import uuid
        gid = "g_" + uuid.uuid4().hex[:12]
    every = data.get("everySec", None)
    if every is not None:
        try:
            every = max(30, min(int(every), 86400))
        except Exception:
            every = None
    jitter = data.get("jitterPct", 10)
    try:
        jitter = max(0, min(int(jitter or 0), 50))
    except Exception:
        jitter = 10
    ts = now()
    db = init()
    with lock:
        old = db.execute("SELECT * FROM schedule_group WHERE id = ?", (gid,)).fetchone()
        if old:
            db.execute(
                """UPDATE schedule_group SET name = ?, every_sec = ?, jitter_pct = ?,
                                             enabled = ?, updated_at = ?
                    WHERE id = ?""",
                (str(data.get("name") or old["name"] or ""), every, jitter,
                 1 if (data.get("enabled") is None and old["enabled"]) or data.get("enabled") else 0,
                 ts, gid),
            )
        else:
            db.execute(
                """INSERT INTO schedule_group (id, user_key, name, every_sec, jitter_pct,
                                               enabled, next_at, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (gid, user, str(data.get("name") or "未命名分组"), every, jitter,
                 1 if data.get("enabled", True) else 0, ts if every else None, ts, ts),
            )
        db.commit()
    return get_group(gid)


def delete_group(gid: str) -> bool:
    """删组：成员不删，只解绑，避免「删个分组把仓库轮询也删了」"""
    db = init()
    with lock:
        db.execute("UPDATE schedule SET group_id = NULL WHERE group_id = ?", (str(gid),))
        cur = db.execute("DELETE FROM schedule_group WHERE id = ?", (str(gid),))
        db.commit()
        return bool(cur.rowcount)


# ------------------------------------------------------------------ 触发记录

def log_tick(schedule_id: str, planned_at: int, fired_at: int, outcome: str,
             reason: str = "", group_id: str | None = None, kind: str = "",
             run_id: str | None = None, source: str = "tick") -> int:
    db = init()
    with lock:
        cur = db.execute(
            """INSERT INTO tick_log (schedule_id, group_id, kind, source, planned_at, fired_at,
                                     started_at, outcome, reason, run_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (str(schedule_id), group_id, kind, str(source or "tick"), int(planned_at), int(fired_at),
             # running 也要写开始时间：手动执行先记一条 running、结束时再回填。
             # 只认 fired 会让开始时间空到 finish_tick 才补，于是「开始/结束」
             # 显示成同一秒，和 duration_ms 自相矛盾。
             int(fired_at) if outcome in ("fired", "running") else None,
             str(outcome), str(reason or "")[:500], run_id, now()),
        )
        db.commit()
        return int(cur.lastrowid)


def finish_tick(tick_id: int, outcome: str, result: dict | None = None,
                reason: str = "", duration_ms: int | None = None) -> None:
    """回填一次执行的结果——「看到每次执行的记录」靠这一步"""
    db = init()
    with lock:
        db.execute(
            """UPDATE tick_log
                  SET finished_at = ?, outcome = ?, result_json = ?, reason = ?,
                      duration_ms = ?, started_at = COALESCE(started_at, ?)
                WHERE id = ?""",
            (now(), str(outcome),
             json.dumps(result or {}, ensure_ascii=False) if result is not None else None,
             str(reason or "")[:500], duration_ms, now(), int(tick_id)),
        )
        db.commit()


def sweep_stale_ticks(stale_ms: int = 30 * 60 * 1000, reason: str = "") -> int:
    """收殓「有始无终」的记录：进程被杀时 finish_tick 来不及执行

    这类记录在库里的样子是 outcome='fired' 但 finished_at 为 NULL。留着不清
    会一直污染看板：顶部统计把它算成一次成功，流水里又显示不出来结果，用户
    只会认为「定时任务没在干活」。

    阈值给到 30 分钟：一次轮询最多几分钟，超过这个时长还没结束，基本只能是
    进程没了。留足余量也避免误伤「另一个进程正在跑的那一轮」。
    """
    stale_ms = int(stale_ms or 0)
    # stale_ms=0：无条件收殓。启动时用——那一刻上个进程已经死了，
    # 库里凡是还没结束的记录都必然是中断残留。
    cutoff = now() - stale_ms if stale_ms > 0 else now() + 1
    msg = reason or "执行被中断（进程重启或异常退出），本次结果未知"
    db = init()
    with lock:
        cur = db.execute(
            """UPDATE tick_log
                  SET finished_at = ?, outcome = 'failed', reason = ?,
                      result_json = ?, duration_ms = NULL
                WHERE finished_at IS NULL AND outcome = 'fired'
                  AND COALESCE(started_at, fired_at) < ?""",
            (now(), msg, json.dumps({"error": msg}, ensure_ascii=False), cutoff),
        )
        db.commit()
        return int(cur.rowcount or 0)


def list_ticks(schedule_id: str | None = None, limit: int = 50,
               only_comment: bool = False) -> list:
    """最近的触发记录。

    only_comment=True 时只返回「贴过评论」的轮次。这个条件必须在 SQL 里
    下推，不能取完最近 N 条再在内存里过滤——评论是稀疏事件（146 条里只有
    几条），先截断再过滤等于「最近 80 条里恰好有评论的」，用户想看的往往是
    上周贴过的那一次。
    """
    limit = max(1, min(int(limit or 50), 500))
    db = init()
    cond, params = [], []
    if schedule_id:
        cond.append("schedule_id = ?")
        params.append(str(schedule_id))
    if only_comment:
        cond.append("id IN (SELECT tick_id FROM review WHERE tick_id IS NOT NULL)")
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    params.append(limit)
    with lock:
        rows = db.execute(
            "SELECT * FROM tick_log" + where + " ORDER BY id DESC LIMIT ?", tuple(params)
        ).fetchall()
    return [_tick_row(r) for r in rows]


def tick_stats(schedule_id: str, days: int = 7) -> dict:
    """近 N 天的执行统计：成功/失败/跳过、平均耗时、最近一次。

    看板用它回答「这个定时任务稳不稳」，比逐条翻历史快。
    """
    since = now() - max(1, int(days)) * 86400 * 1000
    db = init()
    with lock:
        rows = db.execute(
            """SELECT outcome, COUNT(*) AS n, AVG(duration_ms) AS avg_ms,
                      MAX(fired_at) AS last_at
                 FROM tick_log WHERE schedule_id = ? AND fired_at >= ?
                GROUP BY outcome""",
            (str(schedule_id), since),
        ).fetchall()
    out = {"fired": 0, "skipped": 0, "failed": 0, "running": 0,
           "avgMs": 0, "lastAt": 0, "total": 0}
    weighted = 0.0
    for r in rows:
        n = int(r["n"] or 0)
        key = str(r["outcome"] or "")
        if key in out:
            out[key] = n
        out["total"] += n
        if r["avg_ms"] is not None:
            weighted += float(r["avg_ms"]) * n
        if r["last_at"] and int(r["last_at"]) > out["lastAt"]:
            out["lastAt"] = int(r["last_at"])
    out["avgMs"] = int(weighted / out["total"]) if out["total"] else 0
    return out


def tick_summary(since_ms: int | None = None) -> dict:
    """全局执行统计：页面顶部那几个数字

    与 tick_stats 的区别是「不分调度项」——用户先关心「今天一共跑了几次、
    成了几次」，再关心「哪个仓库有问题」。since_ms 为空则统计全部。
    """
    db = init()
    where, params = "", ()
    if since_ms:
        where = " WHERE fired_at >= ?"
        params = (int(since_ms),)
    with lock:
        rows = db.execute(
            "SELECT outcome, COUNT(*) AS n, AVG(duration_ms) AS avg_ms"
            " FROM tick_log" + where + " GROUP BY outcome",
            params,
        ).fetchall()
    out = {"fired": 0, "skipped": 0, "failed": 0, "running": 0,
           "avgMs": 0, "total": 0, "lastAt": 0}
    weighted = 0.0
    for r in rows:
        n = int(r["n"] or 0)
        key = str(r["outcome"] or "")
        if key in out:
            out[key] = n
        out["total"] += n
        if r["avg_ms"] is not None:
            weighted += float(r["avg_ms"]) * n
    out["avgMs"] = int(weighted / out["total"]) if out["total"] else 0
    with lock:
        row = db.execute("SELECT MAX(fired_at) AS t FROM tick_log" + where, params).fetchone()
    out["lastAt"] = int(row["t"] or 0) if row else 0
    # 「有评论」的轮次：评论是稀疏事件，单独数一次才知道这个筛选有多大的池子
    sub = (" fired_at >= ? AND " if since_ms else " ")
    with lock:
        row = db.execute(
            "SELECT COUNT(*) AS n FROM tick_log WHERE" + sub +
            "id IN (SELECT tick_id FROM review WHERE tick_id IS NOT NULL)",
            params,
        ).fetchone()
    out["commented"] = int(row["n"] or 0) if row else 0
    return out


def prune_ticks(keep_days: int = 7) -> int:
    """清掉老记录。触发记录是运维排障用的，不必留 30 天（run 才需要）"""
    cutoff = now() - max(1, int(keep_days)) * 86400 * 1000
    db = init()
    with lock:
        cur = db.execute("DELETE FROM tick_log WHERE fired_at < ?", (cutoff,))
        db.commit()
        return int(cur.rowcount or 0)


def latest_ticks() -> dict:
    """每个调度项最近一次触发，列表页用它显示「上次结果」

    一次 JOIN 拿全，避免列表页 N+1 查询。
    """
    db = init()
    with lock:
        rows = db.execute(
            """SELECT t.* FROM tick_log t
                 JOIN (SELECT schedule_id, MAX(id) AS mid FROM tick_log GROUP BY schedule_id) m
                   ON t.id = m.mid"""
        ).fetchall()
    return {_tick_row(r)["scheduleId"]: _tick_row(r) for r in rows}


def tick_stats_bulk(days: int = 7) -> dict:
    """近 N 天各调度项的成功/失败/跳过计数与平均耗时（一次聚合）"""
    since = now() - max(1, int(days)) * 86400 * 1000
    db = init()
    with lock:
        rows = db.execute(
            """SELECT schedule_id, outcome, COUNT(*) AS n, AVG(duration_ms) AS avg_ms
                 FROM tick_log WHERE fired_at >= ?
                GROUP BY schedule_id, outcome""",
            (since,),
        ).fetchall()
    out: dict = {}
    for r in rows:
        sid = r["schedule_id"]
        box = out.setdefault(sid, {"fired": 0, "skipped": 0, "failed": 0, "running": 0,
                                   "total": 0, "_weighted": 0.0})
        n = int(r["n"] or 0)
        key = str(r["outcome"] or "")
        if key in box:
            box[key] = n
        box["total"] += n
        if r["avg_ms"] is not None:
            box["_weighted"] += float(r["avg_ms"]) * n
    for box in out.values():
        box["avgMs"] = int(box["_weighted"] / box["total"]) if box["total"] else 0
        box.pop("_weighted", None)
    return out
