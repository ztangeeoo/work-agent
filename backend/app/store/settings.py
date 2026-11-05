# -*- coding: utf-8 -*-
"""持久层 · 进程级设置（键值对）

只放「重启后仍然需要在」的少量配置，目前只有模型配置：
    setting(key, value_json) —— value 一律存 JSON 字符串，读出来即 Python 对象。

为什么模型配置必须落库
    它原来只存在 state.MODEL_CFG（进程内存），而前端仅在「发出一条消息」时
    才 POST /api/model-config 同步一次。服务一重启内存就空了，轮询器每轮都会
    以空配置调用 _require_cfg()，把「未配置模型」写进 repo.last_error，
    左侧每个审核会话都挂上「未配模型」角标。落库后启动即回填，
    轮询不再依赖「浏览器是否发过消息」。
"""

import json

from .db import init, lock, now

MODEL_CFG_KEY = "model_cfg"


def get_setting(key: str, default=None):
    """读一个设置项；不存在或解析失败都返回 default"""
    db = init()
    with lock:
        row = db.execute("SELECT value FROM setting WHERE key = ?", (str(key),)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except Exception:
        return default


def set_setting(key: str, value) -> None:
    db = init()
    payload = json.dumps(value, ensure_ascii=False)
    with lock:
        db.execute(
            """INSERT INTO setting (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value, updated_at = excluded.updated_at""",
            (str(key), payload, now()),
        )
        db.commit()


def delete_setting(key: str) -> bool:
    db = init()
    with lock:
        cur = db.execute("DELETE FROM setting WHERE key = ?", (str(key),))
        db.commit()
        return cur.rowcount > 0


def get_model_cfg() -> dict:
    """已保存的模型配置；没存过返回空 dict（调用方用 .get 兜底）"""
    val = get_setting(MODEL_CFG_KEY, None)
    if not isinstance(val, dict):
        return {}
    return {k: str(val.get(k) or "") for k in ("base", "key", "model")}


def save_model_cfg(cfg: dict) -> dict:
    """只把 base / key / model 落库

    profile、review、runId 这些是「这一轮对话的上下文」，每轮都变，
    存进库里既没用又会把库写脏，所以只留连接模型所必需的三项。
    """
    clean = {k: str((cfg or {}).get(k) or "").strip() for k in ("base", "key", "model")}
    set_setting(MODEL_CFG_KEY, clean)
    return clean
