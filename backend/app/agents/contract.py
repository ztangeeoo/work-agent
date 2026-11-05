# -*- coding: utf-8 -*-
"""专家插件契约

一个插件 = `backend/plugins/<id>/`，内含 `plugin.yaml` 与流水线实现。
对外只有两种入口，由 `registry` 按 `mode` 选中后调用：

    chat(ctx)        流式对话（/v1/chat/completions），产出 SSE 字符串
    job(payload)     一次性任务（/v1/review、后台轮询），返回 dict

依赖方向单向：插件可以 import 基础设施（runtime / llm / utils / schemas），
但基础设施**不认识任何具体插件**，只按目录发现。这样加一条业务线不用改
app 里的 if 分支。

注意边界：**角色（stage）不是插件**。策划官离开面试官没有消费者，评分官
离开面试官没有输入——它们是同一个能力包里的编排位置，由插件自己按顺序驱动。
"""

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol


@dataclass
class ChatCtx:
    """一次流式对话的全部上下文。

    做成显式字段而不是往 payload 里塞 `_history` 之类的私有键：插件不用猜
    调用方在请求体里埋了什么，调用方也不用记得清理。
    """

    payload: dict
    """原始请求体，插件可读自己的自定义字段"""

    model: Any
    """已构造好的模型客户端。factory 有缓存，同一配置重复取不产生新连接"""

    base: str = ""
    """实际生效的 API 地址（404 补 /v1 重试时会与本轮请求不同）"""

    model_name: str = ""
    cid: str = ""
    """本轮 SSE 的 completion id"""
    created: int = 0
    history: list = field(default_factory=list)
    """已转成 AgentScope Msg 的历史对话（不含本轮提问）"""
    last_user: str = ""
    """本轮用户最后一条文本"""
    has_assistant: bool = False
    """历史里是否已有助手回复。多轮业务线靠它区分「首次」和「续聊」"""
    profile: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    """plugin.yaml 的 options，插件自己的业务配置（不掺环境变量）"""
    _log: Optional[Callable[[str], None]] = None

    def log(self, text: str) -> None:
        if self._log:
            self._log(text)


class ExpertPlugin(Protocol):
    """插件实现需要满足的形状（不强制继承，duck typing 即可）

    `registry.load()` 只检查这两个方法在不在，不做类型校验——插件可以有
    额外导出，也可以只实现其中一个入口（比如纯后台任务型能力包）。
    """

    id: str
    mode: str

    def chat(self, ctx: ChatCtx) -> AsyncIterator[str]:
        """流式产出 SSE 字符串（`data: {...}\\n\\n`）"""
        ...

    async def job(self, payload: dict) -> dict:
        """跑完一次性任务返回结果；失败直接抛异常，由调用方决定状态码"""
        ...


# 供插件按需取用的类型别名（避免每个插件重复写 Callable 长签名）
ChatFn = Callable[[ChatCtx], AsyncIterator[str]]
JobFn = Callable[[dict], Awaitable[dict]]
