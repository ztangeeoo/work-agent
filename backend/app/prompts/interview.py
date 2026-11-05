# -*- coding: utf-8 -*-
"""兼容壳：面试提示词已搬进 `backend/plugins/interview/prompts.py`

保留这三个名字是因为 main.py 的兼容出口按 `A.interviewer_sys_prompt` 取用。
新代码请直接从插件导入。
"""

from backend.plugins.interview.prompts import (  # noqa: F401
    evaluator_sys_prompt,
    interviewer_sys_prompt,
    planner_sys_prompt,
)
