# -*- coding: utf-8 -*-
"""代码审核官 · 流水线实现

两个入口：
  chat(ctx)   带着当前 PR 的审核报告回答追问（可调用只读仓库工具）
  run(payload) 侦察官 → 审核官 → 裁决官，返回结构化报告

前一个的输出作为后一个的输入，任一环节失败即整轮失败（不留半份报告）。
"""

import json
import time
from typing import AsyncIterator

from agentscope.message import UserMsg

from backend.app.agents.contract import ChatCtx
from backend.app.agents.runtime import StatusFlusher, drive_agent, make_agent
from backend.app.core.errors import fmt_exc
from backend.app.core.log import log
from backend.app.core.stages import stage_meta
from backend.app.llm.factory import draft_model
from backend.app.schemas.review_report import (
    blocking_count,
    enforce_decision,
    normalize_decision,
    normalize_severity,
)
from backend.app.utils.parsing import diff_stats, try_parse_json
from backend.app.utils.render import render_review_md
from backend.app.utils.sse import sse_chunk, sse_stage
from .prompts import judge_sys_prompt, reviewer_chat_sys_prompt, reviewer_sys_prompt, scout_sys_prompt


def resolve_review_repo(payload: dict, review: dict) -> dict | None:
    """定位这次追问该用哪个仓库（带 token）。

    优先级：payload.repo_id（前端当前选中的仓库）→ 报告里的 owner/name →
    只有一个仓库时兜底。找不到就返回 None，审核官会退化成「只能读报告」模式。
    """
    from backend.app import store

    rid = str(payload.get("repo_id") or "").strip()
    if rid:
        try:
            repo = store.get_repo_by_id(rid, with_token=True)
            if repo:
                return repo
        except Exception:
            pass

    full = str((review or {}).get("repo") or "").strip().strip("/")
    try:
        repos = store.list_all_repos()
    except Exception:
        return None
    if full and "/" in full:
        for r in repos:
            if (str(r.get("owner") or "") + "/" + str(r.get("name") or "")) == full:
                return r
    if len(repos) == 1:
        return repos[0]
    return None


# ---------------------------------------------------------------- chat 入口
async def chat(ctx: ChatCtx) -> AsyncIterator[str]:
    """审核模式下的追问：带着当前 PR 的审核报告回答，并可调用只读仓库工具"""
    review_ctx = ctx.payload.get("review") or {}
    repo = resolve_review_repo(ctx.payload, review_ctx)

    yield sse_stage("advisor", "running")
    t_adv = time.time()
    status = StatusFlusher()
    status.set("🛡️ 审核官正在整理答复…\n\n")
    advisor = make_agent("代码审核官", reviewer_chat_sys_prompt(review_ctx, repo),
                         ctx.model, bool(repo), repo=repo, stage="advisor")
    ask = ctx.last_user or "请总结这次审核的要点"
    produced = False
    # 历史里的最后一条就是用户这次的问题，再追加一份同样的 ask 会让模型
    # 看到重复提问；只有历史为空（例如刚打开页面就追问）时才兜底造一条。
    inputs = list(ctx.history) or [UserMsg("user", ask)]
    async for kind, val in drive_agent(advisor, inputs):
        if kind == "error":
            if not produced:
                raise val
            yield sse_chunk("\n\n❌ " + fmt_exc(val), ctx.model_name, ctx.cid, ctx.created)
            break
        if kind == "text":
            head = status.flush()
            if head:
                yield sse_chunk(head, ctx.model_name, ctx.cid, ctx.created)
            produced = True
            yield sse_chunk(val, ctx.model_name, ctx.cid, ctx.created)
    yield sse_stage("advisor", "done", ms=int((time.time() - t_adv) * 1000))
    yield sse_chunk("", ctx.model_name, ctx.cid, ctx.created, finish="stop")
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------- job 入口
async def run(payload: dict) -> dict:
    """执行代码审核流水线，返回 {report, reportMd, model}"""
    pr = payload.get("pr") or {}
    diff = str(payload.get("diff") or "").strip()
    model_name = payload.get("model") or ""

    if not model_name:
        raise ValueError("缺少模型名称")
    if not payload.get("base"):
        raise ValueError("缺少 API 地址")
    if not diff:
        raise ValueError("这个 PR 没有可比对的代码改动")

    model = draft_model(payload)
    stats = diff_stats(diff)
    stages: list[dict] = []
    t_all = time.time()

    def stage_done(stage: str, ms: int, detail: str = "") -> dict:
        item = {**stage_meta(stage),
                "stage": stage, "status": "done", "ms": ms}
        if detail:
            item["detail"] = detail
        stages.append(item)
        return item

    # ---------- 1. 侦察官 ----------
    t0 = time.time()
    scout = make_agent("侦察官", scout_sys_prompt(pr), model, False, stage="scout")
    scout_text = ""
    async for kind, val in drive_agent(
        scout,
        [UserMsg("user", f"请阅读下面这个 PR 的 diff，输出改动概览。\n\n```diff\n{diff}\n```")],
    ):
        if kind == "error":
            raise val
        if kind == "text":
            scout_text += val
    stage_done("scout", int((time.time() - t0) * 1000), f"概览 {len(scout_text)} 字")
    log(f"[review] 侦察官完成 {len(scout_text)} 字")

    # ---------- 2. 审核官（结构化问题清单） ----------
    t1 = time.time()
    reviewer = make_agent("审核官", reviewer_sys_prompt(pr), model, False, stage="reviewer")
    review_inputs = [
        UserMsg("侦察官", f"【侦察官的改动概览】\n{scout_text or '（无）'}"),
        UserMsg("user", f"请审核下面这个 PR 的 diff，输出问题清单。\n\n```diff\n{diff}\n```"),
    ]
    issues: list[dict] = []
    notes_text = ""
    issues_text = ""
    async for kind, val in drive_agent(reviewer, review_inputs):
        if kind == "error":
            raise val
        if kind == "text":
            issues_text += val
    card = try_parse_json(issues_text, "issues")
    if isinstance(card, dict):
        issues = [i for i in (card.get("issues") or []) if isinstance(i, dict)]
        notes_text = str(card.get("notes") or "")
    stage_done("reviewer", int((time.time() - t1) * 1000), f"{len(issues)} 处问题")
    log(f"[review] 审核官完成 {len(issues)} 处问题")

    # ---------- 3. 裁决官 ----------
    t2 = time.time()
    judge = make_agent("裁决官", judge_sys_prompt(pr), model, False, stage="judge")
    issues_json = json.dumps(
        [{**i, "severity": normalize_severity(i.get("severity"))} for i in issues],
        ensure_ascii=False, indent=1,
    )[:6000]
    verdict_inputs = [
        UserMsg("侦察官", f"【改动概览】\n{scout_text or '（无）'}"),
        UserMsg("审核官", f"【问题清单】\n{issues_json or '[]'}"),
        UserMsg("user", "请给出最终结论。"),
    ]
    verdict_text = ""
    async for kind, val in drive_agent(judge, verdict_inputs):
        if kind == "error":
            raise val
        if kind == "text":
            verdict_text += val
    verdict = try_parse_json(verdict_text, "risk")
    if not isinstance(verdict, dict):
        verdict = {}
    stage_done("judge", int((time.time() - t2) * 1000))

    # 裁决官可能一边列 major 一边给「可合入」，这里用问题清单兜底改写结论
    risk_final, decision_final = enforce_decision(
        verdict.get("risk"), verdict.get("decision"), issues)
    if decision_final != normalize_decision(verdict.get("decision")):
        log(f"[review] 结论被兜底改写：{verdict.get('decision')} → {decision_final}"
            f"（阻塞/重要 {blocking_count(issues)} 条）")

    report = {
        "risk": risk_final,
        "decision": decision_final,
        "summary": str(verdict.get("summary") or "").strip(),
        "positives": [str(x) for x in (verdict.get("positives") or [])][:4],
        "suggestions": [str(x) for x in (verdict.get("suggestions") or [])][:4],
        "issues": issues,
        "notes": notes_text,
        "scout": scout_text.strip(),
        "stats": stats,
        "stages": stages,
        "elapsedMs": int((time.time() - t_all) * 1000),
    }
    md = render_review_md(pr, report, model_name, report["elapsedMs"])
    log(f"[review] 完成 风险={report['risk']} 结论={report['decision']} 用时={report['elapsedMs']}ms")
    return {"report": report, "reportMd": md, "model": model_name}
