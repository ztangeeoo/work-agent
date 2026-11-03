# -*- coding: utf-8 -*-
"""路由公共依赖：用户标识、错误响应、降级判断"""

import asyncio
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse

from .. import store


def user_key(request: Request) -> str:
    """轻量多用户隔离：前端传 x-user-key，没传就是本机单人模式"""
    return str(request.headers.get("x-user-key") or "local")[:64]


def err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"message": message}}, status_code=status)


def db_blocked() -> Optional[JSONResponse]:
    """SQLite 不可用时统一返回 503，让前端明确回退到浏览器本地存储"""
    if store.available():
        return None
    return JSONResponse(
        {"dbAvailable": False,
         "error": {"message": "SQLite 不可用，已回退到浏览器本地存储：" + store.unavailable_reason()}},
        status_code=503,
    )


def swallow(task: asyncio.Task) -> None:
    """后台任务结束回调：取走异常，避免 "never retrieved" 噪音。"""
    if task.cancelled():
        return
    task.exception()
