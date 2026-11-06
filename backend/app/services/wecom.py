# -*- coding: utf-8 -*-
"""企业微信推送 · 兼容层

实现已搬到 `backend/notifiers/wecom/`（渠道是目录即真相）。这个文件只为
历史调用方保留原来的函数名，内部一律转调渠道层，不再自己渲染或发送。

新代码请直接用：

    from backend.app import notify
    await notify.dispatch(notify_event.build(pr, review))

**为什么还留着**：poller 与 reviews 路由曾直接 import 这里的函数，
把这个文件删掉会让那些调用点同时坏掉；而这个兼容层只有几行，留着更稳。
"""

import os

from .. import config
from ..notify import registry as ch_registry

# 环境变量兜底：没有渠道配置时，至少还能用 WECOM_WEBHOOK 发出去
_ENV_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")


def _module():
    """企业微信渠道的实现模块

    懒 import：NOTIFIERS_ENABLED=0 时渠道目录不会被注册表扫到，但把消息渲出来
    做自检/排障仍然有需求，所以这里按模块路径直接取，不走注册表。
    """
    from importlib import import_module
    return import_module("backend.notifiers.wecom.notifier")


def __getattr__(name: str):
    """把 MAX_BYTES / _clip 这类实现细节转发到渠道模块（PEP 562）

    兼容层不该把实现抄一遍——抄了就有两份，改一处忘一处。这里只做转发，
    真相仍在 backend/notifiers/wecom/notifier.py。
    """
    if name in ("MAX_BYTES", "_clip", "SEVERITY_DOT"):
        return getattr(_module(), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _channel():
    """取企业微信渠道；扫不到（比如 NOTIFIERS_ENABLED=0）就返回 None"""
    return ch_registry.get("wecom")


def _options(webhook: str = "") -> dict:
    """渠道配置 + 调用方临时指定的 webhook"""
    ch = _channel()
    opts = dict(ch.options) if ch else {}
    if webhook:
        opts["webhook"] = webhook
    elif not opts.get("webhook"):
        # yaml 没填就退回环境变量，保证老的 WECOM_WEBHOOK 部署方式仍然可用
        opts["webhook"] = _ENV_WEBHOOK
    return opts


def should_notify(webhook: str = "") -> bool:
    """现在能不能发（渠道启用 + 有 webhook）"""
    ch = _channel()
    if ch is not None and not ch.enabled:
        return False
    return bool(_options(webhook).get("webhook"))


def build_markdown(pr: dict, review: dict) -> str:
    """按企业微信的格式渲染一条消息（不发送）

    保留这个名字是因为测试与调试脚本都按它取用；实现已归渠道。
    """
    from .notify_event import build as build_event

    # 渲染不依赖注册表：渠道被停用 / 总开关关掉时也要能渲出来看格式
    return _module().render(build_event(pr, review), _options())


async def send_markdown(content: str, webhook: str = "") -> dict:
    """直接发一段 markdown（绕过渲染）。

    只给「手工发一条测试消息」这类场景用；正常链路请走 dispatch()。
    """
    url = _options(webhook).get("webhook")
    return await _module().post_markdown(url, content)


async def notify_review(pr: dict, review: dict, webhook: str = "") -> dict:
    """审核结果 → 推一条。保留旧签名，内部走渠道实现。"""
    from .notify_event import build as build_event

    try:
        event = build_event(pr, review)
    except Exception as e:
        return {"ok": False, "error": "生成消息失败：" + str(e)}

    ch = _channel()
    if ch is None:
        return {"ok": False, "skipped": "企业微信渠道未装载（检查 backend/notifiers/wecom/）"}
    if not ch.enabled:
        return {"ok": False, "skipped": "企业微信渠道已停用（notifier.yaml 的 enabled）"}
    opts = _options(webhook)
    mod = _module()
    res = await mod.send(event, opts)
    if res.get("ok"):
        # 兼容：老调用方会读 content 打日志
        res.setdefault("content", mod.render(event, opts))
    return res
