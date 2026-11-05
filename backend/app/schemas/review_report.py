# -*- coding: utf-8 -*-
"""审核报告数据契约：JSON 形状、标签表与归一化

为什么不用 structured_schema（response_format / tool-call）：
审核结果要经过 OpenAI 兼容中转站，各站对结构化输出的支持参差不齐，
一旦不支持会触发 AgentScope 反复重试，既慢又费额度。
改为「提示词约定严格 JSON + 容错解析」，任何兼容接口都能跑。
"""

from typing import Any

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
    "request_changes": "不建议合并",
}

# 出现这些严重程度就一票否决：不允许「可合入」这种口径往下走
BLOCKING_SEVERITIES = ("blocker", "major")

RISK_LABEL = {"high": "高", "medium": "中", "low": "低"}


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


def severity_counts(issues) -> dict:
    """按严重程度计数（容忍 issues 里缺字段/脏值）"""
    counts = {k: 0 for k in SEVERITY_META}
    for it in (issues or []):
        if isinstance(it, dict):
            counts[normalize_severity(it.get("severity"))] += 1
    return counts


def blocking_count(issues) -> int:
    counts = severity_counts(issues)
    return sum(counts[k] for k in BLOCKING_SEVERITIES)


def enforce_decision(risk: Any, decision: Any, issues) -> tuple[str, str]:
    """用问题清单兜底裁决结论。

    为什么不让模型单独说了算：模型经常一边列出 2 条 major，一边给
    「可合入，建议先改」，回帖到 PR 上就成了「可以合」。这里按硬规则改：
    只要出现 1 条 blocker 或 major，就返回 request_changes（不建议合并）。
    """
    counts = severity_counts(issues)
    r = normalize_risk(risk)
    d = normalize_decision(decision)
    if counts["blocker"]:
        return "high", "request_changes"
    if counts["major"]:
        # 重要问题多时一并判高风险；1-2 条至少给 medium
        return ("high" if counts["major"] >= 3 else "medium"), "request_changes"
    return r, d
