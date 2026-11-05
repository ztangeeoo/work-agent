# -*- coding: utf-8 -*-
"""进程内共享状态

**模型配置是全局的**：只有 base / key / model 三项，与任何会话无关，供：
  - PR 轮询器（定时审核用的 base/key/model）
  - Node 网关启动时读回，转给 CopilotKit 适配器

它同时落进 setting 表，启动时 load_model_cfg() 回填，所以重启后
轮询不会退化成「只轮询不审核」，也不用等浏览器再发一条消息。

会话相关的上下文（画像、当前 PR、repoId、runId）不在这里：那属于
「这一轮对话」，由 Node 网关按轮次单独保管，别和全局配置混在一个口袋。
"""

MODEL_CFG: dict = {"base": "", "key": "", "model": ""}

_poller = None


def model_cfg() -> dict:
    return MODEL_CFG


def load_model_cfg() -> dict:
    """启动时把上次保存的模型配置回填进内存（库里没有就保持空）。

    延迟导入 store：store 只依赖 config，但 state << store << api 这条链上
    顶层互相导入容易绕成环，放在函数里最省事。
    """
    try:
        from . import store
        saved = store.get_model_cfg()
    except Exception:
        return dict(MODEL_CFG)
    if saved.get("base") and saved.get("model"):
        MODEL_CFG["base"] = saved.get("base") or ""
        MODEL_CFG["key"] = saved.get("key") or ""
        MODEL_CFG["model"] = saved.get("model") or ""
    return dict(MODEL_CFG)


def model_ready() -> bool:
    """内存里是否已有可用的模型配置（轮询器审核前的统一判断）"""
    return bool(MODEL_CFG.get("base") and MODEL_CFG.get("model"))


def set_poller(poller) -> None:
    """由 main 在启动时注入（轮询器依赖审核流水线，避免模块级循环导入）"""
    global _poller
    _poller = poller


def get_poller():
    if _poller is None:
        raise RuntimeError("轮询调度尚未启动")
    return _poller


def has_poller() -> bool:
    return _poller is not None
