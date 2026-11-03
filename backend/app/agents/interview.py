# -*- coding: utf-8 -*-
"""面试出题官 · 多智能体流水线（无状态，一次请求跑完就丢）

调用链：
  POST /v1/chat/completions（OpenAI 兼容，SSE 流式）
    → 策划官  Planner    顺序流水线第 1 棒：首次出题前产出考察大纲
    → 面试官  Interviewer Agent + 高频题库工具：出题 / 追问 / 讲解
    → 评分官  Evaluator  Agent + 结构化输出（Pydantic）：回答评分卡

历史对话由前端 messages 重建（memory 不落库），所以多人同时用不会串场。
"""

import time
import uuid
from typing import Optional

from agentscope.message import UserMsg

from ..config import USE_PLANNER
from ..core.errors import fmt_exc
from ..core.log import log
from ..llm.factory import draft_model, has_v1_suffix
from ..prompts.interview import evaluator_sys_prompt, interviewer_sys_prompt, planner_sys_prompt
from ..prompts.profile import PROFILE_DEFAULTS
from ..schemas.score_card import ScoreCard
from ..utils.messages import has_assistant_reply, last_user_text, to_scope_msgs
from ..utils.parsing import try_parse_score
from ..utils.render import render_score_card
from ..utils.sse import sse_chunk, sse_stage
from .router import INTENT_SCORE, route_intent
from .runtime import StatusFlusher, drive_agent, make_agent


async def stream_review_advisor(payload: dict, model, cid: str, created: int, model_name: str):
    """审核模式下的追问：带着当前 PR 的审核报告回答（单智能体，无流水线）"""
    from ..prompts.review import reviewer_chat_sys_prompt

    history = payload.get("_history") or []
    last_user = payload.get("_last_user") or ""
    review_ctx = payload.get("review") or {}

    yield sse_stage("advisor", "running")
    t_adv = time.time()
    status = StatusFlusher()
    status.set("🛡️ 审核官正在整理答复…\n\n")
    advisor = make_agent("代码审核官", reviewer_chat_sys_prompt(review_ctx), model, False)
    ask = last_user or "请总结这次审核的要点"
    produced = False
    # 历史里的最后一条就是用户这次的问题，再追加一份同样的 ask 会让模型
    # 看到重复提问；只有历史为空（例如刚打开页面就追问）时才兜底造一条。
    inputs = list(history) or [UserMsg("user", ask)]
    async for kind, val in drive_agent(advisor, inputs):
        if kind == "error":
            if not produced:
                raise val
            yield sse_chunk("\n\n❌ " + fmt_exc(val), model_name, cid, created)
            break
        if kind == "text":
            head = status.flush()
            if head:
                yield sse_chunk(head, model_name, cid, created)
            produced = True
            yield sse_chunk(val, model_name, cid, created)
    yield sse_stage("advisor", "done", ms=int((time.time() - t_adv) * 1000))
    yield sse_chunk("", model_name, cid, created, finish="stop")
    yield "data: [DONE]\n\n"


async def stream_score(history, model, cid: str, created: int, model_name: str, profile: dict, last_user: str):
    """评分官：结构化输出优先，失败回退到文本解析"""
    agent = make_agent("评分官", evaluator_sys_prompt(profile), model, False)
    ask = UserMsg(
        "user",
        "请对下面这场面试对话中候选人的最后一次回答进行结构化评分。"
        "如果候选人还没有给出实质性回答，请基于最近一道面试题给出「自评清单 + 参考回答要点」。\n\n"
        f"【候选人最后的消息】\n{last_user}",
    )
    yield sse_stage("planner", "skipped")
    yield sse_stage("interviewer", "skipped")
    yield sse_stage("grader", "running")
    t_grader = time.time()
    status = StatusFlusher()
    status.set("🧑‍⚖️ 评分官正在分析回答…\n\n")
    full_text = ""
    card = None
    async for kind, val in drive_agent(agent, [*history, ask], structured_schema=ScoreCard):
        if kind == "error":
            raise val  # 未产出内容，错误以 JSON 502 透出
        if kind in ("text", "structured"):
            head = status.flush()
            if head:
                yield sse_chunk(head, model_name, cid, created)
        if kind == "text":
            full_text += val
            yield sse_chunk(val, model_name, cid, created)
        elif kind == "structured":
            card = val
    yield sse_stage("grader", "done", ms=int((time.time() - t_grader) * 1000))
    if not card:
        card = try_parse_score(full_text)
    if card:
        head = status.flush()
        if head:
            yield sse_chunk(head, model_name, cid, created)
        yield sse_chunk("\n\n---\n\n" + render_score_card(card), model_name, cid, created)
    elif not full_text:
        yield sse_chunk("（评分官没有返回内容，请重试或换一个模型）", model_name, cid, created)
    yield sse_chunk("", model_name, cid, created, finish="stop")
    yield "data: [DONE]\n\n"


async def stream_interview(payload: dict, base_override: Optional[str] = None):
    """执行多 Agent 流程，产出 SSE 文本块"""
    base_raw = base_override or (payload.get("base") or "")
    key = payload.get("key") or ""
    model_name = payload.get("model") or ""
    messages = payload.get("messages") or []
    profile = {**PROFILE_DEFAULTS, **(payload.get("profile") or {})}

    if not model_name:
        raise ValueError("缺少模型名称")
    if not base_raw:
        raise ValueError("缺少 API 地址")

    model = draft_model({**payload, "base": base_raw, "key": key, "model": model_name})

    history = to_scope_msgs(messages)
    last_user = last_user_text(messages)
    has_assistant = has_assistant_reply(messages)
    intent = route_intent(last_user, has_assistant)

    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    # 注意：不预发 role 占位块——保证模型/连接类错误能以 JSON 502 透出
    log(f"[{cid[:12]}] intent={intent} model={model_name} 历史={len(history)}条")

    # ================= 代码审核官：针对当前 PR 的追问 =================
    if str(payload.get("agent_mode") or "") == "review":
        async for chunk in stream_review_advisor(
            {**payload, "_history": history, "_last_user": last_user},
            model, cid, created, model_name,
        ):
            yield chunk
        return

    # ================= 评分官：结构化输出 =================
    if intent == INTENT_SCORE:
        async for chunk in stream_score(history, model, cid, created, model_name, profile, last_user):
            yield chunk
        return

    # ================= 策划官 → 面试官（顺序流水线） =================
    status = StatusFlusher()
    plan_text = ""
    if USE_PLANNER and not has_assistant:
        yield sse_stage("planner", "running")
        t_planner = time.time()
        planner = make_agent("策划官", planner_sys_prompt(profile), model, False)
        plan_ask = UserMsg("user", f"请为下面这次面试规划考察大纲：\n{last_user}")
        status.set("🧭 策划官正在规划考察大纲…\n\n")
        async for kind, val in drive_agent(planner, [plan_ask]):
            if kind == "error":
                raise val
            if kind == "text":
                head = status.flush()
                if head:
                    yield sse_chunk(head, model_name, cid, created)
                plan_text += val
                yield sse_chunk(val, model_name, cid, created)
        yield sse_stage("planner", "done", ms=int((time.time() - t_planner) * 1000),
                        detail=f"大纲 {len(plan_text)} 字")
        status.set("\n\n---\n\n🎯 面试官出题\n\n")
        log(f"[{cid[:12]}] 策划官大纲 {len(plan_text)} 字")
    else:
        yield sse_stage("planner", "skipped")

    yield sse_stage("grader", "skipped")
    yield sse_stage("interviewer", "running")
    t_interviewer = time.time()
    interviewer = make_agent("面试官", interviewer_sys_prompt(profile), model, True)
    inputs = list(history)
    if plan_text:
        # 注意：AgentScope 的 inputs 不允许带 system 消息，大纲用 user 角色承载
        inputs.append(UserMsg("策划官", f"【策划官的考察大纲，请按此覆盖出题】\n{plan_text}"))
    if not any(m.role == "user" for m in inputs):
        inputs.append(UserMsg("user", last_user or "请开始出题"))

    async for kind, val in drive_agent(interviewer, inputs):
        if kind == "error":
            raise val
        if kind == "text":
            head = status.flush()
            if head:
                yield sse_chunk(head, model_name, cid, created)
            yield sse_chunk(val, model_name, cid, created)
        elif kind == "tool":
            status.set(f"\n📚 面试官正在查阅高频题库（{val}）…\n\n")
            yield sse_stage("interviewer", "tool", detail=f"查阅题库 {val}")

    yield sse_stage("interviewer", "done", ms=int((time.time() - t_interviewer) * 1000))
    yield sse_chunk("", model_name, cid, created, finish="stop")
    yield "data: [DONE]\n\n"


async def stream_with_retry(payload: dict):
    """带 /v1 自动补全重试：第一次失败且未产出内容时，补 /v1 再试一次"""
    base_raw = (payload.get("base") or "").strip()
    produced = False
    try:
        async for chunk in stream_interview(payload):
            produced = True
            yield chunk
        return
    except Exception as e:
        if produced:
            raise
        need_retry = "404" in str(e) and base_raw and not has_v1_suffix(base_raw)
        if not need_retry:
            raise
        log(f"404 重试：{base_raw} → {base_raw.rstrip('/')}/v1")
        async for chunk in stream_interview(payload, base_override=base_raw.rstrip("/") + "/v1"):
            yield chunk
