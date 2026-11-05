# -*- coding: utf-8 -*-
"""结果渲染：评分卡 / 审核报告 → Markdown

前端按标题特征识别卡片（`## 📊 回答评分`、`## 🛡️ PR #N`），
改标题就等于改前端的识别规则，动之前先看 web/src/lib/markdown.ts。
"""

from ..schemas.review_report import (
    BLOCKING_SEVERITIES,
    DECISION_LABEL,
    RISK_LABEL,
    SEVERITY_META,
    normalize_decision,
    normalize_risk,
    normalize_severity,
    severity_counts,
)


def render_score_card(card: dict) -> str:
    overall = card.get("overall", "-")
    lines = [f"## 📊 回答评分：{overall} / 100", ""]
    dims = card.get("dimensions") or []
    if dims:
        lines.append("| 维度 | 得分 | 点评 |")
        lines.append("| --- | --- | --- |")
        for d in dims:
            lines.append(f"| {d.get('name', '')} | {d.get('score', '')} | {d.get('comment', '')} |")
        lines.append("")
    for key, title in (("strengths", "✅ 亮点"), ("weaknesses", "⚠️ 不足"), ("suggestions", "💡 改进建议")):
        items = card.get(key) or []
        if items:
            lines.append(f"**{title}**")
            lines.extend(f"- {i}" for i in items)
            lines.append("")
    ref = card.get("reference_answer")
    if ref:
        lines.append("**📖 参考回答要点**")
        lines.append("")
        lines.append(ref)
    return "\n".join(lines).strip()


def _scout_intent(scout: str) -> str:
    """从侦察官概览里抽出「改动意图」那一句（用于回帖开头的功能摘要）"""
    for raw in str(scout or "").splitlines():
        line = raw.strip().lstrip("-*• ").strip()
        line = line.lstrip("0123456789.、) ").strip()
        for tag in ("改动意图：", "改动意图:", "本次改动：", "功能："):
            if line.startswith(tag):
                return line[len(tag):].strip()
    return ""


def _strip_intent_line(scout: str) -> str:
    """把「改动意图」那一行摘掉，避免和上面的「功能」重复；顺手把编号列表改成无序"""
    import re

    out = []
    for raw in str(scout or "").splitlines():
        norm = raw.strip().lstrip("-*• ").strip()
        norm = norm.lstrip("0123456789.、) ").strip()
        if norm.startswith(("改动意图：", "改动意图:", "本次改动：", "功能：")):
            continue
        m = re.match(r"^(\s*)\d+[.、)]\s*(.*)$", raw)
        if m:
            raw = m.group(1) + "- " + m.group(2)
        out.append(raw)
    return "\n".join(out).strip()


def _author_mention(pr: dict) -> str:
    """PR 作者的 @ 提及；拿不到就退化成名字"""
    author = str(pr.get("author") or "").strip()
    if not author:
        return ""
    return "@" + author.lstrip("@")


def render_review_md(pr: dict, report: dict, model_name: str, elapsed_ms: int) -> str:
    """审核报告 → Markdown（前端按 `## 🛡️ PR #N` 识别为审核卡片）"""
    issues = report.get("issues") or []
    counts = severity_counts(issues)

    risk = normalize_risk(report.get("risk"))
    decision = normalize_decision(report.get("decision"))
    blocking = sum(counts[k] for k in BLOCKING_SEVERITIES)
    # 兜底：问题清单里有阻塞/重要，就不许渲染成「可以合入」
    if blocking and decision != "request_changes":
        decision = "request_changes"
        risk = "high" if counts["blocker"] else risk

    count_text = "、".join(
        f"{counts[k]} {SEVERITY_META[k]['label']}" for k in SEVERITY_META if counts[k]
    ) or "无"

    author = str(pr.get("author") or "").strip()
    mention = _author_mention(pr)
    title = (pr.get("title") or "").replace("\n", " ").strip()

    # 开发人标注：每条 bug 都写清该谁改（默认 PR 作者）
    owner_label = mention or author or "（未指明）"

    lines = [
        f"## 🛡️ PR #{pr.get('number')} {title}".rstrip(),
        "",
        f"> 仓库 {pr.get('repo') or '-'} ｜ 作者 {author or '-'} "
        f"｜ 分支 {pr.get('headBranch') or '-'} → {pr.get('baseBranch') or '-'}",
        f"> 风险：{RISK_LABEL[risk]} ｜ 结论：{DECISION_LABEL[decision]} "
        f"｜ 问题：{len(issues)} 处（{count_text}）",
        "",
    ]

    # 结论横幅：合并前先看这一行
    if decision == "request_changes":
        lines += [
            f"> 🚫 **不建议合并**"
            + (f"（{counts['blocker']} 处阻塞 / {counts['major']} 处重要）" if blocking else "")
            + (f"：{mention} 请先处理下面标 🔴/🟠 的问题" if mention else "：请先处理下面标 🔴/🟠 的问题"),
            "",
        ]
    elif decision == "approve":
        lines += ["> ✅ **可以合入**：未发现阻塞或重要问题", ""]
    else:
        lines += ["> 🟡 **可合入**，建议先处理次要问题", ""]

    # 开头先讲这次提交做了什么
    scout = (report.get("scout") or "").strip()
    intent = _scout_intent(scout)
    if intent or scout:
        lines.append("**📌 本次提交做了什么**")
        lines.append("")
        if intent:
            lines.append(f"- **功能**：{intent}")
            scout = _strip_intent_line(scout)
        if scout:
            lines += ["", scout[:800]]
        lines.append("")

    summary = (report.get("summary") or "").strip()
    if summary:
        lines += ["**总体结论**", "", summary, ""]

    if issues:
        lines.append("### 问题清单")
        lines.append("")
        grouped: dict[str, list] = {k: [] for k in SEVERITY_META}
        for it in issues:
            grouped[normalize_severity(it.get("severity"))].append(it)
        idx = 0
        for sev in sorted(SEVERITY_META, key=lambda k: SEVERITY_META[k]["order"]):
            group = grouped[sev]
            if not group:
                continue
            meta = SEVERITY_META[sev]
            note = "（必须改，改完再合）" if sev in BLOCKING_SEVERITIES else ""
            lines.append(f"#### {meta['icon']} {meta['label']}（{len(group)}）{note}")
            lines.append("")
            for it in group:
                idx += 1
                f = str(it.get("file") or "").strip() or "（未指明文件）"
                ln = it.get("line") or 0
                loc = f"`{f}:{ln}`" if ln else f"`{f}`"
                dev = str(it.get("developer") or it.get("owner") or "").strip()
                dev_label = ("@" + dev.lstrip("@")) if dev else owner_label
                lines.append(
                    f"{idx}. **{loc}** {str(it.get('title') or '').strip()}"
                    f" ｜ 开发人 {dev_label}"
                )
                lines.append(f"   - 问题：{str(it.get('detail') or '').strip()}")
                sug = str(it.get("suggestion") or "").strip()
                if sug:
                    lines.append("   - 建议：" + sug.replace("\n", "\n     "))
            lines.append("")
    else:
        lines += ["**问题清单**：未发现明显问题 ✅", ""]

    notes = (report.get("notes") or "").strip()
    if notes:
        lines += [f"> ⚠️ {notes}", ""]

    positives = report.get("positives") or []
    if positives:
        lines.append("**✅ 亮点**")
        lines.append("")
        lines += [f"- {p}" for p in positives]
        lines.append("")

    suggestions = report.get("suggestions") or []
    if suggestions:
        lines.append("**💡 建议**")
        lines.append("")
        lines += [f"- {s}" for s in suggestions]
        lines.append("")

    stats = report.get("stats") or {}
    lines.append("---")
    lines.append(
        f"🔍 侦察官 → 审核官 → 裁决官 ｜ {stats.get('files', 0)} 个文件 "
        f"(+{stats.get('additions', 0)}/−{stats.get('deletions', 0)}) ｜ {model_name} ｜ "
        f"{(elapsed_ms / 1000):.1f}s"
    )
    if pr.get("url"):
        lines.append(f"🔗 {pr.get('url')}")
    return "\n".join(lines).strip()


def render_comment_body(review: dict, pr: dict | None = None) -> str:
    """审核报告 → 可直接回帖到 PR 的评论正文（自动回帖与手动回帖共用）

    比报告多两件事：
      1. 打头一行 @ 开发人 + 结论，必要时写清「不建议合并」
      2. 正文沿用报告（开头已有「本次提交做了什么」）
    """
    review = review or {}
    # 没有显式传 pr 时，review 自己就带 author（store 的 SELECT 已 JOIN pr）
    pr = pr or {"author": review.get("author") or ""}
    body = str(review.get("reportMd") or "").strip()

    issues = review.get("issues")
    if not isinstance(issues, list):
        issues = []
    counts = severity_counts(issues)
    blocking = sum(counts[k] for k in BLOCKING_SEVERITIES)
    decision = normalize_decision(review.get("decision"))
    if blocking and decision != "request_changes":
        decision = "request_changes"

    mention = _author_mention(pr)
    head = []
    if decision == "request_changes":
        detail = f"（{counts['blocker']} 处阻塞 / {counts['major']} 处重要）" if blocking else ""
        who = mention or "开发同学"
        head.append(f"{who} 🚫 **这份改动不建议合并**{detail}，下面每条问题都标了开发人，改完再合。")
    elif decision == "approve":
        head.append((mention + " " if mention else "") + "✅ **审核通过，可以合入。**")
    else:
        head.append((mention + " " if mention else "") + "🟡 **可以合入**，下面几处建议一并改掉。")

    tail = [
        "",
        "---",
        "🤖 由 AI 代码审核官自动生成（侦察官 → 审核官 → 裁决官）。",
        "请结合业务上下文判断，机器意见仅供参考。",
    ]
    return "\n".join(head + ["", body] + tail).strip()
