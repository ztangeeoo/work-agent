# -*- coding: utf-8 -*-
"""代码审核官 · 提示词（兼容壳）

提示词已搬进插件：`backend/plugins/review/prompts.py`。
保留本文件只为不打断历史 import，新代码请直接引用插件目录。
"""

from backend.plugins.review.prompts import (  # noqa: F401
    judge_sys_prompt,
    reviewer_chat_sys_prompt,
    reviewer_sys_prompt,
    scout_sys_prompt,
)
