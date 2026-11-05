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
import time

from .. import store
from ..utils.render import render_comment_body
from . import review_client as rv

TICK_SEC = 20  # 后台巡检间隔：谁到期谁跑

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

    async def review_one(self, repo: dict, pr: dict, cfg: dict) -> dict:
        self.emit({"type": "review-start", "repoId": repo["id"], "prNumber": pr["number"], "title": pr.get("title")})

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
        })

        # 可选：把意见回帖到 PR（默认关闭，避免污染别人的仓库）
        if repo.get("autoPost"):
            try:
                if not repo.get("token"):
                    raise RuntimeError("未配置 Token，无法回帖")
                await rv.post_pr_comment(repo, pr["number"], self._build_comment_body(review))
                review = store.mark_review_posted(review["id"], posted=True)
                self.emit({"type": "review-posted", "repoId": repo["id"], "prNumber": pr["number"], "reviewId": review["id"]})
            except Exception as e:
                review = store.mark_review_posted(review["id"], posted=False, error=str(e))
                self.emit({"type": "review-error", "repoId": repo["id"], "prNumber": pr["number"], "error": "回帖失败：" + str(e)})

        self.emit({"type": "review-done", "repoId": repo["id"], "prNumber": pr["number"], "review": review})
        self.log_line(f"PR #{pr['number']} 审核完成 · 风险 {report.get('risk') or '-'} · {len(issues)} 处问题")
        return review

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
        cfg = None
        try:
            cfg = self._require_cfg()
        except Exception as e:
            errors.append(str(e))

        if cfg:
            pending = [p for p in prs if not p.get("draft") and not store.find_review(repo["id"], p["number"], p.get("headSha") or "")]
            for pr in pending[:rv.MAX_REVIEWS_PER_POLL]:
                try:
                    self.mark(repo["id"], phase="reviewing", reviewingPr=pr["number"])
                    await asyncio.wait_for(
                        self.review_one(repo, pr, cfg), timeout=REVIEW_TIMEOUT_SEC)
                    reviewed += 1
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

        if errors:
            store.touch_repo(repo["id"], at=_now_ms(), error="；".join(errors))
        return {"prs": len(prs), "reviewed": reviewed, "errors": errors}

    async def poll_repo(self, repo_id: str, force: bool = False) -> dict:
        """轮询一个仓库；同仓库并发调用会复用同一个 Task"""
        key = str(repo_id)
        task = self._inflight.get(key)
        if task and not task.done():
            return await task
        task = asyncio.create_task(self._poll_repo_inner(key, force))
        self._inflight[key] = task
        try:
            return await task
        finally:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)
            self.mark(key, polling=False, phase="", reviewingPr=None, startedAt=0)

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
        self.log_line(f"轮询调度已启动（每 {TICK_SEC}s 检查一次到期仓库）")
        while True:
            try:
                await asyncio.sleep(TICK_SEC)
            except asyncio.CancelledError:
                return
            try:
                repos = store.list_all_repos()
            except Exception:
                continue
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
