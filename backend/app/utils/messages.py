# -*- coding: utf-8 -*-
"""OpenAI 消息格式 → AgentScope Msg

注意 AgentScope 2.0 的语义：`reply_stream(inputs)` 会把这些消息**追加**进
`state.context`，所以历史对话只能通过 inputs 传一次（见 agents/runtime.make_agent）。
"""

from agentscope.message import AssistantMsg, Msg, UserMsg


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
    """取最后一条用户消息（多模态分段会被拼成纯文本）"""
    for m in reversed(messages):
        if m.get("role") == "user":
            c = m.get("content") or ""
            if isinstance(c, list):
                c = "".join(x.get("text", "") for x in c if isinstance(x, dict))
            return str(c)
    return ""


def has_assistant_reply(messages: list[dict]) -> bool:
    """历史里是否已经有智能体的回答（决定要不要跑策划官）"""
    return any(m.get("role") == "assistant" for m in messages)
