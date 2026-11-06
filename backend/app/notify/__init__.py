# -*- coding: utf-8 -*-
"""通知派发：把一条审核结果发给所有启用的渠道

调用方（自动回帖的 poller、手动回帖的接口）只说「这条审核结果出来了」，
不关心发给谁、发到哪儿。加渠道是往 `backend/notifiers/` 加目录，不是改这里。

三个原则：
  - **一个渠道失败不影响其他渠道**：逐个 try，互不牵连
  - **失败不往上抛**：通知是好事，不该让审核或回帖失败
  - **全都没发成就说清为什么**：返回里带 skipped / error，页面与日志能看出是
    「没配 webhook」还是「key 失效了」
"""

from typing import Optional

from . import registry


async def dispatch(event: dict, only: Optional[list[str]] = None) -> dict:
    """把一条事件发给所有可用渠道

    Args:
        event: 渠道无关的事件体（谁、什么 PR、级别、问题数、结论、功能、代码量…），
               各渠道自己决定怎么渲染
        only:  只发这几个渠道 id（调试用）；None = 全部启用的

    Returns:
        {
          "ok": bool,          # 是否至少有一个渠道发成功
          "sent": [ {id, name, ok, ms, error} ... ],
          "skipped": [ {id, name, reason} ... ],
          "error": str,        # 全都没发成时的汇总原因（调用方写日志用）
        }
    """
    channels = registry.select()
    if only is not None:
        want = {str(x) for x in only}
        channels = [c for c in channels if c.id in want]

    sent: list[dict] = []
    skipped: list[dict] = []

    if not channels:
        return {
            "ok": False,
            "sent": [],
            "skipped": [],
            "error": _why_none(),
        }

    for ch in channels:
        try:
            res = await ch.notify(event)
        except Exception as e:
            # 实现方本该自己吞掉异常，这里只是最后一道网
            res = {"ok": False, "error": f"{type(e).__name__}: {e}"}

        res = res if isinstance(res, dict) else {"ok": bool(res)}
        item = {
            "id": ch.id,
            "name": ch.name,
            "ok": bool(res.get("ok")),
            "ms": int(res.get("ms") or 0),
            "error": str(res.get("error") or ""),
        }
        # skipped 只在有值时出现：它是「这次没发，因为没配置」，
        # 和「发了但失败」是两件事，混在一个 error 字符串里就分不清了
        if res.get("skipped"):
            reason = str(res["skipped"])
            item["skipped"] = reason
            if not item["ok"]:
                skipped.append({"id": ch.id, "name": ch.name, "reason": reason})
        sent.append(item)

    won = [s for s in sent if s["ok"]]
    if won:
        return {"ok": True, "sent": sent, "skipped": skipped, "error": ""}

    # 全都没成：把各渠道的原因串起来，别只报「发送失败」
    reasons = []
    for s in sent:
        r = s.get("error") or s.get("skipped") or ""
        if r:
            reasons.append(f"{s['name']}：{r}")
    return {
        "ok": False,
        "sent": sent,
        "skipped": skipped,
        "error": "；".join(reasons) or "所有渠道都没发出去",
    }


def _why_none() -> str:
    """一个渠道都用不了时，说清是哪种情况"""
    if not registry.channels_config_enabled():
        return "通知渠道已关闭（NOTIFIERS_ENABLED=0）"
    chans = registry.all_channels()
    if not chans:
        probs = registry.problems()
        if probs:
            return "通知渠道配置有误：" + "、".join(
                f"{p['id']}（{p['problem']}）" for p in probs
            )
        return "没有配置任何通知渠道（backend/notifiers/ 下没有 notifier.yaml）"
    disabled = [c.name for c in chans if not c.enabled]
    unavailable = [c.name for c in chans if c.enabled and not c.available()]
    if unavailable:
        return "渠道未就绪：" + "、".join(unavailable) + "（webhook 等配置未填写）"
    if disabled:
        return "渠道都被停用了：" + "、".join(disabled)
    return "没有可用的通知渠道"
