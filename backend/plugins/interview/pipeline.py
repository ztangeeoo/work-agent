# -*- coding: utf-8 -*-
"""面试出题官 · 流水线实现

  chat(ctx)  策划官 → 面试官（首次出题），或评分官（「评一下我这回答」）

依赖全靠 ctx 传入：model / base / history / last_user / profile / options 都由
注册表准备好，这里不再自己构造模型、也不 import 请求体里的私有键。
"""

import time
from typing import AsyncIterator

from agentscope.message import UserMsg

from backend.app import config
from backend.app.agents.contract import ChatCtx
from backend.app.agents.runtime import StatusFlusher, drive_agent, make_agent
from backend.app.core.log import log
from backend.app.schemas.score_card import ScoreCard
from backend.app.utils.parsing import try_parse_score
from backend.app.utils.render import render_score_card
from backend.app.utils.sse import sse_chunk, sse_stage
from .intent import INTENT_SCORE, route_intent
from .prompts import evaluator_sys_prompt, interviewer_sys_prompt, planner_sys_prompt


def _planner_enabled(ctx: ChatCtx) -> bool:
    """策划官是否启用

    两道闸：插件清单的 `options.use_planner` 是业务默认，环境变量 USE_PLANNER
    是运维级总开关（关掉可省一次模型调用，不用改 yaml）。
    """
    return bool(ctx.options.get("use_planner", True)) and config.USE_PLANNER


async def _stream_score(ctx: ChatCtx) -> AsyncIterator[str]:
    """评分官：结构化输出优先，失败回退到文本解析"""
    p, model = ctx.profile, ctx.model
    agent = make_agent("评分官", evaluator_sys_prompt(p), model, False, stage="grader")
    ask = UserMsg(
        "user",
        "请对下面这场面试对话中候选人的最后一次回答进行结构化评分。"
        "如果候选人还没有给出实质性回答，请基于最近一道面试题给出「自评清单 + 参考回答要点」。\n\n"
        f"【候选人最后的消息】\n{ctx.last_user}",
    )
    yield sse_stage("planner", "skipped")
    yield sse_stage("interviewer", "skipped")
    yield sse_stage("grader", "running")
    t_grader = time.time()
    status = StatusFlusher()
    status.set("🧑‍⚖️ 评分官正在分析回答…\n\n")
    full_text = ""
    card = None
    async for kind, val in drive_agent(agent, [*ctx.history, ask], structured_schema=ScoreCard):
        if kind == "error":
            raise val  # 未产出内容，错误以 JSON 502 透出
        if kind in ("text", "structured"):
            head = status.flush()
            if head:
                yield sse_chunk(head, ctx.model_name, ctx.cid, ctx.created)
        if kind == "text":
            full_text += val
            yield sse_chunk(val, ctx.model_name, ctx.cid, ctx.created)
        elif kind == "structured":
            card = val
    yield sse_stage("grader", "done", ms=int((time.time() - t_grader) * 1000))
    if not card:
        card = try_parse_score(full_text)
    if card:
        head = status.flush()
        if head:
            yield sse_chunk(head, ctx.model_name, ctx.cid, ctx.created)
        yield sse_chunk("\n\n---\n\n" + render_score_card(card),
                        ctx.model_name, ctx.cid, ctx.created)
    elif not full_text:
        yield sse_chunk("（评分官没有返回内容，请重试或换一个模型）",
                        ctx.model_name, ctx.cid, ctx.created)
    yield sse_chunk("", ctx.model_name, ctx.cid, ctx.created, finish="stop")
    yield "data: [DONE]\n\n"


async def chat(ctx: ChatCtx) -> AsyncIterator[str]:
    """执行面试流水线，产出 SSE 文本块"""
    if not ctx.model_name:
        raise ValueError("缺少模型名称")
    if not (ctx.base or "").strip():
        raise ValueError("缺少 API 地址")

    intent = route_intent(ctx.last_user, ctx.has_assistant,
                          ctx.options.get("score_keywords"))
    # 注意：不预发 role 占位块——保证模型/连接类错误能以 JSON 502 透出
    log(f"[{ctx.cid[:12]}] intent={intent} model={ctx.model_name} 历史={len(ctx.history)}条")

    # ================= 评分官：结构化输出 =================
    if intent == INTENT_SCORE:
        async for chunk in _stream_score(ctx):
            yield chunk
        return

    # ================= 策划官 → 面试官（顺序流水线） =================
    p, model = ctx.profile, ctx.model
    status = StatusFlusher()
    plan_text = ""
    if _planner_enabled(ctx) and not ctx.has_assistant:
        yield sse_stage("planner", "running")
        t_planner = time.time()
        planner = make_agent("策划官", planner_sys_prompt(p), model, False, stage="planner")
        plan_ask = UserMsg("user", f"请为下面这次面试规划考察大纲：\n{ctx.last_user}")
        status.set("🧭 策划官正在规划考察大纲…\n\n")
        async for kind, val in drive_agent(planner, [plan_ask]):
            if kind == "error":
                raise val
            if kind == "text":
                head = status.flush()
                if head:
                    yield sse_chunk(head, ctx.model_name, ctx.cid, ctx.created)
                plan_text += val
                yield sse_chunk(val, ctx.model_name, ctx.cid, ctx.created)
        yield sse_stage("planner", "done", ms=int((time.time() - t_planner) * 1000),
                        detail=f"大纲 {len(plan_text)} 字")
        status.set("\n\n---\n\n🎯 面试官出题\n\n")
        log(f"[{ctx.cid[:12]}] 策划官大纲 {len(plan_text)} 字")
    else:
        yield sse_stage("planner", "skipped")

    yield sse_stage("grader", "skipped")
    yield sse_stage("interviewer", "running")
    t_interviewer = time.time()
    interviewer = make_agent("面试官", interviewer_sys_prompt(p), model, True,
                             stage="interviewer")
    inputs = list(ctx.history)
    if plan_text:
        # 注意：AgentScope 的 inputs 不允许带 system 消息，大纲用 user 角色承载
        inputs.append(UserMsg("策划官", f"【策划官的考察大纲，请按此覆盖出题】\n{plan_text}"))
    if not any(m.role == "user" for m in inputs):
        inputs.append(UserMsg("user", ctx.last_user or "请开始出题"))

    async for kind, val in drive_agent(interviewer, inputs):
        if kind == "error":
            raise val
        if kind == "text":
            head = status.flush()
            if head:
                yield sse_chunk(head, ctx.model_name, ctx.cid, ctx.created)
            yield sse_chunk(val, ctx.model_name, ctx.cid, ctx.created)
        elif kind == "tool":
            status.set(f"\n📚 面试官正在查阅高频题库（{val}）…\n\n")
            yield sse_stage("interviewer", "tool", detail=f"查阅题库 {val}")

    yield sse_stage("interviewer", "done", ms=int((time.time() - t_interviewer) * 1000))
    yield sse_chunk("", ctx.model_name, ctx.cid, ctx.created, finish="stop")
    yield "data: [DONE]\n\n"
