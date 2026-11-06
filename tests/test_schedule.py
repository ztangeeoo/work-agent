# -*- coding: utf-8 -*-
"""
定时调度回归测试（Python / 标准库 sqlite3）

覆盖：调度项自动创建 / 24 小时零漂移 / 停机只补一次 / catch_up 连补 /
确定性 jitter / 编组一起触发 / 停用成员记 skipped / 批量配置 /
CAS 防重复触发 / 执行记录回填与统计 / 立即执行。

不发起任何网络请求，也不启动轮询循环——只验证「什么时候该跑」和「记了什么」。

运行：.venv/Scripts/python.exe tests/test_schedule.py
"""

import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmpdir = tempfile.mkdtemp(prefix="sched_test_")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "interview.db")
# 跑完清掉临时库：之前每跑一次就在仓库根留一个 sched_test_* 目录。
# 必须先关连接——Windows 下 sqlite 文件被占用时 rmtree 会静默失败。
def _cleanup_tmpdir():
    try:
        store.close()
    except Exception:
        pass
    shutil.rmtree(_tmpdir, ignore_errors=True)


atexit.register(_cleanup_tmpdir)

from backend.app import store  # noqa: E402  必须在设置 DB_PATH 之后导入
from backend.app.jobs import schedule as jobs  # noqa: E402

pass_n = 0
fail_n = 0


def ok(cond, label):
    global pass_n, fail_n
    if cond:
        pass_n += 1
        print(f"  ✓ {label}")
    else:
        fail_n += 1
        print(f"  ✗ {label}")


def section(title):
    print(f"\n— {title} —")


def make_repo(name, interval=120, enabled=True):
    return store.save_repo({
        "url": "https://github.com/acme/%s" % name,
        "provider": "github", "owner": "acme", "name": name,
        "intervalSec": interval, "enabled": enabled,
    }, "local")


def main():
    store.init()

    section("建表")
    names = {r[0] for r in store.init().execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    ok({"schedule", "schedule_group", "tick_log"} <= names, "三张调度表已建")
    ok(store.tick_stats("nope")["total"] == 0, "空统计不报错")

    section("仓库自动生成调度项")
    a = make_repo("alpha")
    b = make_repo("beta")
    c = make_repo("gamma")
    scheds = store.list_schedules()
    ok(len(scheds) == 3, "三个仓库各一条调度项")
    sid_a = store.repo_schedule_id(a["id"])
    ok(store.get_schedule(sid_a)["everySec"] == 120, "节奏取自 repo.interval_sec")
    ok(store.get_schedule(sid_a)["kind"] == store.REPO_KIND, "kind 为 repo.poll")
    ok(store.get_schedule(sid_a)["scope"] == a["id"], "scope 指向仓库")

    section("算法：累加不漂移 / 停机重置 / catch_up")
    fire, nxt = jobs.advance(1_000_000, 120, 1_010_000, "skip")
    ok(fire and nxt == 1_120_000, "正常路径 next = prev + interval（不是 now + interval）")
    fire, nxt = jobs.advance(1_000_000, 120, 1_000_000 + 7200 * 1000, "skip")
    ok(fire and nxt == 1_000_000 + 7200 * 1000 + 120_000, "停机 2 小时只补一次并重置节拍")
    fire, nxt = jobs.advance(1_000_000, 120, 1_000_000 + 7200 * 1000, "all")
    ok(fire and nxt == 1_120_000, "catch_up=all 保留累加（会连续补）")
    fire, nxt = jobs.advance(1_000_000, 120, 1_060_000, "skip")
    ok(nxt == 1_120_000, "半个周期的正常延迟不算停机")

    section("确定性 jitter")
    j1 = jobs.jitter_offset("repo.poll:x", 20, 120)
    j2 = jobs.jitter_offset("repo.poll:x", 20, 120)
    j3 = jobs.jitter_offset("repo.poll:y", 20, 120)
    ok(j1 == j2, "同一个 key 偏移固定（不用随机数，避免每次重算都跳）")
    ok(0 <= j1 <= 24_000, "偏移不超过 everySec 的 20%")
    ok(jobs.jitter_offset("k", 0, 120) == 0, "jitterPct=0 时不偏移")

    section("24 小时零漂移")
    t0 = store.now() + 10_000
    fired = 0
    t = t0
    for _ in range(int(24 * 3600 / 20)):
        fired += len(jobs.collect_due(at=t))
        t += 20_000
    expect = 3 * int(24 * 3600 / 120)
    ok(fired == expect, f"24h 触发 {fired} 次，与理论值 {expect} 完全一致")
    nxts = [s["nextAt"] for s in store.list_schedules()]
    last_tick_at = t - 20_000
    ok(all(abs(n - last_tick_at) <= 120_000 for n in nxts),
       "next_at 落在最后一个 tick 的一个周期内（无累积漂移）")

    section("CAS 防重复触发")
    s = store.get_schedule(sid_a)
    ok(store.claim_schedule(sid_a, s["nextAt"], s["nextAt"] + 120_000) is True,
       "首次推进成功")
    ok(store.claim_schedule(sid_a, s["nextAt"], s["nextAt"] + 120_000) is False,
       "同一 next_at 第二次推进失败（并发抢同一个到期点只放行一个）")

    section("编组：多个仓库一起触发")
    g = store.save_group({"name": "前端三仓", "everySec": 300, "jitterPct": 0}, "local")
    ids = [store.repo_schedule_id(x["id"]) for x in (a, b, c)]
    ok(store.bind_group(ids, g["id"], "local") == 3, "三个调度项绑到同一组")
    members = store.list_by_group(g["id"])
    ok(len(members) == 3 and all(m["groupId"] == g["id"] for m in members),
       "按组分到三个成员")
    ok(len(store.due_schedules(at=t + 10_000_000)) == 0,
       "编组后的成员不再走独立节奏（避免同一次被触发两遍）")

    round_at = t + 10_000_000
    due = jobs.collect_due(at=round_at)
    ok(len(due) == 3, "组到点一次，三个成员同时触发")
    ok(all(d["groupId"] == g["id"] for d in due), "记录里带上分组 id")
    ok(len(jobs.collect_due(at=round_at + 1000)) == 0, "同一节拍不会重复触发")

    section("停用成员记 skipped")
    store.set_repo_enabled(b["id"], False, "local")
    round2 = round_at + 300_000
    due = jobs.collect_due(at=round2)
    ok(len(due) == 2, "停用的成员不执行")
    skipped = [x for x in store.list_ticks(None, 20) if x["outcome"] == "skipped"]
    ok(any("停用" in (x["reason"] or "") for x in skipped),
       "停用的成员仍留下 skipped 记录（能回答「为什么没跑」）")

    section("批量配置")
    n = store.batch_update_schedules(ids, {"everySec": 600, "jitterPct": 15}, "local")
    ok(n == 3, "一次改三个调度项")
    ok(all(s["everySec"] == 600 and s["jitterPct"] == 15 for s in store.list_schedules()),
       "批量字段全部生效")
    ok(store.batch_update_schedules([], {"everySec": 60}, "local") == 0, "空列表不报错")

    section("执行记录")
    tid = store.log_tick(sid_a, round2, round2, "fired", "", g["id"], store.REPO_KIND)
    store.finish_tick(tid, "fired", {"prs": 3, "reviewed": 1, "errors": []}, "", 1234)
    row = [x for x in store.list_ticks(sid_a, 20) if x["id"] == tid][0]
    ok(row["outcome"] == "fired" and row["durationMs"] == 1234, "回填结果与耗时")
    ok((row["result"] or {}).get("prs") == 3, "回填结构化结果")
    ok(row["finishedAt"] and row["startedAt"], "起止时间都记了（能分辨排队还是卡住）")
    ok(row["source"] == "tick", "默认来源为定时触发")

    tid2 = store.log_tick(sid_a, round2, round2, "running", "手动触发", source="manual")
    store.finish_tick(tid2, "failed", None, "缺 Token", 88)
    row2 = [x for x in store.list_ticks(sid_a, 5) if x["id"] == tid2][0]
    ok(row2["source"] == "manual" and row2["outcome"] == "failed", "手动执行可区分且能记失败")
    ok("Token" in row2["reason"], "失败原因保留")

    st = store.tick_stats(sid_a, 7)
    ok(st["total"] >= 2 and st["fired"] >= 1 and st["failed"] >= 1, "7 天统计按结果分类")
    bulk = store.tick_stats_bulk(7)
    ok(sid_a in bulk and bulk[sid_a]["total"] >= 2, "批量统计与单项一致")

    section("立即执行 / 清理")
    jobs.trigger_now(c["id"], at=round2 + 1000)
    ok(store.get_schedule(store.repo_schedule_id(c["id"]))["nextAt"] < round2 + 1000,
       "trigger_now 把 next_at 提前，下一轮 tick 立刻捞到")
    ok(store.delete_group(g["id"]) is True, "删组")
    ok(len(store.list_by_group(g["id"])) == 0, "成员被解绑而不是被删")
    ok(store.get_schedule(sid_a) is not None, "删组后调度项还在")
    store.delete_repo(c["id"], "local")
    ok(store.get_schedule(store.repo_schedule_id(c["id"])) is None, "删仓库连带删调度项")

    section("同仓库重复触发不留空记录")
    # 场景：上一轮还在跑，本轮又到期。调度层已经写了一条 fired 记录，
    # 若 poll_repo 复用已有 Task 时直接 return，那条记录会永远停在
    # result=NULL / finished_at=NULL，看板上显示「成功但没有任何结果」。
    import asyncio as _asyncio

    from backend.app.services.poller import ReviewPoller

    async def _dup_tick():
        p = ReviewPoller(get_model_cfg=lambda: {}, review_fn=None, on_event=None, log=lambda m: None)
        d = make_repo("dup-tick", interval=60)
        jobs.ensure_repo_schedules(store.list_all_repos())
        sid = store.repo_schedule_id(d["id"])
        ts = store.now()

        # 占住 _inflight，模拟「上一轮还没跑完」
        p._inflight[d["id"]] = _asyncio.create_task(_asyncio.sleep(0.08))
        t2 = store.log_tick(sid, ts, ts, "fired", "", kind=store.REPO_KIND, source="tick")
        try:
            await p.poll_repo(d["id"], force=False, tick_id=t2)
        except Exception:
            pass
        return sid, t2

    sid_dup, t2 = _asyncio.run(_dup_tick())
    rows = {t["id"]: t for t in store.list_ticks(sid_dup, 20)}
    dup = rows.get(t2) or {}
    ok(dup.get("finishedAt") is not None, "复用执行的触发记录补上了结束时间")
    ok(bool(dup.get("result")), "复用执行的触发记录写入了结果，而不是空着")
    ok(dup.get("outcome") == "skipped", "复用被标成 skipped，不虚构一次成功")
    ok(not any(r["outcome"] == "fired" and not r.get("result") and r.get("finishedAt") is None
               for r in rows.values()), "同仓库没有留下「已触发但无结果」的记录")

    section("僵尸记录清扫")
    # 进程被杀时 finish_tick 来不及跑，库里会留下 fired 且 finished_at 为空的记录。
    # 不清掉会一直挂在看板上：算成功，又没有结果。
    zombie = store.log_tick(sid_dup, store.now(), store.now(), "fired", "", kind=store.REPO_KIND, source="tick")
    old = store.now() - 60 * 60 * 1000
    _db = store.init()
    _db.execute("UPDATE tick_log SET fired_at = ?, started_at = ? WHERE id = ?", (old, old, zombie))
    _db.commit()
    n = store.sweep_stale_ticks()
    ok(n >= 1, "清扫扫到了无结束时间的记录")
    z = {t["id"]: t for t in store.list_ticks(sid_dup, 50)}.get(zombie) or {}
    ok(z.get("finishedAt") is not None, "僵尸记录被补上结束时间")
    ok(z.get("outcome") == "failed", "僵尸记录标成 failed，不再冒充成功")
    ok("中断" in (z.get("reason") or ""), "僵尸记录写明了中断原因")
    # 刚写下的纪录不能被误伤
    fresh = store.log_tick(sid_dup, store.now(), store.now(), "fired", "", kind=store.REPO_KIND, source="tick")
    store.sweep_stale_ticks()
    f = {t["id"]: t for t in store.list_ticks(sid_dup, 50)}.get(fresh) or {}
    ok(f.get("finishedAt") is None, "刚写下、可能正在跑的记录不被误清扫")

    section("有评论的筛选")
    # 「这一轮贴了评论」是稀疏事件，筛选必须在 SQL 里下推：
    # 先取最近 N 条再内存过滤，会把更早贴过评论的轮次全丢掉。
    r1 = make_repo("cmt-demo")
    store.upsert_repo_schedule(r1, sync_interval=True)
    sid_c = store.repo_schedule_id(r1["id"])
    plain = []
    for _ in range(2):
        plain.append(store.log_tick(sid_c, store.now(), store.now(), "fired", "", kind=store.REPO_KIND))
        store.finish_tick(plain[-1], "fired", {"prs": 1, "reviewed": 0, "errors": []})
    with_cmt = store.log_tick(sid_c, store.now(), store.now(), "fired", "", kind=store.REPO_KIND)
    store.finish_tick(with_cmt, "fired", {"prs": 1, "reviewed": 1, "errors": []})
    rev = store.save_review({
        "repoId": r1["id"], "prNumber": 9, "headSha": "sha-cmt",
        "risk": "major", "decision": "comment", "summary": "有两条 major",
        "issues": [{"level": "major"}], "issueCount": 1, "tickId": with_cmt,
    })
    store.mark_review_posted(rev["id"], posted=True, tick_id=with_cmt)

    all_rows = store.list_ticks(sid_c, 50)
    only_cmt = store.list_ticks(sid_c, 50, only_comment=True)
    ok(len(only_cmt) == 1, "只筛出贴过评论的那一轮")
    ok(only_cmt[0]["id"] == with_cmt, "筛出的是正确的那条记录")
    ok(len(all_rows) >= 3, "不筛选时记录还在")
    ok(all(x["id"] != with_cmt for x in all_rows if x["id"] in plain),
       "没贴评论的轮次不会被算进来")

    pm = store.posted_map([with_cmt, plain[0]])
    ok(with_cmt in pm and len(pm[with_cmt]) == 1, "posted_map 按 tick 聚到评论")
    ok(plain[0] not in pm, "没评论的 tick 不出现在 posted_map 里")
    ok(pm[with_cmt][0]["prNumber"] == 9, "评论记录带着 PR 号")
    ok(pm[with_cmt][0]["issueCount"] == 1, "评论记录带着问题数")

    s_today = store.tick_summary(None)
    ok(s_today.get("commented", 0) >= 1, "全局摘要里有「有评论」的轮次数")

    # 稀疏性：先截断再过滤会漏。构造 60 条无评论的新记录压在老的评论记录上，
    # limit=30 时如果不把条件下推，老的那条就再也查不出来了。
    for _ in range(60):
        t = store.log_tick(sid_c, store.now(), store.now(), "fired", "", kind=store.REPO_KIND)
        store.finish_tick(t, "fired", {"prs": 0})
    got = store.list_ticks(sid_c, 30, only_comment=True)
    ok(any(x["id"] == with_cmt for x in got),
       "limit 小于总量时，早期的评论记录仍能被筛出来（条件下推）")

    # 手动执行要先写 running、再回填；只认 fired 会让开始时间晚于真实开始，
    # 详情里「开始 / 结束」显示成同一秒却带着 90 秒的耗时。
    t_manual = store.log_tick(sid_c, store.now(), store.now(), "running", "", source="manual")
    row = {x["id"]: x for x in store.list_ticks(sid_c, 10)}.get(t_manual) or {}
    ok(row.get("startedAt") is not None, "手动执行的 running 记录一开始就有开始时间")
    ok(row.get("startedAt") == row.get("firedAt"), "开始时间等于触发时刻")

    print(f"\n通过 {pass_n} / {pass_n + fail_n}")
    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
