# -*- coding: utf-8 -*-
"""专家插件注册表

扫描 `backend/plugins/<id>/plugin.yaml`，按 `mode` 选出一个能力包并调用它的入口。

设计取舍：
  - **目录即真相**。插件清单是源码，数据库（expert / agent 两张表）是它的投影。
    先把这条定死，避免「库一套、常量一套、目录一套」三方漂移。
  - **坏插件不拖垮服务**。解析失败的目录记进 `problems()` 并跳过，自检里能看到，
    但不会让整个进程起不来——和 skills.py 的处理方式一致。
  - **懒加载**。只有真正被 dispatch 到的插件才 import 它的模块；`status()` 之类
    的自检只读 yaml，不会触发副作用。
"""

import importlib
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import yaml

from .. import config
from .contract import ChatCtx, ExpertPlugin  # noqa: F401  (ExpertPlugin 供类型参考)

# 目录名必须是合法 Python 包名：插件是 `backend.plugins.<id>` 子包，含 `-` 会 import 不了
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_cache: dict = {"sig": None, "plugins": {}, "problems": []}
_lock = threading.Lock()


class Plugin:
    """一个能力包的运行时视图（元数据 + 两个入口）"""

    def __init__(self, *, pid: str, meta: dict, path: Path):
        self.id = pid
        self.path = path
        self.meta = dict(meta)
        self.mode = str(meta.get("mode") or pid)
        self.name = str(meta.get("name") or pid)
        self.icon = str(meta.get("icon") or "")
        # 对外专家 id：前端与库里的历史取值（interviewer / reviewer），
        # 与插件目录名（interview / review）不同，必须显式声明。
        self.expert_id = str(meta.get("expert_id") or pid)
        self.version = str(meta.get("version") or "0.0.0")
        self.entry: dict = meta.get("entry") or {}
        # 插件自己的业务配置（如面试的 use_planner），经 ctx.options 传进实现
        self.options: dict = meta.get("options") or {}
        # stages 列表 → 按 order 排好的列表；同时保留 id 索引
        raw = meta.get("stages") or []
        self.stages: list[dict] = sorted(
            [s for s in raw if isinstance(s, dict) and s.get("id")],
            key=lambda s: (s.get("order") or 100, str(s["id"])),
        )
        for s in self.stages:
            s.setdefault("label", str(s["id"]))
            s.setdefault("icon", "🤖")
        self.stage_index: dict[str, dict] = {str(s["id"]): s for s in self.stages}

    # ---------------------------------------------------------------- 入口
    def _entry(self, key: str):
        target = str(self.entry.get(key) or "").strip()
        if not target:
            raise LookupError(f"插件 {self.id} 没有声明 {key} 入口（plugin.yaml 的 entry.{key}）")
        module_name, _, attr = target.partition(":")
        if not module_name or not attr:
            raise ValueError(f"插件 {self.id} 的 entry.{key} 写法不对，应为 模块:函数，收到 {target!r}")
        module = importlib.import_module(f"backend.plugins.{self.id}.{module_name}")
        fn = getattr(module, attr, None)
        if not callable(fn):
            raise LookupError(f"插件 {self.id} 的 {target} 不存在或不可调用")
        return fn

    def chat(self, payload: dict, base_override: Optional[str] = None):
        """流式对话入口，返回 async generator（不要 await，直接 async for）"""
        return self._entry("chat")(
            build_ctx(payload, base_override, options=self.options)
        )

    async def job(self, payload: dict) -> dict:
        """一次性任务入口"""
        return await self._entry("job")(payload)

    def stage_meta(self, stage: str) -> Optional[dict]:
        return self.stage_index.get(str(stage))

    def tools_of_stage(self, stage: str) -> Optional[list[str]]:
        """该角色声明要装的工具名；**未声明返回 None**（调用方走旧启发式）

        `tools: []` 与「不写 tools」是两件事：前者是「明确不装」，后者是
        「插件还没接管这件事，别改变现有行为」。
        """
        item = self.stage_index.get(str(stage))
        if not item or "tools" not in item:
            return None
        return [str(t) for t in (item.get("tools") or []) if str(t).strip()]

    # ------------------------------------------------- 投影成 expert / agent 行
    def expert_record(self) -> dict:
        """这一行写进库里的 expert 表（结构字段见 _sync_catalog 的覆盖规则）

        role / desc / tags / cta 只在首次建档时作默认值，之后以库为准——
        库里能改文案是项目原本的工作流，不因为改成插件化就剥夺掉。
        """
        return {
            "id": self.expert_id,
            "mode": self.mode,
            "name": self.name,
            "icon": self.icon,
            "role": str(self.meta.get("role") or ""),
            "desc": str(self.meta.get("desc") or ""),
            "tags": [str(t) for t in (self.meta.get("tags") or [])],
            "cta": str(self.meta.get("cta") or ""),
            "sort": int(self.meta.get("sort") or 0),
        }

    def agent_records(self) -> list[dict]:
        """这一组写进库里的 agent 表：一个 stage 一行

        `icon` 用 avatar（单字，专家中心当头像用），不是 stages[].icon（emoji，
        流水线面板用）——两者在界面上是两个位置，别混。
        """
        out = []
        for i, s in enumerate(self.stages):
            out.append({
                "id": f"{self.expert_id}.{s['id']}",
                "expert_id": self.expert_id,
                "stage": s["id"],
                "name": s.get("label") or s["id"],
                "icon": s.get("avatar") or "",
                "role": s.get("role") or "",
                "primary": bool(s.get("primary")),
                "sort": int(s.get("order") or (i + 1) * 10),
            })
        return out

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Plugin {self.id} mode={self.mode} stages={list(self.stage_index)}>"


def build_ctx(payload: dict, base_override: Optional[str] = None,
              options: Optional[dict] = None) -> ChatCtx:
    """把 OpenAI 兼容请求体拆成插件要的上下文

    model 在这里统一构造：插件不再自己 `draft_model()`，模型配置的口径
    （缓存、/v1 补全、temperature 默认值）只有一处。
    """
    from ..core.log import log
    from ..llm.factory import draft_model
    from ..prompts.profile import PROFILE_DEFAULTS
    from ..utils.messages import has_assistant_reply, last_user_text, to_scope_msgs

    body = dict(payload)
    if base_override:
        body["base"] = base_override
    messages = payload.get("messages") or []
    return ChatCtx(
        payload=payload,
        model=draft_model(body),
        base=str(body.get("base") or ""),
        model_name=str(body.get("model") or ""),
        cid="chatcmpl-" + uuid.uuid4().hex[:24],
        created=int(time.time()),
        history=to_scope_msgs(messages),
        last_user=last_user_text(messages),
        has_assistant=has_assistant_reply(messages),
        profile={**PROFILE_DEFAULTS, **(payload.get("profile") or {})},
        options=dict(options or {}),
        _log=log,
    )


# ---------------------------------------------------------------- 发现
def _signature(root: Path) -> tuple:
    try:
        return tuple(sorted((str(p), p.stat().st_mtime) for p in root.glob("*/plugin.yaml")))
    except OSError:
        return ()


def _parse(path: Path) -> dict:
    meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(meta, dict):
        raise ValueError("plugin.yaml 顶层必须是键值对")
    return meta


def _scan(root: Path) -> tuple[dict[str, Plugin], list[dict]]:
    plugins: dict[str, Plugin] = {}
    problems: list[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        yml = d / "plugin.yaml"
        if not yml.is_file():
            continue
        if not _ID_RE.match(d.name):
            problems.append({"id": d.name, "problem": "目录名需为小写字母 / 数字 / 下划线（要能当 Python 包名）"})
            continue
        try:
            plugins[d.name] = Plugin(pid=d.name, meta=_parse(yml), path=d)
        except Exception as e:
            problems.append({"id": d.name, "problem": f"{type(e).__name__}: {e}"})

    # expert_id 是库表主键：两个插件声明同一个会把对方那行覆盖掉，必须提前拦住。
    # 先扫到的生效（按目录名排序，结果确定），后来者移出并记 problem。
    owner: dict[str, str] = {}
    for pid in list(plugins):
        eid = plugins[pid].expert_id
        if eid in owner:
            problems.append({
                "id": pid,
                "problem": f"expert_id={eid} 已被插件 {owner[eid]} 占用，本插件未生效",
            })
            del plugins[pid]
        else:
            owner[eid] = pid
    return plugins, problems


def _all(root: Optional[Path] = None) -> dict[str, Plugin]:
    root = Path(root or config.PLUGINS_DIR)
    if not config.PLUGINS_ENABLED or not root.is_dir():
        return {}
    sig = _signature(root)
    with _lock:
        if _cache["sig"] is not None and _cache["sig"] == sig:
            return dict(_cache["plugins"])

    plugins, problems = _scan(root)
    with _lock:
        _cache["sig"] = sig
        _cache["plugins"] = plugins
        _cache["problems"] = problems
    return dict(plugins)


def problems() -> list[dict]:
    """被跳过的插件目录；自检与排障用"""
    _all()
    return list(_cache["problems"])


# ---------------------------------------------------------------- 查询
def get(pid: str) -> Optional[Plugin]:
    return _all().get(str(pid or ""))


def for_mode(mode: str) -> Optional[Plugin]:
    """按 mode 取能力包；mode 为空返回 None（调用方回落到内置流水线）"""
    mode = str(mode or "").strip()
    if not mode:
        return None
    hits = [p for p in _all().values() if p.mode == mode]
    if not hits:
        return None
    if len(hits) > 1:
        # 不静默择一：mode 撞车时插件作者必须知道
        raise LookupError(
            "多个插件声明了同一个 mode="
            + mode
            + "：" + "、".join(p.id for p in hits)
        )
    return hits[0]


def default() -> "Plugin":
    """不传 agent_mode 时的默认能力包（config.DEFAULT_MODE）

    以前这个回落是 `stream_with_retry` 里直接 import 面试流水线，等于把
    「哪条业务线是默认」写死在基础设施里。现在它只是配置。
    """
    plugin = for_mode(config.DEFAULT_MODE)
    if plugin is None:
        raise LookupError(
            f"没有声明 mode={config.DEFAULT_MODE} 的默认插件"
            "（检查 backend/plugins/*/plugin.yaml，或设 DEFAULT_MODE）"
            + ("；" + "、".join(f"{b['id']}（{b['problem']}）" for b in problems())
               if problems() else "")
        )
    return plugin


def stage_meta(stage: str) -> Optional[dict]:
    """按 stage id 查插件清单里的角色元信息（跨插件查）"""
    for p in _all().values():
        meta = p.stage_meta(stage)
        if meta:
            return meta
    return None


def all_stages() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in _all().values():
        out.update(p.stage_index)
    return out


def tools_of_stage(stage: str) -> Optional[list[str]]:
    """按 stage 跨插件查声明的工具名；插件没声明就返回 None

    一个 stage 只属于一个插件（插件化后这是约束），这里不处理跨插件合并。
    """
    for p in _all().values():
        names = p.tools_of_stage(stage)
        if names is not None:
            return names
    return None


def all_plugins() -> list["Plugin"]:
    """全部插件（按 id 排序）；给专家清单这类「要看全体」的场合用"""
    return sorted(_all().values(), key=lambda p: p.id)


def status() -> dict:
    plugins = _all()
    return {
        "enabled": bool(config.PLUGINS_ENABLED),
        "dir": str(config.PLUGINS_DIR),
        "count": len(plugins),
        "items": [
            {
                "id": p.id,
                "mode": p.mode,
                "expert_id": p.expert_id,
                "name": p.name,
                "version": p.version,
                "stages": [
                    {"id": s["id"], "label": s.get("label"), "icon": s.get("icon")}
                    for s in p.stages
                ],
                "entries": sorted(k for k in ("chat", "job") if p.entry.get(k)),
            }
            for p in sorted(plugins.values(), key=lambda x: x.id)
        ],
        "problems": problems(),
    }


def reset_cache() -> None:
    """测试用：强制下次重新扫描（已 import 的模块不会重载）"""
    with _lock:
        _cache["sig"] = None
        _cache["plugins"] = {}
        _cache["problems"] = []
