# -*- coding: utf-8 -*-
"""工具注册：Agent 可调用的函数（AgentScope FunctionTool + Toolkit）

全部显式 ALLOW，避免服务端卡在「人工确认」上（无头服务没人点确认）。
新增工具时在这里加一条 `build_toolkit()` 的分支即可。
"""

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit

from .question_bank import query_question_bank


def bank_tool() -> FunctionTool:
    """高频题库工具（显式放行，避免服务端卡在人工确认）"""
    return FunctionTool(
        query_question_bank,
        permission=PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="内置题库检索，无需确认",
        ),
    )


def build_toolkit(with_tools: bool = True) -> Toolkit:
    """按需装配工具箱；不开工具时给空 Toolkit（Agent 依旧可正常对话）"""
    if not with_tools:
        return Toolkit()
    return Toolkit(tools=[bank_tool()])
