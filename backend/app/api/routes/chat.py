# -*- coding: utf-8 -*-
"""对话与任务接口（OpenAI 兼容层）

/v1/chat/completions  : 按 `agent_mode` 选插件；不传或未命中则走内置面试流水线，SSE 流式
/v1/review            : 代码审核插件的一次性任务入口，返回结构化报告 + Markdown

两者都在「预取第一块」处做错误分流：模型配置/连接有问题时直接返回 JSON 502，
而不是先返回 200 再在流里塞一段错误文本——前端才能给出准确的提示。

**这里不再有 `if agent_mode == "review"` 之类的分支**：加一条业务线只加一个
插件目录，分派逻辑本身不动。
"""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...agents import registry
from ...agents.runtime import stream_with_retry
from ...core.errors import fmt_exc
from ...core.log import log
from ...utils.sse import sse_chunk

router = APIRouter(tags=["chat"])

JOB_MODE = "review"
"""一次性任务路由对应的能力包 mode"""


def _plugin_for(payload: dict):
    """按 agent_mode 选能力包，返回 (plugin, 错误文案)

    区分两种「没有插件」：
      - 没传 agent_mode    → 正常回落内置流水线，不算错误
      - 传了但找不到/撞车  → 是配置问题，直接报出来，不要静默回落
    """
    mode = str(payload.get("agent_mode") or "").strip()
    if not mode:
        return None, None
    try:
        plugin = registry.for_mode(mode)
    except LookupError as e:
        return None, str(e)
    if plugin is None:
        return None, f"没有声明 mode={mode} 的插件（检查 backend/plugins/*/plugin.yaml）" + _broken_hint()
    return plugin, None


def _broken_hint() -> str:
    """插件目录被跳过时，把原因带进错误文案——yaml 写错是最常见的情况"""
    try:
        broken = registry.problems()
    except Exception:
        return ""
    if not broken:
        return ""
    return "；另有 " + "、".join(f"{b['id']}（{b['problem']}）" for b in broken) + " 未能加载"


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse({"error": {"message": "请求体解析失败：" + str(e)}}, status_code=400)

    if not payload.get("model"):
        return JSONResponse({"error": {"message": "缺少模型名称"}}, status_code=400)
    if not payload.get("base"):
        return JSONResponse({"error": {"message": "缺少 API 地址"}}, status_code=400)
    if not isinstance(payload.get("messages"), list) or not payload.get("messages"):
        return JSONResponse({"error": {"message": "缺少 messages"}}, status_code=400)

    plugin, problem = _plugin_for(payload)
    if problem:
        log("插件分派失败：" + problem)
        return JSONResponse({"error": {"message": problem}}, status_code=500)

    if plugin is not None:
        # 插件入口签名固定为 chat(payload, base_override)，与内置流水线共用重试口径
        gen = stream_with_retry(payload, spawn=lambda p, override: plugin.chat(p, override))
    else:
        gen = stream_with_retry(payload)

    # 预取第一块：模型配置/连接有问题时直接返回 JSON 错误，前端能读到明确原因
    try:
        first = await gen.__anext__()
    except StopAsyncIteration:
        return JSONResponse({"error": {"message": "智能体没有返回任何内容"}}, status_code=502)
    except Exception as e:
        log("请求失败：" + fmt_exc(e))
        return JSONResponse({"error": {"message": fmt_exc(e)}}, status_code=502)

    async def body():
        if first:
            yield first
        try:
            async for chunk in gen:
                yield chunk
        except Exception as e:
            # 流式中途出错：把错误作为一条内容发出去，用户能在对话里看到
            yield sse_chunk(
                "\n\n❌ " + fmt_exc(e),
                payload.get("model", ""),
                "chatcmpl-error",
                int(time.time()),
                finish="stop",
            )
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/v1/review")
async def review_pr(request: Request):
    """代码审核：侦察官 → 审核官 → 裁决官，返回结构化报告 + Markdown"""
    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse({"error": {"message": "请求体解析失败：" + str(e)}}, status_code=400)

    if not payload.get("model"):
        return JSONResponse({"error": {"message": "缺少模型名称"}}, status_code=400)
    if not payload.get("base"):
        return JSONResponse({"error": {"message": "缺少 API 地址"}}, status_code=400)
    if not str(payload.get("diff") or "").strip():
        return JSONResponse({"error": {"message": "缺少 diff 内容"}}, status_code=400)

    try:
        plugin = registry.for_mode(JOB_MODE)
    except LookupError as e:
        return JSONResponse({"error": {"message": str(e)}}, status_code=500)
    if plugin is None:
        return JSONResponse(
            {"error": {"message": f"没有声明 mode={JOB_MODE} 的插件"}}, status_code=500
        )

    try:
        result = await plugin.job(payload)
        return JSONResponse({"ok": True, **result})
    except ValueError as e:
        return JSONResponse({"error": {"message": str(e)}}, status_code=400)
    except Exception as e:
        log("审核失败：" + fmt_exc(e))
        return JSONResponse({"error": {"message": fmt_exc(e)}}, status_code=502)
