# -*- coding: utf-8 -*-
"""审核事件 → 渠道无关的「通知事件体」

为什么要有这一层：渠道要的是「级别 / 数量 / 能否合并 / 链接 / 功能 / 开发者 /
代码量」，这些散在 pr、review、stats、issues 四处。如果让每个渠道自己去翻
review 记录、自己算 severity_counts，那么：

  - 加一个新渠道就要把口径抄一遍，抄错一处就出现「企微说 10 处、钉钉说 8 处」
  - 硬规则（有阻塞就不许说可以合入）也得在每个渠道里重复实现

所以统一在**入口处**算一次，渠道只负责渲染。渠道拿到的是成品数据，
不做业务判断。
"""

from ..schemas.review_report import (
    BLOCKING_SEVERITIES,
    normalize_decision,
    normalize_risk,
    severity_counts,
)

# 一个事件里我们认的字段（渠道可以按需取，取不到就是空）
EVENT_FIELDS = (
    "kind", "repo", "pr", "risk", "decision", "issueCount", "counts",
    "blocking", "mergeable", "summary", "feature", "stats", "issues",
    "at", "trigger",
)


def build(pr: dict, review: dict, *, repo: dict | None = None,
          trigger: str = "auto") -> dict:
    """拼一条通知事件

    Args:
        pr:     PR 信息（number / title / author / url / headBranch / baseBranch / repo）
        review: 审核记录（risk / decision / summary / feature / stats / issues）
        repo:   仓库信息（owner / name），pr 里没带 repo 时用来拼
        trigger: auto=自动回帖后推送，manual=页面点回帖
    """
    pr = pr or {}
    review = review or {}

    issues = review.get("issues")
    if not isinstance(issues, list):
        issues = []
    counts = severity_counts(issues)
    blocking = sum(int(counts.get(k) or 0) for k in BLOCKING_SEVERITIES)

    risk = normalize_risk(review.get("risk"))
    decision = normalize_decision(review.get("decision"))

    # 硬规则在这里统一执行一次，渠道不再各算各的：
    # 有阻塞/重要就不能说「可以合入」；有 blocker 时风险至少是 high。
    if blocking and decision != "request_changes":
        decision = "request_changes"
    if counts.get("blocker"):
        risk = "high"

    repo_name = str(pr.get("repo") or "").strip()
    if not repo_name and repo:
        repo_name = (str(repo.get("owner") or "") + "/" + str(repo.get("name") or "")).strip("/")

    return {
        "kind": "review",
        "repo": repo_name,
        "pr": {
            "number": pr.get("number"),
            "title": str(pr.get("title") or ""),
            "author": str(pr.get("author") or review.get("author") or ""),
            "url": str(pr.get("url") or review.get("prUrl") or ""),
            "repo": repo_name,
            "headBranch": str(pr.get("headBranch") or ""),
            "baseBranch": str(pr.get("baseBranch") or ""),
        },
        "risk": risk,
        "decision": decision,
        "issueCount": len(issues),
        "counts": counts,
        "blocking": blocking,
        # 给「要不要现在去看」一个布尔值：有的渠道（内部系统、钉钉看板）
        # 只想要一个可筛选的字段，不想自己解释 approve/comment 的语义
        "mergeable": decision in ("approve", "comment") and blocking == 0,
        "summary": str(review.get("summary") or ""),
        "feature": str(review.get("feature") or ""),
        "stats": dict(review.get("stats") or {}),
        # 只带渲染需要的最小问题集，不把整份报告塞进事件里
        "issues": [
            {
                "severity": str(i.get("severity") or ""),
                "file": str(i.get("file") or ""),
                "line": i.get("line"),
                "title": str(i.get("title") or ""),
            }
            for i in issues if isinstance(i, dict)
        ],
        "at": _now_ms(),
        "trigger": str(trigger or "auto"),
    }


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)
