# -*- coding: utf-8 -*-
"""审核事件中心：轮询状态 + 审核完成广播

两个约束：
  - 事件保留最近 REVIEW_EVENT_MAX 条，页面晚开时先补发最近一批（不用等下一轮）
  - 订阅者用 asyncio.Queue，广播失败就丢弃该订阅者（客户端断开不该拖垮轮询）
"""

import asyncio

REVIEW_EVENT_MAX = 120

_clients: set[asyncio.Queue] = set()
_log: list[dict] = []


def broadcast(ev: dict) -> None:
    """向所有订阅者推送一条事件，并留档"""
    _log.append(ev)
    if len(_log) > REVIEW_EVENT_MAX:
        del _log[: len(_log) - REVIEW_EVENT_MAX]
    for q in list(_clients):
        try:
            q.put_nowait(ev)
        except Exception:
            _clients.discard(q)


def recent(n: int = 30) -> list[dict]:
    """最近 n 条事件（新订阅者补发用）"""
    return _log[-n:]


def add_client(q: asyncio.Queue) -> None:
    _clients.add(q)


def remove_client(q: asyncio.Queue) -> None:
    _clients.discard(q)
