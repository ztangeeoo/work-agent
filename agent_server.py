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

运行时基线：AgentScope 2.0（Python 3.11+）
  - 编排用 2.0 的 `Agent` + `reply_stream()` 事件流（TextBlockDelta / ToolCallStart / ReplyEnd）
  - 模型用 `OpenAIChatModel` + `OpenAICredential`，可接任意 OpenAI 兼容接口
  - 工具用 `FunctionTool` + `Toolkit`，显式 ALLOW 免人工确认
  - 评分卡用 2.0 原生 `structured_schema` 结构化输出，失败时回退到文本解析
  - 不再依赖已归档的 `agentscope-runtime` 包
"""

import os
import re
import sys
import json
import time
import uuid
import asyncio
from typing import Any, Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

import agentscope
from agentscope.agent import Agent
from agentscope.credential import OpenAICredential
from agentscope.event import (
    TextBlockDeltaEvent,
    ToolCallStartEvent,
    ReplyEndEvent,
)
from agentscope.message import Msg, UserMsg, AssistantMsg
from agentscope.model import OpenAIChatModel
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit
from agentscope.types import ReplyFinishedReason

import harness_adapter
from question_bank import query_question_bank

# ------------------------------------------------------------------ 版本闸门
# 本项目按 AgentScope 2.0 的 API 编写（Agent / reply_stream / Event / structured_schema）。
# 1.x 的 ReActAgent + agentscope_runtime 写法已不再适用，装错版本会报一堆看不懂的
# ImportError，这里提前拦截并给出可执行的修复命令。
AS_VERSION = str(getattr(agentscope, "__version__", "0.0.0"))
_as_major = AS_VERSION.split(".")[0]
if not _as_major.isdigit() or int(_as_major) < 2:
    raise RuntimeError(
        f"本项目需要 AgentScope 2.0+，当前安装的是 {AS_VERSION}。\n"
        f"请执行：python -m pip install -U 'agentscope>=2.0.11'"
    )

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

    return harness_adapter.compose(f"""你是一位资深 IT 技术面试官，称呼「面试官」。你只做两件事：出题、答追问。

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
追问必须基于你之前问过的题目，保持上下文连续；回答要直接给结论，再给推理过程。""", "interview")


def planner_sys_prompt(p: dict) -> str:
    return harness_adapter.compose(f"""你是面试团队的「出题策划官」。你不负责写题，只负责规划考察大纲。

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
- 只输出大纲本身，不要任何前后缀说明。""", "interview")


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


# ================================================================== 代码审核
# 三智能体流水线：侦察官（读 diff 归纳）→ 审核官（结构化问题清单）→ 裁决官（风险与结论）


# 说明：这里不用 structured_schema（response_format / tool-call）——
# 审核结果要经过 OpenAI 兼容中转站，各站对结构化输出的支持参差不齐，
# 一旦不支持会触发 AgentScope 反复重试，既慢又费额度。
# 改为「提示词约定严格 JSON + 容错解析」，任何兼容接口都能跑。

ISSUES_JSON_SHAPE = """{"issues":[{"severity":"blocker|major|minor|nit","file":"相对路径","line":42,"title":"不超过30字","detail":"触发条件与影响","suggestion":"可执行改法，必要时给代码"}],"notes":"补充说明（可空）"}"""

VERDICT_JSON_SHAPE = """{"risk":"high|medium|low","decision":"approve|comment|request_changes","summary":"3句以内的结论","positives":["亮点1"],"suggestions":["建议1"]}"""

SEVERITY_META = {
    "blocker": {"label": "阻塞", "icon": "🔴", "order": 0},
    "major": {"label": "重要", "icon": "🟠", "order": 1},
    "minor": {"label": "次要", "icon": "🟡", "order": 2},
    "nit": {"label": "细节", "icon": "🔵", "order": 3},
}

DECISION_LABEL = {
    "approve": "可以合入",
    "comment": "可合入，建议先改",
    "request_changes": "打回修改",
}

RISK_LABEL = {"high": "高", "medium": "中", "low": "低"}


def pr_brief(pr: dict) -> str:
    return "\n".join([
        f"- 仓库：{pr.get('repo') or '-'}",
        f"- PR：#{pr.get('number') or '-'} {pr.get('title') or ''}".strip(),
        f"- 作者：{pr.get('author') or '-'}",
        f"- 分支：{pr.get('headBranch') or '-'} → {pr.get('baseBranch') or '-'}",
    ])


def scout_sys_prompt(pr: dict) -> str:
    return harness_adapter.compose(f"""你是代码审核团队的「侦察官」。你只负责读 diff、讲清楚这次改了什么，不下结论、不提修改建议。

## 本次审核对象
{pr_brief(pr)}

## 输出要求
180 字以内，分三小块（用短横线列表）：
1. 改动意图：一句话概括这个 PR 想做什么
2. 涉及文件：列出主要文件（超过 5 个时按目录归并）
3. 重点风险区：2-4 条，指出哪里最值得审核官细看（如鉴权、并发、数据迁移、错误处理、兼容性）

只输出这三块，不要寒暄，不要复述整段代码。全程中文。""", "review")


def reviewer_sys_prompt(pr: dict) -> str:
    return harness_adapter.compose(f"""你是代码审核团队的「审核官」，负责逐处找出真实存在的问题。

## 本次审核对象
{pr_brief(pr)}

## 审核维度
- 正确性：逻辑错误、边界条件、空值、异常处理、资源释放
- 安全：注入、越权、鉴权缺失、密钥硬编码、不安全的反序列化
- 并发与性能：竞态、死锁、N+1 查询、循环内 IO、无界内存
- 可维护性：重复代码、命名误导、过深嵌套、缺少注释的关键决策
- 兼容性与测试：破坏性变更、缺少用例、错误码不一致

## 严重程度
- blocker：会导致线上故障、数据错误或安全漏洞，必须改
- major：明显问题，应该改
- minor：建议改，不影响正确性
- nit：风格与细节

## 硬性要求
1. 只依据给出的 diff，不要臆测 diff 之外的代码；不确定就在 detail 里说明前提。
2. 每条必须给文件路径与行号（diff 中 + 侧行号；无法定位填 0）。
3. detail 必须写清「什么条件触发、会造成什么后果」，禁止「建议优化」这类空话。
4. suggestion 给可执行改法，必要时给代码片段。
5. 最多 10 条，按严重度排序；确实没问题就返回空列表，并在 notes 写清已检查的维度。
6. 全程中文。

## 输出格式
只输出一个 JSON 对象，不要解释、不要 markdown 代码块，格式严格如下：
{ISSUES_JSON_SHAPE}""", "review")


def judge_sys_prompt(pr: dict) -> str:
    return harness_adapter.compose(f"""你是代码审核团队的「裁决官」。基于侦察官概览与审核官的问题清单，给出最终结论。

## 本次审核对象
{pr_brief(pr)}

## 判定规则
- 存在 blocker → risk=high，decision=request_changes
- 只有 major：3 条以上 → risk=high 或 medium，decision=request_changes；1-2 条 → risk=medium，decision=comment
- 只有 minor / nit → risk=low，decision=comment 或 approve
- 没有任何问题 → risk=low，decision=approve

## 输出要求
- summary：3 句以内，说清「能不能合、最大风险是什么、合入前先改哪一处」
- positives：1-3 条具体的亮点（不要空泛夸奖）
- suggestions：1-4 条，区分「合入前必须做」与「后续可做」
- 全程中文。

## 输出格式
只输出一个 JSON 对象，不要解释、不要 markdown 代码块，格式严格如下：
{VERDICT_JSON_SHAPE}""", "review")


def diff_stats(diff: str) -> dict:
    files = 0
    additions = 0
    deletions = 0
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return {"files": files, "additions": additions, "deletions": deletions}


def try_parse_json(text: str, required: Optional[str] = None) -> Optional[dict]:
    """结构化输出不可用时，从文本里捞 JSON"""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    candidates = [t]
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
        except Exception:
            continue
        if isinstance(obj, dict) and (required is None or required in obj):
            return obj
    return None


def normalize_risk(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("high", "高", "高risk", "严重"):
        return "high"
    if s in ("low", "低", "轻微", "轻"):
        return "low"
    if s in ("medium", "中", "中等", "中risk"):
        return "medium"
    return "medium"


def normalize_decision(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in ("approve", "approved", "通过", "可以合入", "同意"):
        return "approve"
    if s in ("request_changes", "request changes", "打回", "打回修改", "要求修改", "reject", "拒绝"):
        return "request_changes"
    return "comment"


def normalize_severity(v: Any) -> str:
    s = str(v or "").strip().lower()
    if s in SEVERITY_META:
        return s
    if s in ("block", "critical", "严重", "阻塞", "致命"):
        return "blocker"
    if s in ("high", "important", "重要", "高"):
        return "major"
    if s in ("low", "建议", "minor"):
        return "minor"
    return "nit"


def render_review_md(pr: dict, report: dict, model_name: str, elapsed_ms: int) -> str:
    """审核报告 → Markdown（前端按 `## 🛡️ PR #N` 识别为审核卡片）"""
    issues = report.get("issues") or []
    counts = {k: 0 for k in SEVERITY_META}
    for it in issues:
        counts[normalize_severity(it.get("severity"))] += 1

    risk = normalize_risk(report.get("risk"))
    decision = normalize_decision(report.get("decision"))
    count_text = "、".join(
        f"{counts[k]} {SEVERITY_META[k]['label']}" for k in SEVERITY_META if counts[k]
    ) or "无"

    title = (pr.get("title") or "").replace("\n", " ").strip()
    lines = [
        f"## 🛡️ PR #{pr.get('number')} {title}".rstrip(),
        "",
        f"> 仓库 {pr.get('repo') or '-'} ｜ 作者 {pr.get('author') or '-'} "
        f"｜ 分支 {pr.get('headBranch') or '-'} → {pr.get('baseBranch') or '-'}",
        f"> 风险：{RISK_LABEL[risk]} ｜ 结论：{DECISION_LABEL[decision]} "
        f"｜ 问题：{len(issues)} 处（{count_text}）",
        "",
    ]

    summary = (report.get("summary") or "").strip()
    if summary:
        lines += ["**总体结论**", "", summary, ""]

    scout = (report.get("scout") or "").strip()
    if scout:
        lines += ["**改动概览**", "", scout[:800], ""]

    if issues:
        lines.append("### 问题清单")
        lines.append("")
        grouped: dict[str, list] = {k: [] for k in SEVERITY_META}
        for it in issues:
            grouped[normalize_severity(it.get("severity"))].append(it)
        idx = 0
        for sev in sorted(SEVERITY_META, key=lambda k: SEVERITY_META[k]["order"]):
            group = grouped[sev]
            if not group:
                continue
            meta = SEVERITY_META[sev]
            lines.append(f"#### {meta['icon']} {meta['label']}（{len(group)}）")
            lines.append("")
            for it in group:
                idx += 1
                f = str(it.get("file") or "").strip() or "（未指明文件）"
                ln = it.get("line") or 0
                loc = f"`{f}:{ln}`" if ln else f"`{f}`"
                lines.append(f"{idx}. **{loc}** {str(it.get('title') or '').strip()}")
                lines.append(f"   - 问题：{str(it.get('detail') or '').strip()}")
                sug = str(it.get("suggestion") or "").strip()
                if sug:
                    lines.append("   - 建议：" + sug.replace("\n", "\n     "))
            lines.append("")
    else:
        lines += ["**问题清单**：未发现明显问题 ✅", ""]

    notes = (report.get("notes") or "").strip()
    if notes:
        lines += [f"> ⚠️ {notes}", ""]

    positives = report.get("positives") or []
    if positives:
        lines.append("**✅ 亮点**")
        lines.append("")
        lines += [f"- {p}" for p in positives]
        lines.append("")

    suggestions = report.get("suggestions") or []
    if suggestions:
        lines.append("**💡 建议**")
        lines.append("")
        lines += [f"- {s}" for s in suggestions]
        lines.append("")

    stats = report.get("stats") or {}
    lines.append("---")
    lines.append(
        f"🔍 侦察官 → 审核官 → 裁决官 ｜ {stats.get('files', 0)} 个文件 "
        f"(+{stats.get('additions', 0)}/−{stats.get('deletions', 0)}) ｜ {model_name} ｜ "
        f"{(elapsed_ms / 1000):.1f}s"
    )
    if pr.get("url"):
        lines.append(f"🔗 {pr.get('url')}")
    return "\n".join(lines).strip()


async def run_review(payload: dict) -> dict:
    """执行代码审核流水线，返回 {report, reportMd, model}"""
    base_raw = payload.get("base") or ""
    key = payload.get("key") or ""
    model_name = payload.get("model") or ""
    pr = payload.get("pr") or {}
    diff = str(payload.get("diff") or "").strip()

    if not model_name:
        raise ValueError("缺少模型名称")
    if not base_raw:
        raise ValueError("缺少 API 地址")
    if not diff:
        raise ValueError("这个 PR 没有可比对的代码改动")

    base = normalize_base(base_raw)
    model = get_model(base, key, model_name, 0.2)
    stats = diff_stats(diff)
    stages: list[dict] = []
    t_all = time.time()

    def stage_done(stage: str, ms: int, detail: str = "") -> dict:
        item = {**STAGE_META.get(stage, {"label": stage, "icon": "🤖"}),
                "stage": stage, "status": "done", "ms": ms}
        if detail:
            item["detail"] = detail
        stages.append(item)
        return item

    # ---------- 1. 侦察官 ----------
    t0 = time.time()
    scout = make_agent("侦察官", scout_sys_prompt(pr), model, False)
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
    reviewer = make_agent("审核官", reviewer_sys_prompt(pr), model, False)
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
    judge = make_agent("裁决官", judge_sys_prompt(pr), model, False)
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


def reviewer_chat_sys_prompt(review: dict) -> str:
    """审核模式下的追问智能体：带着当前 PR 的审核报告回答"""
    report_md = str(review.get("reportMd") or "")[:8000]
    return harness_adapter.compose(f"""你是「代码审核官」。你刚刚完成了一个 PR 的审核，现在回答用户关于这次审核的追问。

## 当前审核对象
- 仓库：{review.get('repo') or '-'}
- PR：#{review.get('number') or '-'} {review.get('title') or ''}
- 风险：{RISK_LABEL.get(normalize_risk(review.get('risk')), '-')} ｜ 结论：{DECISION_LABEL.get(normalize_decision(review.get('decision')), '-')}

## 你的审核报告
{report_md or '（报告为空）'}

## 回答要求
1. 只基于报告内容与提到的代码事实回答，不要编造 PR 里不存在的代码。
2. 用户要修复方案时，给出可直接粘贴的代码片段，并标明文件路径与位置。
3. 用户问「能不能合」时，先给明确结论，再说前置条件。
4. 用户要求「写一条 PR 评论」时，输出一段可直接粘贴到 PR 的中文评论（Markdown）。
5. 回答用中文，简洁，别重复整份报告。""", "review")


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


# 各智能体的展示信息（前端流水线面板用）
STAGE_META = {
    # 面试出题官
    "planner": {"label": "策划官", "icon": "🧭"},
    "interviewer": {"label": "面试官", "icon": "🎯"},
    "grader": {"label": "评分官", "icon": "🧑‍⚖️"},
    # 代码审核官
    "scout": {"label": "侦察官", "icon": "🔍"},
    "reviewer": {"label": "审核官", "icon": "🛡️"},
    "judge": {"label": "裁决官", "icon": "⚖️"},
    "advisor": {"label": "审核答疑", "icon": "💬"},
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


def make_agent(name: str, sys_prompt: str, model: OpenAIChatModel, with_tools: bool) -> Agent:
    """构造一个全空状态的 Agent（无状态服务，每次请求新建）。

    注意 AgentScope 2.0 的语义：`reply_stream(inputs)` 会把 inputs **追加**进
    `state.context`，所以历史对话只应该通过 inputs 传一次。之前这里既预置
    `state.context=history` 又传 `inputs=history`，导致每轮上下文翻倍、最后一条
    用户消息出现三次（既费 token 也干扰模型判断）。现在统一走 inputs。
    """
    toolkit = Toolkit(tools=[_bank_tool()]) if with_tools else Toolkit()
    return Agent(
        name=name,
        system_prompt=sys_prompt,
        model=model,
        toolkit=toolkit,
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

    # ================= 代码审核官：针对当前 PR 的追问 =================
    agent_mode = str(payload.get("agent_mode") or "")
    review_ctx = payload.get("review") or {}
    if agent_mode == "review":
        yield sse_stage("advisor", "running")
        t_adv = time.time()
        status = StatusFlusher()
        status.set("🛡️ 审核官正在整理答复…\n\n")
        advisor = make_agent("代码审核官", reviewer_chat_sys_prompt(review_ctx), model, False)
        ask = last_user or "请总结这次审核的要点"
        produced = False
        # 历史里的最后一条就是用户这次的问题，再追加一份同样的 ask 会让模型
        # 看到重复提问；只有历史为空（例如刚打开页面就追问）时才兜底造一条。
        adv_inputs = list(history) or [UserMsg("user", ask)]
        async for kind, val in drive_agent(advisor, adv_inputs):
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
        return

    # ================= 评分官：结构化输出 =================
    if intent == "score":
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


# ================================================================== 数据服务层
# 持久化（store.py）+ 代码托管适配（reviewer.py）+ PR 轮询（review_poller.py）
# 这一层的 API 由 server.js 反向代理暴露给前端（前端仍只访问 8787）。

from contextlib import asynccontextmanager

import store
import reviewer as rv
from review_poller import create_review_poller

# 前端最新模型配置（内存，由 Node 反代转发 /api/model-config 更新）
MODEL_CFG: dict = {
    "base": "", "key": "", "model": "", "profile": None,
    "agentMode": "interview", "review": None, "runId": "",
}

# ---------- 审核事件中心：轮询状态 + 审核完成广播（页面晚开也能回放最近一批） ----------
REVIEW_EVENT_MAX = 120
_review_clients: set = set()
_review_event_log: list = []


def broadcast_review(ev: dict) -> None:
    _review_event_log.append(ev)
    if len(_review_event_log) > REVIEW_EVENT_MAX:
        del _review_event_log[: len(_review_event_log) - REVIEW_EVENT_MAX]
    for q in list(_review_clients):
        try:
            q.put_nowait(ev)
        except Exception:
            _review_clients.discard(q)


poller = create_review_poller(
    get_model_cfg=lambda: MODEL_CFG,
    review_fn=run_review,
    on_event=broadcast_review,
    log=lambda m: log("[审核] " + m),
)


def _user_key(request: Request) -> str:
    return str(request.headers.get("x-user-key") or "local")[:64]


def _err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"message": message}}, status_code=status)


def _db_blocked() -> Optional[JSONResponse]:
    if store.available():
        return None
    return JSONResponse(
        {"dbAvailable": False,
         "error": {"message": "SQLite 不可用，已回退到浏览器本地存储：" + store.unavailable_reason()}},
        status_code=503,
    )


def _swallow(task: asyncio.Task) -> None:
    """后台任务结束回调：取走异常，避免 "never retrieved" 噪音。"""
    if task.cancelled():
        return
    task.exception()


@asynccontextmanager
async def lifespan(_app):
    try:
        store.init()
        s = store.stats()
        log(f"持久层就绪 · {s['convos']} 场会话 / {s['messages']} 条消息 · {s['path']}")
        repos = store.list_repos()
        active = len([r for r in repos if r.get("enabled")])
        log(f"代码审核：{len(repos)} 个仓库（启用 {active} 个）· 轮询调度启动")
        poller.start()
    except Exception as e:
        log("持久层初始化失败（前端将回退本地存储）：" + str(e))
    yield
    try:
        await poller.stop()
    finally:
        store.close()


# ------------------------------------------------------------------ HTTP 服务

app = FastAPI(title="IT面试出题官 · AgentScope 服务", lifespan=lifespan)


@app.get("/health")
async def health():
    info = {
        "ok": True,
        "service": AGENT_NAME,
        "agentscope": AS_VERSION,
        "agentscopeMin": "2.0.0",
        "python": sys.version.split()[0],
        "agents": ["策划官", "面试官", "评分官", "侦察官", "审核官", "裁决官"],
        "modes": ["interview", "review"],
        "planner": USE_PLANNER,
    }
    try:
        if store.available():
            info["db"] = {"available": True, **store.stats()}
        else:
            info["db"] = {"available": False, "reason": store.unavailable_reason()}
    except Exception as e:
        info["db"] = {"available": False, "error": str(e)}
    return info


# ==================================================================
# 主服务 API：模型配置 / 会话 / 检索 / 仓库 / PR / 审核报告 / 事件流
# 全部由 server.js 反向代理到 /api/*，前端无需知道端口
# ==================================================================


@app.post("/api/model-config")
async def api_model_config(request: Request):
    """把前端最新模型配置存进内存，供聊天适配与审核轮询共用。"""
    try:
        p = await request.json()
    except Exception as e:
        return _err("请求体解析失败：" + str(e), 400)
    MODEL_CFG["base"] = str(p.get("base") or "").strip()
    MODEL_CFG["key"] = str(p.get("key") or "").strip()
    MODEL_CFG["model"] = str(p.get("model") or "").strip()
    MODEL_CFG["profile"] = p.get("profile") if isinstance(p.get("profile"), dict) else None
    MODEL_CFG["agentMode"] = "review" if p.get("agentMode") == "review" else "interview"
    MODEL_CFG["review"] = p.get("review") if isinstance(p.get("review"), dict) else None
    rid = p.get("runId")
    if isinstance(rid, str) and rid:
        MODEL_CFG["runId"] = rid
    return {"ok": True}


@app.get("/api/db/stats")
async def api_db_stats():
    if not store.available():
        return {"dbAvailable": False, "reason": store.unavailable_reason()}
    try:
        return {"dbAvailable": True, **store.stats(), "harness": harness_adapter.status()}
    except Exception as e:
        return _err(str(e), 500)


@app.get("/api/convos")
async def api_convos_list(request: Request):
    blocked = _db_blocked()
    if blocked:
        return blocked
    qp = request.query_params
    try:
        limit = min(int(qp.get("limit") or 200), 200)
    except Exception:
        limit = 200
    with_messages = qp.get("messages") != "0"
    try:
        return {"ok": True, "dbAvailable": True,
                "list": store.list_convos(_user_key(request), limit=limit, with_messages=with_messages)}
    except Exception as e:
        return _err("数据库操作失败：" + str(e), 500)


@app.post("/api/convos")
async def api_convos_create(request: Request):
    blocked = _db_blocked()
    if blocked:
        return blocked
    try:
        body = await request.json()
        return {"ok": True, **store.save_convo(body, _user_key(request))}
    except Exception as e:
        return _err("数据库操作失败：" + str(e), 500)


@app.get("/api/convos/{cid}")
async def api_convo_get(request: Request, cid: str):
    blocked = _db_blocked()
    if blocked:
        return blocked
    try:
        conv = store.get_convo(cid, _user_key(request))
        return {"ok": True, "convo": conv} if conv else _err("会话不存在：" + cid, 404)
    except Exception as e:
        return _err("数据库操作失败：" + str(e), 500)


@app.post("/api/convos/{cid}")
async def api_convo_save(request: Request, cid: str):
    blocked = _db_blocked()
    if blocked:
        return blocked
    try:
        body = await request.json()
        return {"ok": True, **store.save_convo({**body, "id": cid}, _user_key(request))}
    except Exception as e:
        return _err("数据库操作失败：" + str(e), 500)


@app.delete("/api/convos/{cid}")
async def api_convo_delete(request: Request, cid: str):
    blocked = _db_blocked()
    if blocked:
        return blocked
    try:
        removed = store.delete_convo(cid, _user_key(request))
        return {"ok": True} if removed else _err("会话不存在：" + cid, 404)
    except Exception as e:
        return _err("数据库操作失败：" + str(e), 500)


@app.get("/api/search")
async def api_search(request: Request):
    blocked = _db_blocked()
    if blocked:
        return blocked
    q = request.query_params.get("q") or ""
    try:
        limit = min(int(request.query_params.get("limit") or 30), 100)
    except Exception:
        limit = 30
    if not q.strip():
        return {"ok": True, "query": q, "results": []}
    try:
        return {"ok": True, "query": q, "results": store.search_messages(q, user=_user_key(request), limit=limit)}
    except Exception as e:
        return _err("检索失败：" + str(e), 500)


# ---------------- 仓库配置 ----------------

@app.get("/api/repos")
async def api_repos_list(request: Request):
    blocked = _db_blocked()
    if blocked:
        return blocked
    try:
        return {"ok": True, "list": store.list_repos(_user_key(request))}
    except Exception as e:
        return _err("仓库操作失败：" + str(e), 500)


@app.post("/api/repos")
async def api_repos_save(request: Request):
    blocked = _db_blocked()
    if blocked:
        return blocked
    try:
        body = await request.json()
    except Exception as e:
        return _err("请求体解析失败：" + str(e), 400)
    try:
        parsed = rv.parse_repo_url(body.get("url"), body.get("provider"))
    except Exception as e:
        return _err(str(e), 400)
    # 用户没显式选平台时，对自建实例探测一次（GitLab 走 /api/v4，Gitea 走 /api/v3，猜错就是 404）
    # 注意：探测只借用 token 发请求，不能把 token 写回 parsed —— save_repo 见到 token='' 会清空已存的 Token
    if not str(body.get("provider") or "").strip():
        try:
            probe = await rv.ensure_provider({**parsed, "token": body.get("token") or ""})
            parsed["provider"] = probe.get("provider") or parsed["provider"]
        except Exception:
            pass
    try:
        repo = store.save_repo({**body, **parsed}, _user_key(request))
        return {"ok": True, "repo": repo}
    except Exception as e:
        return _err("仓库操作失败：" + str(e), 500)


@app.delete("/api/repos/{rid}")
async def api_repos_delete(request: Request, rid: str):
    blocked = _db_blocked()
    if blocked:
        return blocked
    try:
        ok = store.delete_repo(rid, _user_key(request))
        return {"ok": True} if ok else _err("仓库不存在：" + rid, 404)
    except Exception as e:
        return _err("仓库操作失败：" + str(e), 500)


@app.post("/api/repos/{rid}/test")
async def api_repos_test(request: Request, rid: str):
    """测试地址 / Token 是否可用"""
    blocked = _db_blocked()
    if blocked:
        return blocked
    repo = store.get_repo(rid, _user_key(request), with_token=True)
    if not repo:
        return _err("仓库不存在：" + rid, 404)
    try:
        r = await rv.test_repo(repo)
        # test_repo 里会把自建实例的 provider 校准好，变化就回写
        try:
            store.set_repo_provider(rid, repo.get("provider") or "")
        except Exception:
            pass
        store.touch_repo(rid, error="")
        return {"ok": True, **r}
    except Exception as e:
        store.touch_repo(rid, error=str(e))
        return _err(str(e), 502)


@app.post("/api/repos/{rid}/poll")
async def api_repos_poll(request: Request, rid: str):
    """立即轮询：默认异步触发（202），结果走 /api/review/events；?wait=1 阻塞等待"""
    blocked = _db_blocked()
    if blocked:
        return blocked
    if not store.get_repo(rid, _user_key(request)):
        return _err("仓库不存在：" + rid, 404)
    force = request.query_params.get("force") != "0"
    task = asyncio.create_task(poller.poll_repo(rid, force=force))
    task.add_done_callback(_swallow)
    if request.query_params.get("wait") != "1":
        return JSONResponse({"ok": True, "started": True}, status_code=202)
    try:
        return {"ok": True, **(await task)}
    except Exception as e:
        return _err(str(e), 502)


@app.get("/api/repos/{rid}/prs")
async def api_repos_prs(request: Request, rid: str):
    blocked = _db_blocked()
    if blocked:
        return blocked
    repo = store.get_repo(rid, _user_key(request), with_token=False)
    if not repo:
        return _err("仓库不存在：" + rid, 404)
    try:
        limit = min(int(request.query_params.get("limit") or 20), 100)
    except Exception:
        limit = 20
    try:
        return {"ok": True, "repo": repo, "list": store.list_prs(rid, limit=limit)}
    except Exception as e:
        return _err("读取 PR 列表失败：" + str(e), 500)


@app.post("/api/repos/{rid}/prs/{number}/review")
async def api_repos_review_pr(request: Request, rid: str, number: int):
    """立即审核单个 PR"""
    blocked = _db_blocked()
    if blocked:
        return blocked
    if not store.get_repo(rid, _user_key(request)):
        return _err("仓库不存在：" + rid, 404)
    try:
        return {"ok": True, "review": await poller.review_pr(rid, number)}
    except Exception as e:
        return _err(str(e), 502)


# ---------------- 审核报告 ----------------

@app.get("/api/reviews")
async def api_reviews_list(request: Request):
    blocked = _db_blocked()
    if blocked:
        return blocked
    repo_id = request.query_params.get("repoId") or ""
    try:
        limit = min(int(request.query_params.get("limit") or 20), 100)
    except Exception:
        limit = 20
    try:
        return {"ok": True, "list": store.list_reviews(_user_key(request), repo_id=repo_id, limit=limit)}
    except Exception as e:
        return _err("读取审核报告失败：" + str(e), 500)


@app.post("/api/reviews/{rid}/post")
async def api_review_post(request: Request, rid: str):
    """把某份审核意见一键回帖到 PR"""
    blocked = _db_blocked()
    if blocked:
        return blocked
    review = store.get_review(rid)
    if not review:
        return _err("审核报告不存在：" + rid, 404)
    repo = store.get_repo_by_id(review["repoId"], with_token=True)
    if not repo:
        return _err("仓库已被删除", 404)
    if not repo.get("token"):
        return _err("该仓库没有配置 Token，无法回帖", 400)
    body = "\n".join([
        review.get("reportMd") or "",
        "",
        "---",
        "🤖 由 AI 代码审核官自动生成（侦察官 → 审核官 → 裁决官）。",
        "请结合业务上下文判断，机器意见仅供参考。",
    ])
    try:
        r = await rv.post_pr_comment(repo, review["prNumber"], body)
        return {"ok": True, "url": r.get("url"), "review": store.mark_review_posted(rid, posted=True)}
    except Exception as e:
        store.mark_review_posted(rid, posted=False, error=str(e))
        return _err("回帖失败：" + str(e), 502)


@app.get("/api/review/events")
async def api_review_events(request: Request):
    """审核事件订阅（SSE）：轮询状态 + 审核完成；订阅时先补发最近一批"""
    q: asyncio.Queue = asyncio.Queue()
    for ev in _review_event_log[-30:]:
        q.put_nowait(ev)
    _review_clients.add(q)

    async def gen():
        try:
            while True:
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15)
                    yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _review_clients.discard(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "Access-Control-Allow-Origin": "*",
    })


@app.post("/v1/review")
async def review_pr(request: Request):
    """代码审核：侦察官 → 审核官 → 裁决官，返回结构化报告 + Markdown"""
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse({"error": {"message": "请求体解析失败：" + str(e)}}, status_code=400)

    if not payload.get("model"):
        return JSONResponse({"error": {"message": "缺少模型名称"}}, status_code=400)
    if not payload.get("base"):
        return JSONResponse({"error": {"message": "缺少 API 地址"}}, status_code=400)
    if not str(payload.get("diff") or "").strip():
        return JSONResponse({"error": {"message": "缺少 diff 内容"}}, status_code=400)

    try:
        result = await run_review(payload)
        return JSONResponse({"ok": True, **result})
    except ValueError as e:
        return JSONResponse({"error": {"message": str(e)}}, status_code=400)
    except Exception as e:
        log("审核失败：" + fmt_exc(e))
        return JSONResponse({"error": {"message": fmt_exc(e)}}, status_code=502)


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
