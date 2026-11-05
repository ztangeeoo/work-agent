# -*- coding: utf-8 -*-
"""流水线角色与专家清单（**插件清单的投影**）

唯一真相在 `backend/plugins/<id>/plugin.yaml`：

    stages:     角色 id / 标签 / 图标 / 顺序（前端流水线面板按它渲染）
    expert_id:  对外专家 id（interviewer / reviewer，与目录名不同）
    name/icon:  专家展示名与头像字

本模块只做视图，给非插件代码（前端接口、技能归属）用。刻意不再手抄常量：
以前加一个角色要在插件清单和这里各写一遍，两边一定会漂；而「加一条业务线」
的成本正是插件化要消掉的东西。

老代码里 `from ..core.stages import STAGE_META` 仍可用，见文件末尾的
模块级 `__getattr__`（每次访问重新生成，改完 yaml 不用重启）。
"""

from typing import Optional


def _plugin_stage(stage: str) -> Optional[dict]:
    try:
        from ..agents import registry
        return registry.stage_meta(stage)
    except Exception:
        return None


def stage_meta(stage: str) -> dict:
    """取阶段元信息；插件里没有的角色给个兜底，避免前端空白"""
    meta = _plugin_stage(stage)
    if meta:
        return {"label": meta.get("label") or stage, "icon": meta.get("icon") or "🤖"}
    return {"label": stage, "icon": "🤖"}


def stages() -> dict[str, dict]:
    """全部角色：角色 id → {label, icon, ...}"""
    try:
        from ..agents import registry
        return registry.all_stages()
    except Exception:
        return {}


def experts() -> list[dict]:
    """专家清单：[{id, mode, label, icon, stages}]，顺序按插件 id

    插件目录坏了只会少一行，不会抛——技能归属与前端清单都不该因为
    一个 yaml 写错而整页失败。
    """
    try:
        from ..agents import registry
        return [
            {
                "id": p.expert_id,
                "mode": p.mode,
                "label": p.name,
                "icon": p.icon,
                "stages": [s["id"] for s in p.stages],
            }
            for p in registry.all_plugins()
        ]
    except Exception:
        return []


def stages_of_expert(expert_id: str) -> tuple[str, ...]:
    """某个专家下会跑到的角色（按插件里声明的顺序）"""
    for e in experts():
        if e["id"] == expert_id:
            return tuple(e["stages"])
    return ()


def experts_of_stage(stage: str) -> list[str]:
    """某个角色属于哪些专家（一个角色只属于一个专家；用于展示）"""
    return [e["id"] for e in experts() if stage in e["stages"]]


def __getattr__(name: str):
    """兼容出口：让老的常量式取法仍然工作（PEP 562 模块级属性）

    `STAGE_META` / `EXPERT_META` / `EXPERT_STAGES` 曾经是手写常量，现在改为
    每次访问时从插件清单现算。新代码请直接用上面的函数。
    """
    if name == "STAGE_META":
        return stages()
    if name == "EXPERT_META":
        return {
            e["id"]: {"label": e["label"], "icon": e["icon"], "mode": e["mode"]}
            for e in experts()
        }
    if name == "EXPERT_STAGES":
        return {e["id"]: tuple(e["stages"]) for e in experts()}
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
