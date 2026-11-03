# -*- coding: utf-8 -*-
"""意图路由：最后一条用户消息 → 走哪个智能体

只有两类：出题/追问走面试官，让「评一下我这回答」走评分官。
规则保持极简——它只是省一次模型调用，判错了用户还能继续追问。
"""

import re

_SCORE_PAT = re.compile(r"评分|评估|打个分|评价一下|点评一下|复盘|我答得|看看我的回答|帮我看看答")

INTENT_INTERVIEW = "interview"
INTENT_SCORE = "score"


def route_intent(user_text: str, has_assistant_msg: bool) -> str:
    """根据最后一条用户消息判断走哪个 Agent"""
    if _SCORE_PAT.search(user_text or ""):
        return INTENT_SCORE
    return INTENT_INTERVIEW
