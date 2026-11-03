# -*- coding: utf-8 -*-
"""系统接口：健康检查 / 模型配置 / 库状态

前端与 Node 网关都靠 /health 判断智能体服务是否就绪。
"""

import sys

from fastapi import APIRouter, Request

from ...config import AGENT_NAME, USE_PLANNER
from ... import store
from ...core.errors import fmt_exc
from ...core.version import AS_VERSION
from ...services import harness
from ...state import MODEL_CFG
from ..deps import err

router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    info = {
        "ok": True,
        "service": AGENT_NAME,
        "agentscope": AS_VERSION,
        "agentscopeMin": "2.0.0",
        "python": sys.version.split()[0],
        "agents": ["策划官", "面试官", "评分官", "侦察官", "审核官", "裁决官"],
        "modes": ["interview", "review"],
        "planner": USE_PLANNER,
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
    """把前端最新模型配置存进内存，供聊天适配与审核轮询共用。"""
    try:
        p = await request.json()
    except Exception as e:
        return err("请求体解析失败：" + str(e), 400)
    MODEL_CFG["base"] = str(p.get("base") or "").strip()
    MODEL_CFG["key"] = str(p.get("key") or "").strip()
    MODEL_CFG["model"] = str(p.get("model") or "").strip()
    MODEL_CFG["profile"] = p.get("profile") if isinstance(p.get("profile"), dict) else None
    MODEL_CFG["agentMode"] = "review" if p.get("agentMode") == "review" else "interview"
    MODEL_CFG["review"] = p.get("review") if isinstance(p.get("review"), dict) else None
    rid = p.get("runId")
    if isinstance(rid, str) and rid:
        MODEL_CFG["runId"] = rid
    return {"ok": True}


@router.get("/api/db/stats")
async def api_db_stats():
    if not store.available():
        return {"dbAvailable": False, "reason": store.unavailable_reason()}
    try:
        return {"dbAvailable": True, **store.stats(), "harness": harness.status()}
    except Exception as e:
        return err(fmt_exc(e), 500)
