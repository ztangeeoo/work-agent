# -*- coding: utf-8 -*-
"""结果渲染：评分卡 / 审核报告 → Markdown

前端按标题特征识别卡片（`## 📊 回答评分`、`## 🛡️ PR #N`），
改标题就等于改前端的识别规则，动之前先看 web/src/lib/markdown.ts。
"""

from ..schemas.review_report import (
    DECISION_LABEL,
    RISK_LABEL,
    SEVERITY_META,
    normalize_decision,
    normalize_risk,
    normalize_severity,
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


def render_review_md(pr: dict, report: dict, model_name: str, elapsed_ms: int) -> str:
    """审核报告 → Markdown（前端按 `## 🛡️ PR #N` 识别为审核卡片）"""
    issues = report.get("issues") or []
    counts = {k: 0 for k in SEVERITY_META}
    for it in issues:
        counts[normalize_severity(it.get("severity"))] += 1

    risk = normalize_risk(report.get("risk"))
    decision = normalize_decision(report.get("decision"))
    count_text = "、".join(
        f"{counts[k]} {SEVERITY_META[k]['label']}" for k in SEVERITY_META if counts[k]
    ) or "无"

    title = (pr.get("title") or "").replace("\n", " ").strip()
    lines = [
        f"## 🛡️ PR #{pr.get('number')} {title}".rstrip(),
        "",
        f"> 仓库 {pr.get('repo') or '-'} ｜ 作者 {pr.get('author') or '-'} "
        f"｜ 分支 {pr.get('headBranch') or '-'} → {pr.get('baseBranch') or '-'}",
        f"> 风险：{RISK_LABEL[risk]} ｜ 结论：{DECISION_LABEL[decision]} "
        f"｜ 问题：{len(issues)} 处（{count_text}）",
        "",
    ]

    summary = (report.get("summary") or "").strip()
    if summary:
        lines += ["**总体结论**", "", summary, ""]

    scout = (report.get("scout") or "").strip()
    if scout:
        lines += ["**改动概览**", "", scout[:800], ""]

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
            lines.append(f"#### {meta['icon']} {meta['label']}（{len(group)}）")
            lines.append("")
            for it in group:
                idx += 1
                f = str(it.get("file") or "").strip() or "（未指明文件）"
                ln = it.get("line") or 0
                loc = f"`{f}:{ln}`" if ln else f"`{f}`"
                lines.append(f"{idx}. **{loc}** {str(it.get('title') or '').strip()}")
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
