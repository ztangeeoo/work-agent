# -*- coding: utf-8 -*-
"""模型工厂：任意 OpenAI 兼容接口 → AgentScope 模型对象

缓存按 (base, key, model, temperature) 命中，避免每个请求重建 HTTP 客户端
（重复建连既慢又容易把中转站打出 429）。
"""

import re

from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel

_MODEL_CACHE: dict[tuple, OpenAIChatModel] = {}


def normalize_base(base: str) -> str:
    """把用户填的 API 地址规整成 OpenAI SDK 可用的 base_url（需以 /v1 结尾）"""
    b = (base or "").strip().rstrip("/")
    if not b:
        raise ValueError("缺少 API 地址")
    if not re.match(r"^https?://", b, re.I):
        b = "https://" + b
    if not re.search(r"/v\d+$", b):
        b += "/v1"
    return b


def get_model(base: str, key: str, model_name: str, temperature: float) -> OpenAIChatModel:
    cache_key = (base, key or "", model_name, temperature)
    if cache_key not in _MODEL_CACHE:
        _MODEL_CACHE[cache_key] = OpenAIChatModel(
            credential=OpenAICredential(
                api_key=key or "sk-empty",
                base_url=base,
            ),
            model=model_name,
            parameters=OpenAIChatModel.Parameters(temperature=temperature),
            stream=True,
            max_retries=1,  # 401/404 快速失败，错误尽快透传给用户
            client_kwargs={"timeout": 180.0},
        )
    return _MODEL_CACHE[cache_key]


def has_v1_suffix(base: str) -> bool:
    """地址是否已经带 /v1 之类的版本段（404 重试判据）"""
    return bool(re.search(r"/v\d+$", (base or "").rstrip("/")))


def draft_model(payload: dict) -> OpenAIChatModel:
    """从请求体里取 base/key/model 建模型；审核与对话两条流水线共用"""
    base_raw = payload.get("base") or ""
    model_name = payload.get("model") or ""
    if not model_name:
        raise ValueError("缺少模型名称")
    if not base_raw:
        raise ValueError("缺少 API 地址")
    temperature = payload.get("temperature")
    temperature = float(temperature) if isinstance(temperature, (int, float)) else 0.7
    return get_model(normalize_base(base_raw), payload.get("key") or "", model_name, temperature)
