# -*- coding: utf-8 -*-
"""候选人画像 / PR 信息 → 提示词文本片段（各 system prompt 共用的上下文块）"""

PROFILE_DEFAULTS = {
    "track": "前端开发",
    "sub": "随机",
    "level": "中级",
    "exp": "1-3年",
    "industry": "不限",
    "count": 5,
    "lang": "中文",
    "note": "",
    "resumeText": "",
}


def profile_text(p: dict) -> str:
    """结构化画像 → 提示词文本"""
    track = p.get("track") or "前端开发"
    sub = p.get("sub") or "随机"
    level = p.get("level") or "中级"
    exp = p.get("exp") or "1-3年"
    industry = p.get("industry") or "不限"
    lang = p.get("lang") or "中文"
    resume = (p.get("resumeText") or "").strip()

    sub_text = f"{track} / {sub}" if sub and sub != "随机" else f"{track}（细分方向随机）"

    lines = [
        f"- 技术方向：{sub_text}",
        f"- 级别：{level}",
        f"- 工作经验：{exp}",
        f"- 行业背景：{industry}",
        f"- 出题语言：{lang}",
    ]
    if resume:
        lines.append("")
        lines.append("候选人简历原文（出题必须围绕其中的技术栈与项目经历深挖）：")
        lines.append("<<<")
        lines.append(resume[:6000])
        lines.append(">>>")
    return "\n".join(lines)


def pr_brief(pr: dict) -> str:
    """PR 元信息 → 提示词文本（审核三个智能体共用）"""
    return "\n".join([
        f"- 仓库：{pr.get('repo') or '-'}",
        f"- PR：#{pr.get('number') or '-'} {pr.get('title') or ''}".strip(),
        f"- 作者：{pr.get('author') or '-'}",
        f"- 分支：{pr.get('headBranch') or '-'} → {pr.get('baseBranch') or '-'}",
    ])
