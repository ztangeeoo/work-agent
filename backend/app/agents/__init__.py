# -*- coding: utf-8 -*-
"""智能体层（引擎侧，不含任何具体业务线）

  contract.py  插件契约：ChatCtx / ExpertPlugin
  registry.py  插件注册表：扫 backend/plugins/*/plugin.yaml，按 mode 选能力包
  runtime.py   构造 / 流式驱动 / 状态前缀 / 404 重试

业务线全在 `backend/plugins/`：

  interview/  面试出题官（策划官 → 面试官 / 评分官）
  review/     代码审核官（侦察官 → 审核官 → 裁决官 / 审核答疑）

本目录的 interview.py / review.py / router.py 只剩兼容壳，新代码请走
`registry.for_mode(...)` 或 `registry.default()`。新增业务线加插件目录，
不要在这里加分支。
"""
