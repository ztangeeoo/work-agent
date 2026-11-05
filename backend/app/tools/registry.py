# -*- coding: utf-8 -*-
"""工具注册：Agent 可调用的函数（AgentScope FunctionTool + Toolkit）+ 技能装载

**装哪些工具由插件清单声明**，不再靠「传了 repo 就装仓库工具」这种启发式：

    # plugin.yaml
    stages:
      - id: interviewer
        tools: [question_bank]
      - id: advisor
        tools: [repo_readonly]

`tools: []` = 明确不装；**不写 tools** = 插件还没接管，退回旧的启发式
（有 repo 装仓库只读、没 repo 装题库），保证老插件行为不变。

全部显式 ALLOW，避免无头服务卡在「人工确认」上。
"""

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool, Toolkit

from ..core.log import log
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


# 工具名 → 工厂。名字是 plugin.yaml 里写的那个，新增工具在这里登记一行。
# 接收 repo 是因为部分工具需要服务端绑定的仓库上下文（模型无权切换仓库）。
TOOL_FACTORIES = {
    "question_bank": lambda repo=None: [bank_tool()],
    "repo_readonly": lambda repo=None: repo_tools(repo) if repo else [],
}


def _declared_tools(stage: str):
    """插件为该角色声明的工具名；未声明返回 None"""
    try:
        from ..agents import registry
        return registry.tools_of_stage(stage)
    except Exception as e:  # 插件目录坏了不该让 Agent 构造失败
        log(f"读取插件工具声明失败（stage={stage}）：{e}")
        return None


def build_toolkit(with_tools: bool = True, repo: dict | None = None,
                  stage: str = "") -> Toolkit:
    """按需装配工具箱。

    - with_tools=False：不装工具，Agent 依旧可正常对话；
    - 工具名来自插件清单的 `stages[].tools`；
    - repo：审核官场景下由服务端绑定的仓库，`repo_readonly` 工具需要它；
    - stage：当前角色（interviewer / grader / scout …），决定装哪些工具与技能。

    技能与工具互相独立：策划官、评分官这类没有工具的角色，也能靠技能拿到纯文本
    的方法论——技能是提示词，不是可调用工具。
    """
    tools: list[FunctionTool] = []
    if with_tools:
        names = _declared_tools(stage)
        if names is None:
            # 老行为：没声明就按有没有仓库上下文来判断
            names = ["repo_readonly"] if repo else ["question_bank"]
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            factory = TOOL_FACTORIES.get(name)
            if factory is None:
                log(f"插件声明的工具不存在：{name}（stage={stage}），已跳过")
                continue
            tools.extend(factory(repo))

    # 该角色可见的技能目录；为空时不传该参数，Toolkit 行为与接入前一致
    skill_dirs = for_stage(stage)

    kwargs: dict = {}
    if tools:
        kwargs["tools"] = tools
    if skill_dirs:
        kwargs["skills_or_loaders"] = skill_dirs
    return Toolkit(**kwargs)
