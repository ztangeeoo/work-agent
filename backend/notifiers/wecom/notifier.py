# -*- coding: utf-8 -*-
"""企业微信群机器人渠道

收到一条**渠道无关**的审核事件，渲染成企业微信 markdown 发出去。

为什么是 markdown 而不是 template_card：markdown 能放链接和颜色，又不需要
额外申请按钮交互权限。群里要看的就是「哪个 PR、什么级别、几处问题、能不能合」，
点链接再决定要不要细看，不需要卡片按钮。

两个企业微信特有的坑：
  - **markdown 里 @人不生效**（那是 text 消息的 mentioned_list），
    所以开发者只作为文字标出来，不做成真提醒
  - **HTTP 200 也可能 errcode != 0**（key 失效、频率超限），必须查 errcode

失败不往上抛：通知是锦上添花，贴评论已经成功了。只返回描述，由派发层汇总。
"""

import os
import time

import httpx

from ...app.schemas.review_report import (
    BLOCKING_SEVERITIES,
    RISK_LABEL,
    SEVERITY_META,
    normalize_decision,
    normalize_risk,
)

# 企业微信 markdown 消息上限 4096 字节，留点余量
MAX_BYTES = 4000

# 级别用 emoji 圆点，群里一眼分辨轻重。
# 颜色标记（<font color>）只认 info / comment / warning / gray 这几个值，
# 拿它表达「阻塞 / 重要」不够分档，用圆点更稳。
_SEV_DOT = {k: v["icon"] for k, v in SEVERITY_META.items()}


# ---------------------------------------------------------------- 配置
def _webhook(options: dict) -> str:
    """渠道 webhook：yaml 里的 options 优先，其次环境变量

    优先 yaml 是为了「一个仓库一份配置」；留空才读 WECOM_WEBHOOK，
    是为了同一个镜像在不同环境能指向不同的群。
    """
    return str((options or {}).get("webhook") or os.environ.get("WECOM_WEBHOOK") or "").strip()


def available(options: dict) -> bool:
    """没配 webhook 就是「这个渠道还没就绪」，派发层会跳过它"""
    return bool(_webhook(options))


# ---------------------------------------------------------------- 渲染
def _clip(text: str, limit: int) -> str:
    t = str(text or "").replace("\r", "").strip().replace("\n", " ")
    return t if len(t) <= limit else t[: max(0, limit - 1)] + "…"


def _mention(name: str) -> str:
    """企业微信 markdown 不支持 @人，这里只把名字标出来"""
    n = str(name or "").strip().lstrip("@")
    return n or "（未指明）"


def _level_line(event: dict) -> str:
    """一行说清「级别 + 数量」，这是群里最先要看的"""
    risk = normalize_risk(event.get("risk"))
    counts = event.get("counts") or {}
    parts = []
    for key in sorted(SEVERITY_META, key=lambda k: SEVERITY_META[k]["order"]):
        if counts.get(key):
            parts.append(f"{_SEV_DOT[key]}{SEVERITY_META[key]['label']} {counts[key]}")
    level_text = " · ".join(parts) or "无问题"
    total = int(event.get("issueCount") or 0)
    return f"**级别**：{RISK_LABEL[risk]}风险 ｜ **问题**：{total} 处（{level_text}）"


def _verdict_line(event: dict) -> str:
    """是否可合并。硬规则：有阻塞/重要就不许说「可以合入」"""
    counts = event.get("counts") or {}
    blocking = sum(int(counts.get(k) or 0) for k in BLOCKING_SEVERITIES)
    decision = normalize_decision(event.get("decision"))
    if blocking and decision != "request_changes":
        decision = "request_changes"

    if decision == "approve":
        return "**结论**：<font color=\"info\">✅ 可以合入</font>"
    if decision == "request_changes":
        tail = f"（{counts.get('blocker', 0)} 阻塞 / {counts.get('major', 0)} 重要未修）" if blocking else ""
        return f"**结论**：<font color=\"warning\">🚫 不建议合并</font>{tail}"
    return "**结论**：<font color=\"comment\">🟡 可合入，建议先改</font>"


def render(event: dict, options: dict) -> str:
    """事件 → 企业微信 markdown 正文

    event 的字段是渠道无关的（见 app/services/notify_event.py），
    这里只负责「企业微信长什么样」，不回头查库。
    """
    event = event or {}
    pr = event.get("pr") or {}
    stats = event.get("stats") or {}
    issues = event.get("issues") or []

    files = int(stats.get("files") or 0)
    adds = int(stats.get("additions") or 0)
    dels = int(stats.get("deletions") or 0)

    lines = [
        f"## 🛡️ PR #{pr.get('number')} {_clip(pr.get('title'), 40)}",
        "",
        _level_line(event),
        _verdict_line(event),
        "",
        f"**仓库**：{pr.get('repo') or '-'}",
        f"**功能**：{_clip(event.get('feature') or '（本次改动未提取到功能描述）', 120)}",
        f"**开发者**：{_mention(pr.get('author'))}",
        f"**代码量**：{files} 个文件 "
        f"<font color=\"info\">+{adds}</font> / <font color=\"warning\">−{dels}</font>"
        f"（净 {adds - dels:+d} 行）",
    ]

    if pr.get("headBranch") or pr.get("baseBranch"):
        lines.append(f"**分支**：{pr.get('headBranch') or '-'} → {pr.get('baseBranch') or '-'}")

    # 只列阻塞与重要：群里不需要完整的问题清单，链接里有
    limit = int((options or {}).get("max_blocking") or 5)
    top = [
        i for i in issues
        if isinstance(i, dict) and str(i.get("severity") or "").lower() in BLOCKING_SEVERITIES
    ]
    if top:
        lines += ["", f"**必须处理的 {len(top)} 处**："]
        for it in top[:limit]:
            loc = str(it.get("file") or "（未指明文件）")
            if it.get("line"):
                loc += ":" + str(it["line"])
            lines.append(
                f"- {_SEV_DOT.get(str(it.get('severity') or '').lower(), '•')} "
                f"`{loc}` {_clip(it.get('title'), 60)}"
            )
        if len(top) > limit:
            lines.append(f"- …另有 {len(top) - limit} 处，见 PR 评论")

    summary = _clip(event.get("summary"), 150)
    if summary:
        lines += ["", f"> {summary}"]

    url = str(pr.get("url") or "").strip()
    if url:
        lines += ["", f"[查看 PR 与完整审核意见]({url})"]

    out = "\n".join(lines)

    # 超长时按「信息价值」由低到高逐级砍，直到进 4096 字节。
    # 顺序：摘要 → 必须处理清单 → 分支。
    # 级别/结论/功能/开发者/代码量/链接一律不动——那几行就是这条消息
    # 存在的理由，砍了它不如不发。
    if len(out.encode("utf-8")) > MAX_BYTES and summary:
        out = "\n".join([ln for ln in lines if ln != f"> {summary}"])
    if len(out.encode("utf-8")) > MAX_BYTES:
        kept, skipping = [], False
        for ln in out.split("\n"):
            if ln.startswith("**必须处理的"):
                skipping = True
                continue
            if skipping:
                if ln.startswith("- "):
                    continue
                skipping = False
            kept.append(ln)
        out = "\n".join(kept)
    if len(out.encode("utf-8")) > MAX_BYTES:
        out = "\n".join([ln for ln in out.split("\n") if not ln.startswith("**分支**")])
    return out


# ---------------------------------------------------------------- 发送
async def post_markdown(url: str, content: str) -> dict:
    """把一段拼好的 markdown 发到指定 webhook（不抛异常）

    单独抽出来是因为「发一条测试消息」和「发一条审核结果」只差正文，
    不该为了测试消息在 send() 里开一条旁路。
    """
    if not url:
        return {"ok": False, "skipped": "未配置 webhook"}
    payload = {"msgtype": "markdown", "markdown": {"content": content}}
    t0 = time.time()
    try:
        # 企业微信走公网，必须尊重代理（与 review_client 同一套规则）；
        # trust_env=False 直连会让公司网络下的请求超时。
        proxy = (
            os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
            or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
        )
        kwargs = {"timeout": 12.0}
        if proxy:
            kwargs["proxy"] = proxy
        else:
            kwargs["trust_env"] = False
        async with httpx.AsyncClient(**kwargs) as client:
            res = await client.post(url, json=payload)
        ms = int((time.time() - t0) * 1000)
        try:
            data = res.json()
        except Exception:
            data = {}
        if res.status_code >= 300:
            return {"ok": False, "error": f"HTTP {res.status_code}", "ms": ms}
        # 企业微信即使 HTTP 200 也可能返回 errcode != 0
        code = data.get("errcode")
        if code not in (0, None):
            return {
                "ok": False,
                "error": f"errcode={code} {data.get('errmsg') or ''}".strip(),
                "ms": ms,
            }
        return {"ok": True, "ms": ms}
    except Exception as e:
        return {"ok": False, "error": str(e), "ms": int((time.time() - t0) * 1000)}


async def send(event: dict, options: dict) -> dict:
    """发一条审核事件。返回值只描述成败，**不抛异常**"""
    url = _webhook(options)
    if not url:
        return {
            "ok": False,
            "skipped": "未配置 webhook（notifier.yaml 的 options.webhook 或 WECOM_WEBHOOK）",
        }
    try:
        content = render(event, options)
    except Exception as e:
        return {"ok": False, "error": "生成消息失败：" + str(e)}
    res = await post_markdown(url, content)
    if res.get("ok"):
        res["content"] = content      # 调用方要打日志 / 排查时用得上
    return res
