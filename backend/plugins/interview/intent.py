# -*- coding: utf-8 -*-
"""面试业务线内部的意图路由：最后一条用户消息 → 出题 / 评分

这是**同一条业务线的内部分支**，不是插件边界——所以它跟着 interview 插件走，
而不是留在 app/agents/ 里当成通用能力。

规则刻意保持极简：它只省一次模型调用，判错了用户还能继续追问。
关键词表放在 plugin.yaml 的 options.score_keywords，改话术不用改代码。
"""

import re
from typing import Optional

INTENT_INTERVIEW = "interview"
INTENT_SCORE = "score"

# plugin.yaml 读不到时的兜底，与 yaml 里的默认值保持一致
DEFAULT_KEYWORDS = ("评分", "评估", "打个分", "评价一下", "点评一下", "复盘",
                    "我答得", "看看我的回答", "帮我看看答")


def route_intent(user_text: str, has_assistant_msg: bool = False,
                 keywords: Optional[list] = None) -> str:
    """根据最后一条用户消息判断走哪个 Agent

    `has_assistant_msg` 目前不参与判断，保留参数是为了以后需要「首次提问
    不该触发评分」这类规则时不改调用方签名。
    """
    words = [str(k).strip() for k in (keywords or DEFAULT_KEYWORDS) if str(k).strip()]
    if not words:
        return INTENT_INTERVIEW
    if re.search("|".join(re.escape(w) for w in words), user_text or ""):
        return INTENT_SCORE
    return INTENT_INTERVIEW
