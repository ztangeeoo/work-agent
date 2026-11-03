# -*- coding: utf-8 -*-
"""流水线阶段元信息

前端「智能体流水线」面板按 stage 渲染标签与图标，SSE 里的
`{agent_stage: {stage, label, icon, status, ms, detail}}` 取自这里。
新增一个智能体时只需要在这里加一行。
"""

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


def stage_meta(stage: str) -> dict:
    """取阶段元信息，未知阶段给个兜底，避免前端空白"""
    return STAGE_META.get(stage, {"label": stage, "icon": "🤖"})
