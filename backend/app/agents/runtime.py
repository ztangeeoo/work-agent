# -*- coding: utf-8 -*-
"""Agent 运行时：构造、流式驱动与状态前缀

所有 Agent 都是「每次请求新建、用完即弃」——服务无状态，多人并发不会串场。
"""

from typing import Optional

from agentscope.agent import Agent
from agentscope.event import ReplyEndEvent, TextBlockDeltaEvent, ToolCallStartEvent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.types import ReplyFinishedReason

from ..tools.registry import build_toolkit


def make_agent(name: str, sys_prompt: str, model: OpenAIChatModel, with_tools: bool) -> Agent:
    """构造一个全空状态的 Agent（无状态服务，每次请求新建）。

    注意 AgentScope 2.0 的语义：`reply_stream(inputs)` 会把 inputs **追加**进
    `state.context`，所以历史对话只应该通过 inputs 传一次。之前这里既预置
    `state.context=history` 又传 `inputs=history`，导致每轮上下文翻倍、最后一条
    用户消息出现三次（既费 token 也干扰模型判断）。现在统一走 inputs。
    """
    return Agent(
        name=name,
        system_prompt=sys_prompt,
        model=model,
        toolkit=build_toolkit(with_tools),
    )


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
