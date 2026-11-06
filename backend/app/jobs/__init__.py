# -*- coding: utf-8 -*-
"""后台执行层

当前只有调度（schedule.py）：决定「什么时候该跑」。
执行（runner / engine）与副作用队列（effect）在 Run Layer 落地后加进来，
届时 tick_log.run_id 会指向 run，本包负责把「触发」变成「可追踪的执行」。

依赖方向：jobs → store，不反向。jobs 里不放 SQL，store 里不放算法。
"""

from .schedule import (  # noqa: F401
    REPO_KIND,
    advance,
    collect_due,
    due_plan,
    ensure_repo_schedules,
    interval_ms,
    jitter_offset,
    schedule_of_repo,
    trigger_now,
)

__all__ = [
    "REPO_KIND",
    "advance",
    "collect_due",
    "due_plan",
    "ensure_repo_schedules",
    "interval_ms",
    "jitter_offset",
    "schedule_of_repo",
    "trigger_now",
]
