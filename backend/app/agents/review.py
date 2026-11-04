# -*- coding: utf-8 -*-
"""代码审核 · 三智能体流水线

侦察官（读 diff 归纳）→ 审核官（结构化问题清单）→ 裁决官（风险与结论）。
前一个的输出作为后一个的输入，任一环节失败即整轮失败（不留半份报告）。
"""

import json
import time

from agentscope.message import UserMsg

from ..core.log import log
from ..core.stages import stage_meta
from ..llm.factory import draft_model
from ..prompts.review import judge_sys_prompt, reviewer_sys_prompt, scout_sys_prompt
from ..schemas.review_report import normalize_decision, normalize_risk, normalize_severity
from ..utils.parsing import diff_stats, try_parse_json
from ..utils.render import render_review_md
from .runtime import drive_agent, make_agent


async def run_review(payload: dict) -> dict:
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

    report = {
        "risk": normalize_risk(verdict.get("risk")),
        "decision": normalize_decision(verdict.get("decision")),
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
