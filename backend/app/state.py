# -*- coding: utf-8 -*-
"""进程内共享状态

前端每次改配置都会 POST /api/model-config，这里留一份最新的，供：
  - 对话适配（/v1/chat/completions 兜底取值）
  - PR 轮询器（定时审核用的 base/key/model）
共享是刻意的：这是单进程本地服务，一份配置对应一个使用者。
"""

from typing import Optional

MODEL_CFG: dict = {
    "base": "", "key": "", "model": "", "profile": None,
    "agentMode": "interview", "review": None, "runId": "", "repoId": "",
}

_poller = None


def model_cfg() -> dict:
    return MODEL_CFG


def set_poller(poller) -> None:
    """由 main 在启动时注入（轮询器依赖审核流水线，避免模块级循环导入）"""
    global _poller
    _poller = poller


def get_poller():
    if _poller is None:
        raise RuntimeError("轮询调度尚未启动")
    return _poller


def has_poller() -> bool:
    return _poller is not None
