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


# ---------------------------------------------------------------- 专家与角色
# 「专家」是用户能选的两位入口（对应 web/src/data/tracks.ts 的 AGENTS）；
# 「角色」是流水线里的具体智能体（上面 STAGE_META 的 key）。
# 专家 id 沿用前端已有取值（不改历史会话数据），其中 interviewer 恰好与同名
# 角色重名，所以代码里凡指专家一律写成 expert_id / EXPERT_*。
EXPERT_META = {
    "interviewer": {"label": "面试出题官", "icon": "面", "mode": "interview"},
    "reviewer": {"label": "代码审核官", "icon": "审", "mode": "review"},
}

# 专家 → 该专家下会跑到的角色。skill 绑定到专家时，这些角色都会装载它。
EXPERT_STAGES = {
    "interviewer": ("planner", "interviewer", "grader"),
    "reviewer": ("scout", "reviewer", "judge", "advisor"),
}


def stages_of_expert(expert_id: str) -> tuple[str, ...]:
    """某个专家下会跑到的角色列表；未知专家返回空"""
    return EXPERT_STAGES.get(expert_id, ())


def experts_of_stage(stage: str) -> list[str]:
    """某个角色属于哪些专家（一个角色只属于一个专家；用于展示）"""
    return [e for e, stages in EXPERT_STAGES.items() if stage in stages]

