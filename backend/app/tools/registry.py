# -*- coding: utf-8 -*-
"""工具注册：Agent 可调用的函数（AgentScope FunctionTool + Toolkit）

全部显式 ALLOW，避免服务端卡在「人工确认」上（无头服务没人点确认）。
新增工具时在这里加一条 `build_toolkit()` 的分支即可。
"""

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit

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


def build_toolkit(with_tools: bool = True, repo: dict | None = None) -> Toolkit:
    """按需装配工具箱。

    - with_tools=False：空工具箱，Agent 依旧可正常对话；
    - 传了 repo：审核官场景，装配只读仓库工具（列 MR / 拉 diff / 看文件）；
    - 没传 repo：面试场景，装配内置题库工具。
    """
    if not with_tools:
        return Toolkit()
    if repo:
        return Toolkit(tools=repo_tools(repo))
    return Toolkit(tools=[bank_tool()])
