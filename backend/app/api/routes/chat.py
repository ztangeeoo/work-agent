# -*- coding: utf-8 -*-
"""对话与审核接口（OpenAI 兼容层）

/v1/chat/completions  : 多智能体出题/追问/评分，SSE 流式
/v1/review            : 代码审核流水线，一次性返回结构化报告 + Markdown

两者都在「预取第一块」处做错误分流：模型配置/连接有问题时直接返回 JSON 502，
而不是先返回 200 再在流里塞一段错误文本——前端才能给出准确的提示。
"""

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from ...agents.interview import stream_with_retry
from ...agents.review import run_review
from ...core.errors import fmt_exc
from ...core.log import log
from ...utils.sse import sse_chunk

router = APIRouter(tags=["chat"])


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
        result = await run_review(payload)
        return JSONResponse({"ok": True, **result})
    except ValueError as e:
        return JSONResponse({"error": {"message": str(e)}}, status_code=400)
    except Exception as e:
        log("审核失败：" + fmt_exc(e))
        return JSONResponse({"error": {"message": fmt_exc(e)}}, status_code=502)
