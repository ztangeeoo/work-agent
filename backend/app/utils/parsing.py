# -*- coding: utf-8 -*-
"""容错解析：模型返回的「大概是 JSON」的文本 → dict

兼容中转站不支持 response_format 的情况：允许 ```json 包裹、允许前后有解释文字。
"""

import json
import re
from typing import Optional


def try_parse_json(text: str, required: Optional[str] = None) -> Optional[dict]:
    """结构化输出不可用时，从文本里捞 JSON"""
    if not text:
        return None
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    candidates = [t]
    m = re.search(r"\{[\s\S]*\}", t)
    if m:
        candidates.append(m.group(0))
    for c in candidates:
        try:
            obj = json.loads(c)
        except Exception:
            continue
        if isinstance(obj, dict) and (required is None or required in obj):
            return obj
    return None


def try_parse_score(text: str) -> Optional[dict]:
    """结构化输出不可用时，尝试从原文解析 JSON 评分卡"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.M).strip()
    try:
        obj = json.loads(t)
        if isinstance(obj, dict) and "overall" in obj:
            return obj
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\"overall\"[\s\S]*\}", t)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def diff_stats(diff: str) -> dict:
    """diff 文本 → 文件数 / 增删行数（审核报告尾部展示用）"""
    files = 0
    additions = 0
    deletions = 0
    for line in diff.split("\n"):
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return {"files": files, "additions": additions, "deletions": deletions}
