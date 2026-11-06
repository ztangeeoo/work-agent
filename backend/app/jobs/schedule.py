# -*- coding: utf-8 -*-
"""定时调度核心：什么时候该触发、下一次落在几点

这里只有算法，不执行任何业务。执行由调用方（当前是 services/poller.py）负责，
跑完回填 tick_log。这样 jobs 层可以纯单测——给定一串时间戳，验证触发次数和
下一次时间，不需要起服务、不需要网络。

四个决定长期行为的规则
  1. 累加而非重算：next = prev + interval，不是 now + interval。
     后者会把每次 tick 的判定延迟（最多 TICK_SEC 秒）累加进去，
     一天偏几分钟，一年偏十几小时。
  2. 落后 1.5 个周期以上则重置为 now + interval。
     服务停机两天再启动，next_at 还停在两天前，不重置会连补几百次。
  3. jitter 用哈希而不是随机数：同一个调度项每次的偏移固定，
     否则每次重算都跳，某些项会长期抢跑或长期垫底。
     只往后偏不往前，避免提前触发导致节拍持续左移。
  4. 组是节拍器：组到点，组内成员本轮全部触发（这是「一起执行」），
     成员 next_at 直接对齐到组的新节拍，不各自累积误差。
"""

import hashlib

from .. import store
from ..store import DEFAULT_USER
from ..store.schedule import REPO_KIND, repo_schedule_id

# 落后超过「1.5 个周期」视为错过：正常 tick 延迟（<50%）不该被当成停机
CATCHUP_FACTOR = 1.5


# ------------------------------------------------------------------ 纯算法

def interval_ms(every_sec) -> int:
    try:
        sec = int(every_sec or 120)
    except Exception:
        sec = 120
    return max(30, min(sec, 86400)) * 1000


def jitter_offset(key: str, jitter_pct, every_sec) -> int:
    """确定性错开：同一 key 每次偏移相同，范围 [0, everySec * jitterPct%]"""
    try:
        pct = max(0, min(int(jitter_pct or 0), 50))
    except Exception:
        pct = 0
    if pct <= 0:
        return 0
    span = int(interval_ms(every_sec) * pct / 100)
    if span <= 0:
        return 0
    h = hashlib.md5(str(key or "").encode("utf-8")).hexdigest()
    return int(h[:8], 16) % (span + 1)


def advance(prev_next_at: int, every_sec, at: int, catch_up: str = "skip"):
    """算出本轮是否触发 + 新的 next_at

    返回 (should_fire, new_next_at)。

    catch_up 语义（三种，实际只有两档行为）：
      skip / once —— 错过就补一次，然后对齐到 now + interval。
                     两者的区别只在文案：skip 是「丢掉的不管了」，
                     once 是「错过的合成一次」。因为一次 tick 只能触发一次，
                     实现上等价；不必为了区分而多存状态。
      all         —— 保留累加，下一轮若仍 <= now 会继续补，直到追上。
                     停机很久后可能连续触发多次，只适合必须逐次补齐的任务。
    """
    prev = int(prev_next_at or 0)
    step = interval_ms(every_sec)
    nxt = prev + step

    if str(catch_up or "skip") == "all":
        return True, nxt          # 连补：落后就让它落后，一轮一轮追

    if at - prev >= step * CATCHUP_FACTOR:
        nxt = at + step           # 真的错过了：补这一次，节拍从此刻重新起算
    return True, nxt


def due_plan(prev_next_at: int, every_sec, at: int, catch_up: str = "skip",
             jitter_pct=0, key: str = "") -> tuple:
    """advance + jitter 的组合，独立节奏的调度项走这个"""
    fire, nxt = advance(prev_next_at, every_sec, at, catch_up)
    return fire, nxt + jitter_offset(key, jitter_pct, every_sec)


# ------------------------------------------------------------------ 采集到期项

def collect_due(at: int | None = None, user: str = DEFAULT_USER) -> list:
    """扫一轮：返回本轮该触发的调度项，并已写好 tick_log

    调用方拿到列表后逐个执行（受全局槽位限制），完成后调用 store.finish_tick
    回填结果。这里先落 tick_log 的原因：哪怕执行时进程被杀，也能看到
    「几点触发过、结果未知」，而不是凭空消失。

    返回 [{"schedule": dict, "tickId": int, "plannedAt": int, "groupId": str|None}]
    """
    ts = store.now() if at is None else int(at)
    out: list = []

    # ---- 1) 组：一次 claim，成员本轮全部触发（「多个仓库一起执行」）
    for g in store.due_groups(ts, user):
        members = store.list_by_group(g["id"])
        _, g_next = advance(g["nextAt"], g["everySec"], ts, "skip")
        if not store.claim_group(g["id"], g["nextAt"], g_next):
            continue                      # 被另一个触发者抢走了，本轮不管
        for m in members:
            if not m["enabled"]:
                store.log_tick(m["id"], m["nextAt"] or g["nextAt"], ts, "skipped",
                               "调度项已停用", g["id"], m["kind"])
                continue
            planned = int(m["nextAt"] or g["nextAt"])
            # 成员与组对齐：直接对齐到组的新节拍，不各自累加（否则越跑越散）
            store.reset_next_at(m["id"], g_next)
            tid = store.log_tick(m["id"], planned, ts, "fired", "", g["id"], m["kind"])
            out.append({"schedule": m, "tickId": tid, "plannedAt": planned, "groupId": g["id"]})

    # ---- 2) 独立节奏
    for s in store.due_schedules(ts, user):
        fire, nxt = due_plan(s["nextAt"], s["everySec"], ts, s["catchUp"],
                             s["jitterPct"], s["id"])
        if not store.claim_schedule(s["id"], s["nextAt"], nxt):
            continue
        if not fire:
            store.log_tick(s["id"], s["nextAt"], ts, "skipped", "catch_up=skip 且已错过", None, s["kind"])
            continue
        tid = store.log_tick(s["id"], s["nextAt"], ts, "fired", "", None, s["kind"])
        out.append({"schedule": s, "tickId": tid, "plannedAt": s["nextAt"], "groupId": None})

    return out


# ------------------------------------------------------------------ 仓库接入

def ensure_repo_schedules(repos: list | None = None, user: str = DEFAULT_USER) -> int:
    """把仓库配置同步成调度项（老库迁移 / 新仓库创建后调用）

    单向读：只读 repo.interval_sec，不回写。已经存在的调度项只同步 enabled，
    不动节奏——库里可能被人手工调过，启动时的同步不该把它抹掉。
    """
    if repos is None:
        repos = store.list_all_repos()
    n = 0
    for r in repos:
        try:
            store.upsert_repo_schedule(r, user)
            n += 1
        except Exception:
            continue                      # 单个仓库失败不影响其他
    return n


def schedule_of_repo(repo_id: str) -> dict | None:
    return store.get_schedule(repo_schedule_id(repo_id))


def trigger_now(repo_id: str, at: int | None = None) -> dict | None:
    """手动「立即执行」：把 next_at 提前到此刻，让下一轮 tick 立刻捞到

    不自建一条执行路径——手动和定时走同一条链路，执行记录才会一致，
    否则「手动跑的那几次」在历史里要么缺失、要么格式不同。
    """
    sid = repo_schedule_id(repo_id)
    s = store.get_schedule(sid)
    if not s:
        return None
    store.reset_next_at(sid, (store.now() if at is None else int(at)) - 1)
    return store.get_schedule(sid)


__all__ = [
    "REPO_KIND",
    "CATCHUP_FACTOR",
    "advance",
    "collect_due",
    "due_plan",
    "ensure_repo_schedules",
    "interval_ms",
    "jitter_offset",
    "schedule_of_repo",
    "trigger_now",
]
