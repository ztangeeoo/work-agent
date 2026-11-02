# -*- coding: utf-8 -*-
"""
IT 面试出题官 · AgentScope 智能体服务（默认端口 8790）

调用链：
  浏览器 index.html
    → server.js（Node :8787，静态托管 + /api/models + /api/chat 转发）
      → 本服务 POST /v1/chat/completions（OpenAI 兼容，SSE 流式）
        → AgentScope 多 Agent 编排：
            策划官  Planner    顺序流水线第 1 棒：首次出题前产出考察大纲
            面试官  Interviewer Agent + 高频题库工具：出题 / 追问 / 讲解
            评分官  Evaluator  Agent + 结构化输出（Pydantic）：回答评分卡

每次请求无状态：按前端传来的 messages 重建 Agent 的 context，多人同时使用互不串场。
"""

import os
import re
import json
import time
import uuid
import asyncio
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.event import (
    TextBlockDeltaEvent,
    ToolCallStartEvent,
    ReplyEndEvent,
)
from agentscope.message import Msg, UserMsg, AssistantMsg, SystemMsg
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, Toolkit
from agentscope.types import ReplyFinishedReason

from question_bank import query_question_bank

# ------------------------------------------------------------------ 配置
PORT = int(os.environ.get("AGENT_PORT", "8790"))
# 首次出题前是否先跑「策划官」生成考察大纲（关掉可省一次模型调用：USE_PLANNER=0）
USE_PLANNER = os.environ.get("USE_PLANNER", "1") not in ("0", "false", "False")

AGENT_NAME = "agentscope-interview"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------ 评分结构化模型


class ScoreDimension(BaseModel):
    """评分维度"""

    name: str = Field(description="维度名称，例如：准确性 / 深度 / 结构 / 表达")
    score: int = Field(description="该维度得分，0-100 的整数")
    comment: str = Field(description="一句话点评，指出具体好在哪/差在哪")


class ScoreCard(BaseModel):
    """面试回答评分卡"""

    overall: int = Field(description="总分，0-100 的整数")
    dimensions: list[ScoreDimension] = Field(description="各维度得分")
    strengths: list[str] = Field(description="回答中的亮点，1-4 条")
    weaknesses: list[str] = Field(description="回答中的不足，1-4 条")
    suggestions: list[str] = Field(description="可执行的改进建议，1-4 条")
    reference_answer: str = Field(description="该问题的参考回答要点，Markdown 列表")


# ------------------------------------------------------------------ 工具


def _bank_tool() -> FunctionTool:
    """高频题库工具（显式放行，避免服务端卡在人工确认）"""
    return FunctionTool(
        query_question_bank,
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="内置题库检索，无需确认",
        ),
    )


# ------------------------------------------------------------------ 模型构造（带缓存）

_MODEL_CACHE: dict[tuple, OpenAIChatModel] = {}


def normalize_base(base: str) -> str:
    """把用户填的 API 地址规整成 OpenAI SDK 可用的 base_url（需以 /v1 结尾）"""
    b = (base or "").strip().rstrip("/")
    if not b:
        raise ValueError("缺少 API 地址")
    if not re.match(r"^https?://", b, re.I):
        b = "https://" + b
    if not re.search(r"/v\d+$", b):
        b += "/v1"
    return b


def get_model(base: str, key: str, model_name: str, temperature: float) -> OpenAIChatModel:
    cache_key = (base, key or "", model_name, temperature)
    if cache_key not in _MODEL_CACHE:
        _MODEL_CACHE[cache_key] = OpenAIChatModel(
            credential=OpenAICredential(
                api_key=key or "sk-empty",
                base_url=base,
            ),
            model=model_name,
            parameters=OpenAIChatModel.Parameters(temperature=temperature),
            stream=True,
            max_retries=1,  # 401/404 快速失败，错误尽快透传给用户
            client_kwargs={"timeout": 180.0},
        )
    return _MODEL_CACHE[cache_key]


# ------------------------------------------------------------------ 提示词

PROFILE_DEFAULTS = {
    "track": "前端开发",
    "sub": "随机",
    "level": "中级",
    "exp": "1-3年",
    "industry": "不限",
    "count": 5,
    "lang": "中文",
    "note": "",
    "resumeText": "",
}


def profile_text(p: dict) -> str:
    """结构化画像 → 提示词文本"""
    track = p.get("track") or "前端开发"
    sub = p.get("sub") or "随机"
    level = p.get("level") or "中级"
    exp = p.get("exp") or "1-3年"
    industry = p.get("industry") or "不限"
    lang = p.get("lang") or "中文"
    resume = (p.get("resumeText") or "").strip()

    sub_text = f"{track} / {sub}" if sub and sub != "随机" else f"{track}（细分方向随机）"

    lines = [
        f"- 技术方向：{sub_text}",
        f"- 级别：{level}",
        f"- 工作经验：{exp}",
        f"- 行业背景：{industry}",
        f"- 出题语言：{lang}",
    ]
    if resume:
        lines.append("")
        lines.append("候选人简历原文（出题必须围绕其中的技术栈与项目经历深挖）：")
        lines.append("<<<")
        lines.append(resume[:6000])
        lines.append(">>>")
    return "\n".join(lines)


def interviewer_sys_prompt(p: dict) -> str:
    sub = p.get("sub") or "随机"
    industry = p.get("industry") or "不限"
    lang = p.get("lang") or "中文"
    note = (p.get("note") or "").strip()

    sub_rule = (
        f"细分方向固定为「{sub}」：围绕该子领域深度优先，可以连续追问底层原理。"
        if sub != "随机"
        else "细分方向随机：题目要覆盖该主方向的不同子领域，均衡分布。"
    )
    industry_rule = (
        "行业不限：使用通用互联网业务场景即可。"
        if industry == "不限"
        else f"场景题必须结合「{industry}」行业的真实约束（数据规模、合规要求、稳定性/实时性诉求），"
        "要具体可信，避免脱离行业的教科书式提问。"
    )
    note_rule = f"\n7. 用户的补充要求：{note}" if note else ""

    return f"""你是一位资深 IT 技术面试官，称呼「面试官」。你只做两件事：出题、答追问。

## 候选人画像
{profile_text(p)}

## 出题规范
1. 难度必须匹配候选人的级别与经验：初级重概念清晰，中级重原理与排查，高级重设计权衡与体系化，专家重架构决策与风险预判。
2. 题型至少覆盖四类中的三类：基础概念 / 场景设计 / 排查定位 / 深挖追问；避免纯背书题。
3. 有简历时，优先深挖简历里的技术栈、项目细节、技术选型原因、难点与解决方案。
4. {sub_rule}
5. {industry_rule}
6. 用{lang}输出，Markdown 排版，题目编号清晰。每题格式固定为：

### 第 N 题【题型】
题目正文……

【考察点】一句话说明这道题想筛出什么能力。{note_rule}

## 工具使用
- query_question_bank：查询该方向的高频经典题。出题前可以先调用它作参考，确保不遗漏高频考点；
  但必须结合上面的候选人画像与行业背景改造题目，严禁照抄题库原句。

## 对话中的追问
用户可能要求：讲解某题的思路、给出参考答案或评分标准、按同样风格再出几道、换个方向出题。
追问必须基于你之前问过的题目，保持上下文连续；回答要直接给结论，再给推理过程。"""


def planner_sys_prompt(p: dict) -> str:
    return f"""你是面试团队的「出题策划官」。你不负责写题，只负责规划考察大纲。

## 候选人画像
{profile_text(p)}

## 输出要求
严格按以下格式输出一份精炼的考察大纲（总长度 200 字以内，不要出题、不要解释）：

### 考察大纲
1. <子领域>（权重 高/中/低）：<该领域要覆盖的考点要点>
2. <子领域>（权重 高/中/低）：<考点要点>
3. ……

规则：
- 大纲条目数与用户要求的题量匹配（默认 {p.get("count", 5)} 道）。
- 有简历时，至少一条大纲必须指向简历中的项目或技术栈。
- 只输出大纲本身，不要任何前后缀说明。"""


def evaluator_sys_prompt(p: dict) -> str:
    return f"""你是面试团队的「评分官」，只负责对候选人的面试回答做结构化评分。

## 候选人画像
{profile_text(p)}

## 评分要求
- 依据候选人的级别（{p.get("level") or "中级"}）与经验（{p.get("exp") or "1-3年"}）设定评分基线，不要用专家标准苛求初级。
- 维度默认：准确性 / 深度 / 结构 / 表达，可按题目特点增删。
- comment 必须具体：指出哪句话对、哪个点漏了，不写空话。
- reference_answer 给出该问题的参考回答要点（分点、可执行），不要写成完整范文。
- 全程用中文。"""


# ------------------------------------------------------------------ 路由

_SCORE_PAT = re.compile(r"评分|评估|打个分|评价一下|点评一下|复盘|我答得|看看我的回答|帮我看看答")


def route_intent(user_text: str, has_assistant_msg: bool) -> str:
    """根据最后一条用户消息判断走哪个 Agent"""
    if _SCORE_PAT.search(user_text or ""):
        return "score"
    return "interview"


# ------------------------------------------------------------------ 消息转换


def to_scope_msgs(messages: list[dict]) -> list[Msg]:
    """OpenAI 格式消息 → AgentScope Msg；system 由各 Agent 的 system_prompt 承担，跳过"""
    out = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "system":
            continue
        if isinstance(content, list):  # 兼容多模态分段
            content = "".join(
                c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
            )
        if not content or not str(content).strip():
            continue
        text = str(content)
        if role == "assistant":
            out.append(AssistantMsg("面试官", text))
        else:
            out.append(UserMsg("user", text))
    return out


def last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content") or ""
            if isinstance(c, list):
                c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
            return str(c)
    return ""


# ------------------------------------------------------------------ 评分卡渲染


def render_score_card(card: dict) -> str:
    overall = card.get("overall", "-")
    lines = [f"## 📊 回答评分：{overall} / 100", ""]
    dims = card.get("dimensions") or []
    if dims:
        lines.append("| 维度 | 得分 | 点评 |")
        lines.append("| --- | --- | --- |")
        for d in dims:
            lines.append(f"| {d.get('name', '')} | {d.get('score', '')} | {d.get('comment', '')} |")
        lines.append("")
    for key, title in (("strengths", "✅ 亮点"), ("weaknesses", "⚠️ 不足"), ("suggestions", "💡 改进建议")):
        items = card.get(key) or []
        if items:
            lines.append(f"**{title}**")
            lines.extend(f"- {i}" for i in items)
            lines.append("")
    ref = card.get("reference_answer")
    if ref:
        lines.append("**📖 参考回答要点**")
        lines.append("")
        lines.append(ref)
    return "\n".join(lines).strip()


def try_parse_score(text: str) -> Optional[dict]:
    """结构化输出不可用时，尝试从原文解析 JSON 评分卡"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and "overall" in obj:
            return obj
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\"overall\"[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


# ------------------------------------------------------------------ SSE 输出


def sse_chunk(content: str, model: str, cid: str, created: int, finish: Optional[str] = None) -> str:
    delta = {"content": content} if content else {}
    if finish:
        delta["finish_reason"] = finish
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


# 三个智能体的展示信息（前端流水线面板用）
STAGE_META = {
    "planner": {"label": "策划官", "icon": "🧭"},
    "interviewer": {"label": "面试官", "icon": "🎯"},
    "grader": {"label": "评分官", "icon": "🧑‍⚖️"},
}


def sse_stage(stage: str, status: str, ms: Optional[int] = None, detail: str = "") -> str:
    """阶段事件：不放在 choices 里，标准 OpenAI 客户端会安全忽略。

    status: running / done / skipped / tool / error
    """
    meta = STAGE_META.get(stage, {"label": stage, "icon": "🤖"})
    info = {
        "stage": stage,
        "label": meta["label"],
        "icon": meta["icon"],
        "status": status,
        "ts": int(time.time() * 1000),
    }
    if ms is not None:
        info["ms"] = ms
    if detail:
        info["detail"] = detail
    return "data: " + json.dumps({"agent_stage": info}, ensure_ascii=False) + "\n\n"



def fmt_exc(e: BaseException) -> str:
    msg = str(e).strip() or e.__class__.__name__
    low = msg.lower()
    cls = e.__class__.__name__.lower()
    if "401" in msg or "invalid api key" in low or "authentication" in low:
        return "模型 API Key 无效或没有权限（HTTP 401）。请检查左侧配置里的 Key。"
    if "403" in msg:
        return "没有访问该模型的权限（HTTP 403）。请检查 Key 权限或模型名。"
    if "404" in msg:
        return "接口或模型不存在（HTTP 404）。请检查 API 地址是否以 /v1 结尾、模型名是否正确。"
    if "429" in msg:
        return "请求被限流或额度不足（HTTP 429）。请稍后重试或检查账户额度。"
    if "econnrefused" in low or "connection error" in low or "connectionerror" in cls:
        return "连接模型接口失败（连接被拒绝/网络错误）：本地模型（如 Ollama）是否已启动？地址端口是否正确？"
    if "enotfound" in low or "getaddrinfo" in low:
        return "域名解析失败（ENOTFOUND）：请检查 API 地址拼写。"
    if "timeout" in low or "timed out" in low or "timeouterror" in cls:
        return "连接模型接口超时：请检查网络，或稍后重试。"
    if "error code: 5" in low or "badgateway" in low:
        return "模型接口返回 5xx 服务端错误，可能是中转站波动，请稍后重试。原始信息：" + msg[:200]
    return "智能体运行失败（%s）：%s" % (e.__class__.__name__, msg[:300])


# ------------------------------------------------------------------ Agent 驱动


async def drive_agent(
    agent: Agent,
    inputs: list[Msg],
    structured_schema=None,
):
    """驱动单个 Agent 流式输出，产出 (kind, payload)：
    ("text", 增量文本) / ("structured", dict) / ("tool", 工具名) / ("error", 异常)
    """
    final_msg: Optional[Msg] = None
    tool_hinted = False
    try:
        async for evt in agent.reply_stream(
            inputs,
            structured_schema=structured_schema,
            yield_final_msg=bool(structured_schema),
        ):
            if isinstance(evt, Msg):
                final_msg = evt  # yield_final_msg=True 时的最终消息
                continue
            if isinstance(evt, TextBlockDeltaEvent):
                if evt.delta:
                    yield ("text", evt.delta)
            elif isinstance(evt, ToolCallStartEvent):
                if not tool_hinted:
                    tool_hinted = True
                    yield ("tool", evt.tool_call_name)
            elif isinstance(evt, ReplyEndEvent):
                if evt.finished_reason == ReplyFinishedReason.ERROR and evt.error:
                    yield ("error", RuntimeError(evt.error.message))
    except Exception as e:  # 模型调用失败等
        yield ("error", e)
        return

    if structured_schema is not None:
        card = None
        if final_msg is not None:
            card = final_msg.structured_output
        yield ("structured", card if isinstance(card, dict) else None)


def make_agent(name: str, sys_prompt: str, model: OpenAIChatModel, with_tools: bool, context: list[Msg]) -> Agent:
    """构造一个带独立 context 的 Agent（无状态服务，每次请求新建）"""
    toolkit = Toolkit(tools=[_bank_tool()]) if with_tools else Toolkit()
    return Agent(
        name=name,
        system_prompt=sys_prompt,
        model=model,
        toolkit=toolkit,
        state=AgentState(context=context),
    )


class StatusFlusher:
    """懒发送状态前缀：Agent 真正开始产出前不写任何字节，
    这样模型配置/连接类错误能以 JSON 502 透出，而不是混进 SSE 内容。"""

    def __init__(self):
        self.pending = ""
        self.flushed = False

    def set(self, text: str):
        if not self.flushed:
            self.pending += text

    def flush(self):
        """返回待发送的前缀（只发一次）"""
        if self.flushed:
            return ""
        self.flushed = True
        out, self.pending = self.pending, ""
        return out


# ------------------------------------------------------------------ 核心流程


async def run_pipeline(payload: dict, base_override: Optional[str] = None):
    """执行多 Agent 流程，产出 SSE 文本块"""
    base_raw = base_override or (payload.get("base") or "")
    key = payload.get("key") or ""
    model_name = payload.get("model") or ""
    temperature = payload.get("temperature")
    temperature = float(temperature) if isinstance(temperature, (int, float)) else 0.7
    messages = payload.get("messages") or []
    profile = {**PROFILE_DEFAULTS, **(payload.get("profile") or {})}

    if not model_name:
        raise ValueError("缺少模型名称")
    if not base_raw:
        raise ValueError("缺少 API 地址")

    base = normalize_base(base_raw)
    model = get_model(base, key, model_name, temperature)

    history = to_scope_msgs(messages)
    last_user = last_user_text(messages)
    has_assistant = any(m.get("role") == "assistant" for m in messages)
    intent = route_intent(last_user, has_assistant)

    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())
    # 注意：不预发 role 占位块——保证模型/连接类错误能以 JSON 502 透出
    log(f"[{cid[:12]}] intent={intent} model={model_name} base={base} 历史={len(history)}条")

    # ================= 评分官：结构化输出 =================
    if intent == "score":
        agent = make_agent("评分官", evaluator_sys_prompt(profile), model, False, history)
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
        return

    # ================= 策划官 → 面试官（顺序流水线） =================
    status = StatusFlusher()
    plan_text = ""
    if USE_PLANNER and not has_assistant:
        yield sse_stage("planner", "running")
        t_planner = time.time()
        planner = make_agent("策划官", planner_sys_prompt(profile), model, False, [])
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
    interviewer = make_agent("面试官", interviewer_sys_prompt(profile), model, True, history)
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


async def run_with_retry(payload: dict):
    """带 /v1 自动补全重试：第一次失败且未产出内容时，补 /v1 再试一次"""
    base_raw = (payload.get("base") or "").strip()
    produced = False
    try:
        async for chunk in run_pipeline(payload):
            produced = True
            yield chunk
        return
    except Exception as e:
        if produced:
            raise
        msg = str(e)
        need_retry = "404" in msg and base_raw and not re.search(r"/v\d+$", base_raw.rstrip("/"))
        if not need_retry:
            raise
        log(f"404 重试：{base_raw} → {base_raw.rstrip('/')}/v1")
        async for chunk in run_pipeline(payload, base_override=base_raw.rstrip("/") + "/v1"):
            yield chunk


# ------------------------------------------------------------------ HTTP 服务

app = FastAPI(title="IT面试出题官 · AgentScope 服务")


@app.get("/health")
async def health():
    import agentscope

    return {
        "ok": True,
        "service": AGENT_NAME,
        "agentscope": getattr(agentscope, "__version__", "unknown"),
        "agents": ["策划官", "面试官", "评分官"],
        "planner": USE_PLANNER,
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse({"error": {"message": "请求体解析失败：" + str(e)}}, status_code=400)

    if not payload.get("model"):
        return JSONResponse({"error": {"message": "缺少模型名称"}}, status_code=400)
    if not payload.get("base"):
        return JSONResponse({"error": {"message": "缺少 API 地址"}}, status_code=400)
    if not isinstance(payload.get("messages"), list) or not payload.get("messages"):
        return JSONResponse({"error": {"message": "缺少 messages"}}, status_code=400)

    gen = run_with_retry(payload)

    # 预取第一块：模型配置/连接有问题时直接返回 JSON 错误，前端能读到明确原因
    try:
        first = await gen.__anext__()
    except StopAsyncIteration:
        return JSONResponse({"error": {"message": "智能体没有返回任何内容"}}, status_code=502)
    except Exception as e:
        log("请求失败：" + fmt_exc(e))
        return JSONResponse({"error": {"message": fmt_exc(e)}}, status_code=502)

    async def body():
        if first:
            yield first
        try:
            async for chunk in gen:
                yield chunk
        except Exception as e:
            # 流式中途出错：把错误作为一条内容发出去，用户能在对话里看到
            yield sse_chunk(
                "\n\n❌ " + fmt_exc(e),
                payload.get("model", ""),
                "chatcmpl-error",
                int(time.time()),
                finish="stop",
            )
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    print("")
    print("  🤖 AgentScope 智能体服务启动中…")
    print(f"  ➜  端口: {PORT}（由 server.js 转发，无需直接访问）")
    print(f"  ➜  策划官: {'开' if USE_PLANNER else '关'}（USE_PLANNER=0 可关闭）")
    print("")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", timeout_keep_alive=300)
