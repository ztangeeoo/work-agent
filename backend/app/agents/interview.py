# -*- coding: utf-8 -*-
"""兼容壳：面试流水线的实现已搬进 `backend/plugins/interview/`

保留 `stream_interview` / `stream_with_retry` 两个名字，是因为 main.py 与部分
外部脚本按 `A.stream_interview(...)` 取用。新代码请直接用：

    from backend.app.agents import registry
    async for chunk in registry.for_mode("interview").chat(payload):

或（只想要默认业务线时）`registry.default().chat(payload)`。
"""

from typing import Optional

from backend.plugins.interview.pipeline import chat as _chat
from .registry import build_ctx
from .runtime import stream_with_retry  # noqa: F401  兼容出口：实现本来就在 runtime


async def stream_interview(payload: dict, base_override: Optional[str] = None):
    """按请求体跑一遍面试流水线（薄包装，只为兼容老调用方）"""
    async for chunk in _chat(build_ctx(payload, base_override)):
        yield chunk
