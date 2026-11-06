# -*- coding: utf-8 -*-
"""
代码审核 · 轮询调度器（Python / asyncio）

每个仓库按自己的 intervalSec 到期触发：拉 PR → 发现未审的 head →
交给三智能体审核流水线（由 main 注入的 review_fn）→ 落库 → 广播事件。

关键取舍
  - 同仓库串行：避免同一个 PR 被并发审两遍、也避免刷爆模型额度
  - 单轮最多审 MAX_REVIEWS_PER_POLL 个，剩下的下一轮继续（积压不丢）
  - 模型配置从页面内存配置读取；没配就只轮询不审核，并把原因写回仓库状态
"""

import asyncio
import os
import time

from .. import notify, store
from ..jobs import schedule as jobs
from ..utils.render import render_comment_body, scout_intent
from . import notify_event, review_client as rv

TICK_SEC = 20  # 后台巡检间隔：谁到期谁跑

# 调度开关：置 0 完全回到「lastPollAt + intervalSec」的老判定，用于回退排查。
# 两条路径共用 poll_repo，所以执行记录、审批评分逻辑都不分叉。
SCHED_ENABLED = os.environ.get("SCHED_ENABLED", "1") != "0"

# 整轮审核（侦察官 → 审核官 → 裁决官）的硬上限。
# 单次模型调用已有 180s 超时，但三环节串起来 + 重试仍可能把一次审核拖到十几分钟；
# 没有这个上限时，前端会永远停在「审核中」（事件的终态永远等不到）。
REVIEW_TIMEOUT_SEC = 600


def _now_ms() -> int:
    return int(time.time() * 1000)


class ReviewPoller:
    def __init__(self, get_model_cfg, review_fn, on_event=None, log=None):
        """review_fn(payload) -> awaitable {report, reportMd, model}（由 main 注入，避免循环依赖）"""
        self._get_model_cfg = get_model_cfg
        self._review_fn = review_fn
        self._on_event = on_event
        self._log = log
        self._inflight: dict[str, asyncio.Task] = {}
        self._loop_task: asyncio.Task | None = None
        # 当前正在执行的那条 tick 记录：review_one 要给 review.tick_id 填上，
        # 才能精确回答「这条评论是哪一轮贴出去的」。同仓库串行，所以单值够用。
        self._tick_ctx: dict[str, int] = {}
        # 运行时状态（只存在进程内存，不进库）：repo_id -> {polling, phase, reviewingPr, startedAt}
        # 页面靠它显示「这一刻到底有没有在轮询」，避免只能看到上一次轮询的时间
        self._runtime: dict[str, dict] = {}

    # ---------------------------------------------------------------- 运行时状态

    def _rt(self, repo_id: str) -> dict:
        return self._runtime.setdefault(str(repo_id), {
            "polling": False, "phase": "", "reviewingPr": None, "startedAt": 0,
        })

    def mark(self, repo_id: str, **kw):
        self._rt(repo_id).update(kw)

    def runtime_status(self, repo_id: str | None = None):
        """单个仓库或全部仓库的轮询运行时状态"""
        if repo_id is None:
            return {k: dict(v) for k, v in self._runtime.items()}
        return dict(self._rt(str(repo_id)))

    @property
    def scheduler_running(self) -> bool:
        """后台调度循环是否活着（进程起来了但循环挂了 = 不会自动轮询）"""
        return bool(self._loop_task and not self._loop_task.done())

    @property
    def tick_sec(self) -> int:
        return TICK_SEC

    # ---------------------------------------------------------------- 工具

    def emit(self, ev: dict):
        if not self._on_event:
            return
        try:
            self._on_event({**ev, "ts": _now_ms()})
        except Exception:
            pass  # 广播失败不影响轮询

    def log_line(self, msg: str):
        if self._log:
            self._log(msg)
        else:
            print("[review] " + msg, flush=True)

    def _require_cfg(self) -> dict:
        cfg = self._get_model_cfg() or {}
        if not cfg.get("base") or not cfg.get("model"):
            raise RuntimeError("未配置模型：请先在页面右上角齿轮里完成「模型配置」，否则只轮询不审核")
        return cfg

    @staticmethod
    def _build_comment_body(review: dict) -> str:
        """回帖正文：@ 开发人 + 结论横幅 + 报告（开头含「本次提交做了什么」）"""
        return render_comment_body(review)

    # ---------------------------------------------------------------- 审核单个 PR

    async def review_one(self, repo: dict, pr: dict, cfg: dict, tick_id: int | None = None) -> dict:
        self.emit({"type": "review-start", "repoId": repo["id"], "prNumber": pr["number"], "title": pr.get("title")})
        tid = tick_id if tick_id is not None else self._tick_ctx.get(str(repo["id"]))

        diff = await rv.fetch_pr_diff(repo, pr["number"])
        if not str(diff or "").strip():
            raise RuntimeError("这个 PR 没有文本改动（可能全是二进制或空提交），跳过")

        try:
            result = await self._review_fn({
                "base": cfg.get("base"),
                "key": cfg.get("key") or "",
                "model": cfg.get("model"),
                "pr": {
                    "repo": repo["owner"] + "/" + repo["name"],
                    "number": pr["number"],
                    "title": pr.get("title"),
                    "author": pr.get("author"),
                    "url": pr.get("url"),
                    "baseBranch": pr.get("baseBranch"),
                    "headBranch": pr.get("headBranch"),
                },
                "diff": diff,
            })
        except Exception as e:
            raise RuntimeError("审核失败：" + str(e))

        if not isinstance(result, dict) or not result.get("report"):
            raise RuntimeError("审核失败：智能体没有返回报告")

        report = result.get("report") or {}
        issues = report.get("issues") if isinstance(report.get("issues"), list) else []
        review = store.save_review({
            "repoId": repo["id"],
            "prNumber": pr["number"],
            "headSha": pr.get("headSha") or "",
            "risk": report.get("risk"),
            "decision": report.get("decision"),
            "summary": report.get("summary"),
            "reportMd": result.get("reportMd") or report.get("reportMd") or "",
            "issues": issues,
            "issueCount": len(issues),
            "stages": report.get("stages") or [],
            "model": result.get("model") or cfg.get("model"),
            "tickId": tid,
            # 功能与代码量：群消息要「这次改了什么、改了多少」，
            # 事后从 reportMd 里再抠一遍不可靠，落库时顺手存下。
            "feature": scout_intent(report.get("scout") or ""),
            "stats": report.get("stats") or {},
        })

        # 可选：把意见回帖到 PR（默认关闭，避免污染别人的仓库）
        posted_ok = False
        if repo.get("autoPost"):
            try:
                if not repo.get("token"):
                    raise RuntimeError("未配置 Token，无法回帖")
                await rv.post_pr_comment(repo, pr["number"], self._build_comment_body(review))
                review = store.mark_review_posted(review["id"], posted=True, tick_id=tid)
                posted_ok = True
                self.emit({"type": "review-posted", "repoId": repo["id"], "prNumber": pr["number"], "reviewId": review["id"]})
            except Exception as e:
                review = store.mark_review_posted(review["id"], posted=False, error=str(e))
                self.emit({"type": "review-error", "repoId": repo["id"], "prNumber": pr["number"], "error": "回帖失败：" + str(e)})

        # 通知放在 try 外面：回帖已经成功了，消息发不出去不该把这条评论
        # 改写成「回帖失败」——那会让页面显示的状态与事实相反。
        if posted_ok:
            await self._notify(repo, pr, review, trigger="auto")

        self.emit({"type": "review-done", "repoId": repo["id"], "prNumber": pr["number"], "review": review})
        self.log_line(f"PR #{pr['number']} 审核完成 · 风险 {report.get('risk') or '-'} · {len(issues)} 处问题")
        return review

    async def _notify(self, repo: dict, pr: dict, review: dict,
                      trigger: str = "auto") -> dict:
        """回帖后把这条审核结果发给所有启用的通知渠道

        为什么不再直接调 wecom：渠道是可插拔的（企业微信 / 钉钉 / 内部 webhook），
        审核流程不该知道「发到哪儿」。这里只拼事件、调派发器。

        失败只记日志、发事件，不往上抛：审核与回帖都已经完成，
        群机器人 key 过期不该让整轮执行记成失败。
        """
        event = notify_event.build(pr, review or {}, repo=repo, trigger=trigger)
        res = await notify.dispatch(event)
        if res.get("ok"):
            got = "、".join(s["name"] + f"({s['ms']}ms)" for s in res["sent"] if s["ok"])
            self.log_line(f"PR #{pr['number']} 已推送：{got}")
        else:
            self.log_line(f"PR #{pr['number']} 推送失败：{res.get('error')}")
            self.emit({"type": "review-error", "repoId": repo["id"],
                       "prNumber": pr["number"],
                       "error": "消息推送失败：" + str(res.get("error"))})
        return res

    # ---------------------------------------------------------------- 轮询一个仓库

    async def _poll_repo_inner(self, repo_id: str, force: bool) -> dict:
        repo = store.get_repo_by_id(repo_id, with_token=True)
        if not repo:
            raise RuntimeError("仓库不存在")
        if not repo.get("enabled") and not force:
            return {"skipped": "仓库已停用"}

        self.emit({"type": "poll-start", "repoId": repo["id"]})
        self.mark(repo["id"], polling=True, phase="listing", reviewingPr=None, startedAt=_now_ms())
        try:
            prs = await rv.list_open_prs(repo, limit=20)
            for pr in prs:
                store.upsert_pr(repo["id"], pr)
            try:
                store.set_repo_provider(repo["id"], repo.get("provider") or "")
            except Exception:
                pass
            store.touch_repo(repo["id"], at=_now_ms(), error="")
            self.emit({"type": "poll-done", "repoId": repo["id"], "count": len(prs)})
            self.log_line(f"{repo['owner']}/{repo['name']} 轮询到 {len(prs)} 个开放 PR")
        except Exception as e:
            store.touch_repo(repo["id"], at=_now_ms(), error=str(e))
            self.emit({"type": "poll-error", "repoId": repo["id"], "error": str(e)})
            raise

        errors: list[str] = []
        reviewed = 0
        posted = 0
        pending: list = []
        notes: list[str] = []
        cfg = None
        try:
            cfg = self._require_cfg()
        except Exception as e:
            errors.append(str(e))
            notes.append("未配置模型，本轮只拉取 PR 不审核")

        if cfg:
            pending = [p for p in prs if not p.get("draft") and not store.find_review(repo["id"], p["number"], p.get("headSha") or "")]
            for pr in pending[:rv.MAX_REVIEWS_PER_POLL]:
                try:
                    self.mark(repo["id"], phase="reviewing", reviewingPr=pr["number"])
                    one = await asyncio.wait_for(
                        self.review_one(repo, pr, cfg), timeout=REVIEW_TIMEOUT_SEC)
                    reviewed += 1
                    if isinstance(one, dict) and one.get("posted"):
                        posted += 1
                except asyncio.TimeoutError:
                    msg = "审核超时：超过 %d 分钟没有结果，模型接口可能没响应" % (REVIEW_TIMEOUT_SEC // 60)
                    errors.append(f"#{pr['number']} {msg}")
                    self.emit({"type": "review-error", "repoId": repo["id"], "prNumber": pr["number"], "error": msg})
                except Exception as e:
                    errors.append(f"#{pr['number']} {e}")
                    self.emit({"type": "review-error", "repoId": repo["id"], "prNumber": pr["number"], "error": str(e)})
                finally:
                    self.mark(repo["id"], phase="listing", reviewingPr=None)
            if len(pending) > reviewed:
                self.log_line(f"还有 {len(pending) - reviewed} 个 PR 待下轮审核")
            # 「审核 0 个」不等于没干活：开放 PR 都已按 headSha 审过，本轮无事可做。
            # 不写清楚的话看板上就是一条「发现了 PR 却一个没审」的怪记录。
            if prs and not pending:
                notes.append("%d 个开放 PR 都已审过（提交未变），本轮无需重审" % len(prs))

        if errors:
            store.touch_repo(repo["id"], at=_now_ms(), error="；".join(errors))
        return {"prs": len(prs), "reviewed": reviewed, "posted": posted, "errors": errors,
                "pending": len(pending), "note": "；".join(notes)}

    async def poll_repo(self, repo_id: str, force: bool = False,
                        tick_id: int | None = None, source: str = "tick") -> dict:
        """轮询一个仓库；同仓库并发调用会复用同一个 Task

        tick_id：调度层已经写好触发记录时传进来，执行完回填那一行；
                 不传就自己建一条（手动「立即执行」走这条）。
                 两条入口最终落在同一张 tick_log，历史里用 source 区分。

        复用已有 Task 时不另建记录——否则「点了三次立即执行」会出现三条
        记录但只有一次真正拉取，看起来像重复执行。
        """
        key = str(repo_id)
        sid = store.repo_schedule_id(key)
        task = self._inflight.get(key)
        if task and not task.done():
            # 已有同仓库任务在跑：等它的结果，但不能把本次的触发记录空着。
            # 调度层在 collect_due 里已经写了一条 fired，直接 return 会让那条
            # 记录永远停在 result=NULL / finished_at=NULL——看板上就是一条
            # 「成功了但没有任何结果」的怪记录。
            t0 = _now_ms()
            try:
                result = await task
            except Exception as e:
                try:
                    store.finish_tick(tick_id, "skipped", {"skipped": "同仓库已有轮询在执行，本次未重复拉取"},
                                      "同仓库已有轮询在执行，本次未重复拉取", _now_ms() - t0)
                except Exception:
                    pass
                raise
            try:
                store.finish_tick(tick_id, "skipped",
                                  {"skipped": "同仓库已有轮询在执行，本次复用其结果", "prs": (result or {}).get("prs")},
                                  "同仓库已有轮询在执行，本次复用其结果", _now_ms() - t0)
            except Exception:
                pass
            return result

        own = tick_id is None
        if own:
            ts = _now_ms()
            tick_id = store.log_tick(sid, ts, ts, "running", "", kind=store.REPO_KIND, source=source)
        t0 = _now_ms()
        self._tick_ctx[key] = int(tick_id)
        task = asyncio.create_task(self._poll_repo_inner(key, force))
        self._inflight[key] = task
        try:
            result = await task
            self._mark_tick(sid, tick_id, result, t0)
            return result
        except Exception as e:
            try:
                store.finish_tick(tick_id, "failed", None, str(e), duration_ms=_now_ms() - t0)
                store.bump_fail(sid, str(e))
            except Exception:
                pass
            raise
        finally:
            self._tick_ctx.pop(key, None)
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)
            self.mark(key, polling=False, phase="", reviewingPr=None, startedAt=0)

    @staticmethod
    def _mark_tick(sid: str, tick_id: int, result, t0: int) -> None:
        """把一次执行的结果写回触发记录

        拉 PR 失败会抛异常（上面按 failed 处理）；审核单个 PR 失败只是
        `errors` 里的一项业务结果，算 fired 但记下原因——否则一个二进制 diff
        就能把整个调度项的失败计数拉满。
        """
        try:
            duration = _now_ms() - t0
            if isinstance(result, dict) and result.get("skipped"):
                store.finish_tick(tick_id, "skipped", result, str(result["skipped"]), duration)
                return
            errors = (result or {}).get("errors") if isinstance(result, dict) else None
            reason = "；".join(str(x) for x in errors)[:400] if errors else ""
            store.finish_tick(tick_id, "fired", result if isinstance(result, dict) else {},
                              reason, duration)
            store.clear_fail(sid)
        except Exception:
            pass  # 记录失败不能影响主流程

    async def review_pr(self, repo_id: str, pr_number) -> dict:
        """立即审核某个已知 PR（不等待下一轮）

        注意：这里必须发 review-error。前端收到 review-start 后就把界面切成「审核中」，
        只有 review-done / review-error 才会切回来；手动审核以前直接把异常抛给 HTTP 路由，
        一旦请求被浏览器中断（刷新、切页、超时），界面就永远卡在「审核中」。
        """
        repo = store.get_repo_by_id(str(repo_id), with_token=True)
        if not repo:
            raise RuntimeError("仓库不存在")
        pr = store.get_pr(repo["id"], pr_number)
        if not pr:
            raise RuntimeError("PR 不存在，请先轮询一次")
        cfg = self._require_cfg()
        self.mark(repo["id"], polling=True, phase="reviewing", reviewingPr=pr["number"], startedAt=_now_ms())
        try:
            return await asyncio.wait_for(
                self.review_one(repo, pr, cfg), timeout=REVIEW_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            msg = "审核超时：超过 %d 分钟没有结果，模型接口可能没响应（可先在齿轮里点「测试连接」）" % (REVIEW_TIMEOUT_SEC // 60)
            self.emit({"type": "review-error", "repoId": repo["id"], "prNumber": pr["number"], "error": msg})
            raise RuntimeError(msg)
        except Exception as e:
            self.emit({"type": "review-error", "repoId": repo["id"], "prNumber": pr["number"], "error": str(e)})
            raise
        finally:
            self.mark(repo["id"], polling=False, phase="", reviewingPr=None, startedAt=0)

    # ---------------------------------------------------------------- 后台调度

    async def _loop(self):
        mode = "调度表 next_at" if SCHED_ENABLED else "lastPollAt + interval（兼容模式）"
        self.log_line(f"轮询调度已启动（每 {TICK_SEC}s 检查一次到期仓库 · {mode}）")
        rounds = 0
        while True:
            try:
                await asyncio.sleep(TICK_SEC)
            except asyncio.CancelledError:
                return
            rounds += 1
            try:
                if SCHED_ENABLED:
                    await self._tick_schedule(rounds)
                else:
                    await self._tick_legacy()
            except Exception as e:
                self.log_line("调度扫描失败：" + str(e))

    async def _tick_schedule(self, rounds: int) -> None:
        """新调度：判定交给 jobs.collect_due，这里只负责把到期项跑起来

        每 30 轮（约 10 分钟）补一次调度项，兜住「启动时初始化失败」和
        「绕过接口直接写库」这两种漏网情况。不每轮做是因为它会遍历全部仓库
        并写库，20s 一次没必要。
        """
        if rounds % 30 == 1:
            try:
                jobs.ensure_repo_schedules(store.list_all_repos())
            except Exception as e:
                self.log_line("调度项同步失败：" + str(e))

        due = jobs.collect_due()
        for item in due:
            task = asyncio.create_task(self._run_tick(item))
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def _run_tick(self, item: dict) -> None:
        """执行一条已记好触发记录的调度项；结果由 poll_repo 回填"""
        scope = (item.get("schedule") or {}).get("scope")
        if not scope:
            return
        try:
            await self.poll_repo(scope, force=False, tick_id=item.get("tickId"))
        except Exception:
            pass  # poll_repo 内部已把失败原因写进 tick_log

    async def _tick_legacy(self) -> None:
        """兼容模式：完全按改动前的 lastPollAt + intervalSec 判定

        保留它是为了 SCHED_ENABLED=0 时能一键退回；执行记录照写（poll_repo
        自己建 tick），所以回退之后看板不会突然变空。
        """
        repos = store.list_all_repos()
        now = _now_ms()
        for r in repos:
            if not r.get("enabled"):
                continue
            due = (not r.get("lastPollAt")) or (now - int(r["lastPollAt"]) >= int(r.get("intervalSec") or 120) * 1000)
            if due:
                asyncio.create_task(self.poll_repo(r["id"], force=False))

    def start(self):
        if self._loop_task and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
        self._loop_task = None


def create_review_poller(get_model_cfg, review_fn, on_event=None, log=None) -> ReviewPoller:
    return ReviewPoller(get_model_cfg, review_fn, on_event=on_event, log=log)
