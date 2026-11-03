# -*- coding: utf-8 -*-
"""应用主包

分层（自下向上，禁止反向依赖）：
  config / core / schemas   配置、日志、数据契约
  llm / prompts / tools     模型、提示词、工具
  store / services          持久化、外部适配（代码托管、轮询、事件）
  agents                    智能体编排（面试流水线 / 审核流水线）
  api                       路由层（只做参数校验与响应）
  main                      装配与启动
"""
