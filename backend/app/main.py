# -*- coding: utf-8 -*-
"""应用装配：FastAPI 实例、生命周期、路由注册、启动入口

启动：`python backend/run.py`（等价于 `python -m backend.app.main`）
端口：AGENT_PORT，默认 8790，由 Node 网关（backend/server.js :8787）反向代理
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from . import state, store
from .agents.review import run_review
from .api.routes import catalog, chat, convos, repos, reviews, skills, system
from .config import AGENT_NAME, PORT, USE_PLANNER
from .core.errors import fmt_exc
from .core.log import log
from .core.version import AS_VERSION
from .services.events import broadcast
from .services.poller import create_review_poller

# ------------------------------------------------------------------ 兼容出口
# 历史上这些能力都写在单文件 agent_server.py 里，测试与外部脚本按 `A.xxx` 取用。
# 拆分后保留同一组名字，调用方不用改；新代码请直接从下面的真实模块导入。
from .agents.interview import stream_interview, stream_with_retry  # noqa: E402,F401
from .agents.router import route_intent  # noqa: E402,F401
from .agents.runtime import StatusFlusher, drive_agent, make_agent  # noqa: E402,F401
from .llm.factory import get_model, normalize_base  # noqa: E402,F401
from .prompts.interview import (  # noqa: E402,F401
    evaluator_sys_prompt,
    interviewer_sys_prompt,
    planner_sys_prompt,
)
from .prompts.profile import PROFILE_DEFAULTS, profile_text  # noqa: E402,F401
from .schemas.score_card import ScoreCard, ScoreDimension  # noqa: E402,F401
from .tools.registry import bank_tool as _bank_tool  # noqa: E402,F401
from .utils.messages import last_user_text, to_scope_msgs  # noqa: E402,F401
from .utils.parsing import try_parse_score  # noqa: E402,F401
from .utils.render import render_score_card  # noqa: E402,F401
from .core import stages as _stages  # noqa: E402,F401


def __getattr__(name):
    """兼容出口：`A.STAGE_META` 以前是手写常量，现在从插件清单现算

    用模块级 __getattr__ 而不是 `STAGE_META = _stages.STAGE_META`：后者会把
    导入那一刻的结果固化下来，插件加角色后要重启才生效。
    """
    if name == "STAGE_META":
        return _stages.stages()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@asynccontextmanager
async def lifespan(_app):
    """启动：初始化持久层 → 起 PR 轮询；关闭：停轮询 → 关库"""
    try:
        store.init()
        s = store.stats()
        log(f"持久层就绪 · {s['convos']} 场会话 / {s['messages']} 条消息 · {s['path']}")
        # 模型配置落过库，启动即回填：否则轮询器会以空配置跑，把「未配置模型」
        # 写进每个仓库，左侧审核会话全挂「未配模型」角标（直到浏览器发过消息才修好）
        cfg = state.load_model_cfg()
        if cfg.get("base") and cfg.get("model"):
            log(f"模型配置已恢复 · {cfg.get('model')}")
            # 上次进程跑出来的「未配置模型」已经不成立了，别让它挂到下一轮轮询
            try:
                n = store.clear_repo_errors("未配置模型")
                if n:
                    log(f"已清理 {n} 个仓库的历史「未配置模型」错误")
            except Exception:
                pass
        else:
            log("模型配置为空：轮询只拉 PR，不自动审核（页面右上角齿轮里配置）")
        repo_list = store.list_repos()
        active = len([r for r in repo_list if r.get("enabled")])
        log(f"代码审核：{len(repo_list)} 个仓库（启用 {active} 个）· 轮询调度启动")
        state.get_poller().start()
    except Exception as e:
        log("持久层初始化失败（前端将回退本地存储）：" + str(e))
    yield
    try:
        await state.get_poller().stop()
    finally:
        store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="IT面试出题官 · AgentScope 服务", lifespan=lifespan)

    # 轮询器依赖审核流水线，放在装配期注入，避免 services → agents 的循环导入
    state.set_poller(create_review_poller(
        get_model_cfg=state.model_cfg,
        review_fn=run_review,
        on_event=broadcast,
        log=lambda m: log("[审核] " + m),
    ))

    for router in (system.router, catalog.router, convos.router, repos.router,
                   reviews.router, skills.router, chat.router):
        app.include_router(router)
    return app


app = create_app()


def main() -> None:
    print("")
    print("  🤖 AgentScope 智能体服务启动中…")
    print(f"  ➜  端口: {PORT}（由 server.js 转发，无需直接访问）")
    print(f"  ➜  策划官: {'开' if USE_PLANNER else '关'}（USE_PLANNER=0 可关闭）")
    print(f"  ➜  AgentScope: {AS_VERSION}")
    print("")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", timeout_keep_alive=300)


if __name__ == "__main__":
    main()
