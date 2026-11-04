# -*- coding: utf-8 -*-
"""工具注册：Agent 可调用的函数（AgentScope FunctionTool + Toolkit）+ 技能装载

全部显式 ALLOW，避免服务端卡在「人工确认」上（无头服务没人点确认）。
新增工具时在这里加一条分支；新增技能只需要往 backend/skills/ 放目录
（归属解析见 services/skills.py）。
"""

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit

from ..services.skills import for_stage
from .question_bank import query_question_bank
from .repo import repo_tools


def bank_tool() -> FunctionTool:
    """高频题库工具（显式放行，避免服务端卡在人工确认）"""
    return FunctionTool(
        query_question_bank,
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="内置题库检索，无需确认",
        ),
    )


def build_toolkit(with_tools: bool = True, repo: dict | None = None,
                  stage: str = "") -> Toolkit:
    """按需装配工具箱。

    - with_tools=False：不装工具，Agent 依旧可正常对话；
    - 传了 repo：审核官场景，装配只读仓库工具（列 MR / 拉 diff / 看文件）；
    - 没传 repo：面试场景，装配内置题库工具；
    - stage：当前角色（interviewer / grader / scout …），决定装载哪些技能。

    技能与工具互相独立：策划官、评分官这类没有工具的角色，也能靠技能拿到纯文本
    的方法论——技能是提示词，不是可调用工具。
    """
    tools: list[FunctionTool] = []
    if with_tools:
        tools = repo_tools(repo) if repo else [bank_tool()]

    # 该角色可见的技能目录；为空时不传该参数，Toolkit 行为与接入前一致
    skill_dirs = for_stage(stage)

    kwargs: dict = {}
    if tools:
        kwargs["tools"] = tools
    if skill_dirs:
        kwargs["skills_or_loaders"] = skill_dirs
    return Toolkit(**kwargs)
