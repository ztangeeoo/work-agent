# -*- coding: utf-8 -*-
"""兼容壳：意图路由已搬进 `backend/plugins/interview/intent.py`

「出题 vs 评分」是面试业务线的内部分支，不该留在通用层。这里只做转发，
新代码请直接从插件导入。
"""

from backend.plugins.interview.intent import (  # noqa: F401
    DEFAULT_KEYWORDS,
    INTENT_INTERVIEW,
    INTENT_SCORE,
    route_intent,
)
