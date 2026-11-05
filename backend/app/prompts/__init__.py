# -*- coding: utf-8 -*-
"""提示词仓库（引擎侧只留通用的画像拼装）

  profile.py  候选人画像 → 文本，多条业务线共用

各业务线的 system prompt 已随业务搬进 `backend/plugins/<id>/prompts.py`；
本目录的 interview.py / review.py 只是兼容壳（main.py 的兼容出口按
`A.interviewer_sys_prompt` 取用）。新提示词请写进对应插件。
"""
