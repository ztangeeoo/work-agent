# -*- coding: utf-8 -*-
"""代码审核官 · 兼容壳

实现已搬到 `backend/plugins/review/pipeline.py`——**插件目录是真相**。
这个文件只为历史调用方保留 `run_review` 这个名字：`main.py` 的轮询注入、
backend/scripts 下的脚本、外部测试都还按 `app.agents.review.run_review` 取用。

新代码请直接用注册表：

    from backend.app.agents import registry
    result = await registry.for_mode("review").job(payload)
"""

from .registry import for_mode


def _plugin():
    plugin = for_mode("review")
    if plugin is None:
        raise RuntimeError(
            "找不到 review 插件：检查 backend/plugins/review/plugin.yaml 是否存在且能被解析"
        )
    return plugin


async def run_review(payload: dict) -> dict:
    """执行代码审核流水线，返回 {report, reportMd, model}"""
    return await _plugin().job(payload)
