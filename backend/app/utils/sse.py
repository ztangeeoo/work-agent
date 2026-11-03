# -*- coding: utf-8 -*-
"""SSE 编码：OpenAI 兼容的 chunk + 自定义的阶段事件

阶段事件放在 `agent_stage` 这个非标准字段里，标准 OpenAI 客户端会安全忽略，
前端则据此驱动「智能体流水线」面板。
"""

import json
import time
from typing import Optional

from ..core.stages import stage_meta


def sse_chunk(content: str, model: str, cid: str, created: int, finish: Optional[str] = None) -> str:
    delta = {"content": content} if content else {}
    if finish:
        delta["finish_reason"] = finish
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def sse_stage(stage: str, status: str, ms: Optional[int] = None, detail: str = "") -> str:
    """阶段事件：不放在 choices 里，标准 OpenAI 客户端会安全忽略。

    status: running / done / skipped / tool / error
    """
    meta = stage_meta(stage)
    info = {
        "stage": stage,
        "label": meta["label"],
        "icon": meta["icon"],
        "status": status,
        "ts": int(time.time() * 1000),
    }
    if ms is not None:
        info["ms"] = ms
    if detail:
        info["detail"] = detail
    return "data: " + json.dumps({"agent_stage": info}, ensure_ascii=False) + "\n\n"
