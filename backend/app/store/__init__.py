# -*- coding: utf-8 -*-
"""持久层门面

调用方统一 `from .. import store`，然后按 `store.list_convos(...)` 使用：
  db.py       连接 / 建表 / 容量统计
  catalog.py  专家（expert）与智能体（agent）：会话 → 智能体 → 专家
  convos.py   会话与消息（含 FTS5 检索）
  reviews.py  仓库 / PR / 审核报告
"""

from .catalog import (
    catalog_version,
    get_agent,
    get_expert,
    list_agents,
    list_experts,
    primary_agent_id,
    primary_agent_id_by_mode,
)
from .convos import (
    delete_convo,
    get_convo,
    list_convos,
    resolve_binding,
    save_convo,
    search_messages,
)
from .db import (
    DB_PATH,
    DEFAULT_USER,
    MAX_CONVOS,
    MAX_MSGS,
    available,
    close,
    connected,
    init,
    now,
    stats,
    unavailable_reason,
)
from .reviews import (
    delete_repo,
    find_review,
    get_pr,
    get_repo,
    get_repo_by_id,
    get_review,
    list_all_repos,
    list_prs,
    list_repos,
    list_reviews,
    mark_review_posted,
    save_repo,
    save_review,
    set_repo_provider,
    touch_repo,
    upsert_pr,
)

__all__ = [
    "DB_PATH", "DEFAULT_USER", "MAX_CONVOS", "MAX_MSGS",
    "available", "close", "connected", "init", "now", "stats", "unavailable_reason",
    "catalog_version",
    "get_agent", "get_expert", "list_agents", "list_experts",
    "primary_agent_id", "primary_agent_id_by_mode",
    "delete_convo", "get_convo", "list_convos", "resolve_binding", "save_convo", "search_messages",
    "delete_repo", "find_review", "get_pr", "get_repo", "get_repo_by_id", "get_review",
    "list_all_repos", "list_prs", "list_repos", "list_reviews", "mark_review_posted",
    "save_repo", "save_review", "set_repo_provider", "touch_repo", "upsert_pr",
]
