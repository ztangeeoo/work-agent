# -*- coding: utf-8 -*-
"""系统接口：健康检查 / 模型配置 / 库状态

前端与 Node 网关都靠 /health 判断智能体服务是否就绪。
"""

import sys

from fastapi import APIRouter, Request

from ...config import AGENT_NAME, USE_PLANNER
from ... import state, store
from ...core.errors import fmt_exc
from ...core.log import log
from ...core.version import AS_VERSION
from ...services import harness
from ...notify import registry as notify_registry
from ...services import skills as skill_svc
from ...state import MODEL_CFG
from ..deps import err

router = APIRouter(tags=["system"])


DEFAULT_AGENT_NAMES = ["策划官", "面试官", "评分官", "侦察官", "审核官", "裁决官"]


def _agent_names() -> list:
    """智能体名单以库为准（expert → agent），库不可用时回退常量。"""
    try:
        if store.available():
            names = [a["name"] for a in store.list_agents()]
            if names:
                return names
    except Exception:
        pass
    return list(DEFAULT_AGENT_NAMES)


@router.get("/health")
async def health():
    info = {
        "ok": True,
        "service": AGENT_NAME,
        "agentscope": AS_VERSION,
        "agentscopeMin": "2.0.0",
        "python": sys.version.split()[0],
        "agents": _agent_names(),
        "modes": ["interview", "review"],
        "planner": USE_PLANNER,
        "skills": skill_svc.status(),
        # 通知渠道：扫到几个、哪些会真的发、配置有没有问题（webhook 已脱敏）
        "notifiers": notify_registry.status(),
    }
    try:
        if store.available():
            info["db"] = {"available": True, **store.stats()}
        else:
            info["db"] = {"available": False, "reason": store.unavailable_reason()}
    except Exception as e:
        info["db"] = {"available": False, "error": str(e)}
    return info


@router.post("/api/model-config")
async def api_model_config(request: Request):
    """保存全局模型配置（base / key / model）——全局唯一一份，与任何会话无关。

    同时落库（setting 表）：轮询器在后台跑，不能依赖「浏览器此刻开着且发过消息」。
    配好之后顺手清掉仓库上历史遗留的「未配置模型」错误，角标立刻消失，
    不用等下一轮轮询（间隔可能是几分钟）。

    会话上下文（profile / 当前 PR / repoId / runId）刻意不在这里处理：
    那是「这一轮对话」的输入，由 Node 网关的 /api/run-context 单独承载。
    """
    try:
        p = await request.json()
    except Exception as e:
        return err("请求体解析失败：" + str(e), 400)
    MODEL_CFG["base"] = str(p.get("base") or "").strip()
    MODEL_CFG["key"] = str(p.get("key") or "").strip()
    MODEL_CFG["model"] = str(p.get("model") or "").strip()
    # 打印 key 尾号：排查「我改了 key 怎么还是旧的」时，控制台一眼能对上弹框里显示的尾号
    _k = MODEL_CFG["key"]
    log("模型配置已收到 · %s · %s · key %s" % (
        MODEL_CFG["base"] or "-", MODEL_CFG["model"] or "-", ("**" + _k[-4:]) if _k else "(空)"))

    saved = False
    try:
        if state.model_ready():
            store.save_model_cfg(MODEL_CFG)
            store.clear_repo_errors("未配置模型")
            saved = True
        else:
            # 用户在弹框里把配置清空了：库里那份也删掉，否则下次重启会「复活」
            store.delete_setting(store.MODEL_CFG_KEY)
    except Exception as e:
        # 落库失败不该让接口 500：内存里已经有了，本轮聊天/审核照常
        log("模型配置落库失败：" + str(e))
    return {"ok": True, "saved": saved}


@router.get("/api/model-config")
async def api_model_config_get():
    """读取已保存的模型配置（换浏览器 / 清了本地存储时用来恢复表单）

    这是本机单进程单用户服务，key 与前端 localStorage 里那份同源，直接返回；
    只回 base / key / model，不回 profile / review 这类每轮上下文。
    """
    cfg = {}
    try:
        cfg = store.get_model_cfg()
    except Exception:
        cfg = {}
    if not cfg.get("base") and not cfg.get("model"):
        cfg = {k: MODEL_CFG.get(k) or "" for k in ("base", "key", "model")}
    return {"ok": True, "cfg": cfg}


@router.get("/api/db/stats")
async def api_db_stats():
    if not store.available():
        return {"dbAvailable": False, "reason": store.unavailable_reason()}
    try:
        return {"dbAvailable": True, **store.stats(), "harness": harness.status(),
                "skills": skill_svc.status()}
    except Exception as e:
        return err(fmt_exc(e), 500)
