# -*- coding: utf-8 -*-
"""通知渠道注册表

包一层「把审核结果发到某个地方」的渠道，与 `app/agents/registry.py` 是同构设计，
但**故意不复用它**：

  插件（plugins/）是「一条业务线」——会被投影进 expert / agent 两张表、出现在
  专家中心、有自己的流水线角色。通知渠道只做「把事件发出去」，不进库、不占专家位。
  塞进 plugins/ 的直接后果是专家中心多出一张「企业微信推送」卡片，而且
  plugin.yaml 的 stages / entry.chat 对它毫无意义。

设计取舍（与插件注册表一致）：
  - **目录即真相**：`notifiers/<id>/notifier.yaml` + `notifier.py`
  - **坏渠道不拖垮服务**：解析失败的目录记进 problems() 并跳过
  - **懒加载**：只有真正要发消息时才 import 实现模块
  - **一个渠道失败不影响其他渠道**：逐个 try，结果逐个返回
"""

import importlib
import re
import threading
from pathlib import Path
from typing import Optional

import yaml

from .. import config

# 目录名要能当 Python 包名（backend.notifiers.<id>）
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

_cache: dict = {"sig": None, "channels": {}, "problems": []}
_lock = threading.Lock()


class Channel:
    """一个通知渠道的运行时视图"""

    def __init__(self, *, cid: str, meta: dict, path: Path):
        self.id = cid
        self.path = path
        self.meta = dict(meta)
        self.name = str(meta.get("name") or cid)
        self.icon = str(meta.get("icon") or "🔔")
        # 渠道自己的配置（webhook 地址、@人列表等），由 notifier.py 自己解释
        self.options: dict = meta.get("options") or {}
        # 默认是否启用。false 的渠道仍会被扫到、能在自检里看到，
        # 只是 select() 不会取它——比「删目录」好，改一个开关就能恢复。
        self.enabled = meta.get("enabled", True) is not False
        self.entry: dict = meta.get("entry") or {}

    def _entry(self, key: str):
        target = str(self.entry.get(key) or "").strip()
        if not target:
            raise LookupError(
                f"通知渠道 {self.id} 没有声明 entry.{key}（notifier.yaml）"
            )
        module_name, _, attr = target.partition(":")
        if not module_name or not attr:
            raise ValueError(
                f"通知渠道 {self.id} 的 entry.{key} 写法不对，应为 模块:函数，收到 {target!r}"
            )
        module = importlib.import_module(f"backend.notifiers.{self.id}.{module_name}")
        fn = getattr(module, attr, None)
        if not callable(fn):
            raise LookupError(f"通知渠道 {self.id} 的 {target} 不存在或不可调用")
        return fn

    async def notify(self, event: dict) -> dict:
        """发一条通知，返回 {ok, skipped?, error?, ms?}

        约定：实现方**不要抛异常**——超时、key 失效、网络不通都算「这条没发成」，
        返回描述即可。真抛了外层也会接住，但带上 skipped/error 才能看出是哪种。
        """
        return await self._entry("send")(event, dict(self.options))

    def available(self) -> bool:
        """这个渠道现在能不能用（webhook 有没有配等），实现方说了算"""
        fn = self.entry.get("available")
        if not fn:
            return self.enabled
        try:
            module_name, _, attr = str(fn).partition(":")
            module = importlib.import_module(f"backend.notifiers.{self.id}.{module_name}")
            checker = getattr(module, attr, None)
            return bool(checker(dict(self.options))) if callable(checker) else self.enabled
        except Exception:
            # 判断不了就当可用：让 send() 去报真正的错，
            # 总比「配置写歪了就永远静默不发」好排查
            return self.enabled

    def describe(self) -> dict:
        """自检 / 调试用的一份描述（不含敏感值）"""
        return {
            "id": self.id,
            "name": self.name,
            "icon": self.icon,
            "enabled": self.enabled,
            "available": self.available(),
            "options": _mask(self.options),
        }

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Channel {self.id} enabled={self.enabled}>"


def _mask(options: dict) -> dict:
    """把 webhook / token / secret 这类值遮掉中间，只留尾部

    自检接口是要给人看的，也可能是截图发群里的，不该把完整 key 带出去。
    """
    out = {}
    for k, v in (options or {}).items():
        key = str(k).lower()
        if any(s in key for s in ("webhook", "token", "secret", "key", "password")):
            s = str(v or "")
            out[k] = ("…" + s[-6:]) if len(s) > 6 else ("(已配)" if s else "")
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- 发现
def _signature(root: Path) -> tuple:
    try:
        return tuple(sorted((str(p), p.stat().st_mtime) for p in root.glob("*/notifier.yaml")))
    except OSError:
        return ()


def _parse(path: Path) -> dict:
    meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(meta, dict):
        raise ValueError("notifier.yaml 顶层必须是键值对")
    return meta


def _scan(root: Path) -> tuple[dict[str, Channel], list[dict]]:
    channels: dict[str, Channel] = {}
    problems: list[dict] = []
    if not root.is_dir():
        return channels, problems
    for d in sorted(root.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        yml = d / "notifier.yaml"
        if not yml.is_file():
            continue
        if not _ID_RE.match(d.name):
            problems.append({
                "id": d.name,
                "problem": "目录名需为小写字母 / 数字 / 下划线（要能当 Python 包名）",
            })
            continue
        try:
            ch = Channel(cid=d.name, meta=_parse(yml), path=d)
            if not ch.entry.get("send"):
                raise ValueError("缺少 entry.send（必须声明发送函数）")
            channels[d.name] = ch
        except Exception as e:
            problems.append({"id": d.name, "problem": f"{type(e).__name__}: {e}"})
    return channels, problems


def _all(root: Optional[Path] = None) -> dict[str, Channel]:
    root = Path(root or config.NOTIFIERS_DIR)
    if not config.NOTIFIERS_ENABLED or not root.is_dir():
        return {}
    sig = _signature(root)
    with _lock:
        if _cache["sig"] is not None and _cache["sig"] == sig:
            return dict(_cache["channels"])

    channels, problems = _scan(root)
    with _lock:
        _cache["sig"] = sig
        _cache["channels"] = channels
        _cache["problems"] = problems
    return dict(channels)


def reset_cache() -> None:
    """测试 / 热更新用：强制下次重新扫目录"""
    with _lock:
        _cache["sig"] = None
        _cache["channels"] = {}
        _cache["problems"] = []


def channels_config_enabled() -> bool:
    """通知渠道这个能力本身是否开着（NOTIFIERS_ENABLED）

    与「有没有配渠道」「渠道配得对不对」是三个不同的问题，
    报错文案要能分清——不然用户看到「没发出去」不知道该改哪儿。
    """
    return bool(config.NOTIFIERS_ENABLED)


def problems() -> list[dict]:
    """被跳过的渠道目录；自检与排障用"""
    _all()
    return list(_cache["problems"])


# ---------------------------------------------------------------- 查询
def get(cid: str) -> Optional[Channel]:
    return _all().get(str(cid or ""))


def all_channels() -> list[Channel]:
    """全部渠道（含 enabled: false 的），顺序按目录名"""
    return list(_all().values())


def select() -> list[Channel]:
    """这一轮真正要发的渠道：启用 + 当前可用"""
    return [c for c in all_channels() if c.enabled and c.available()]


def status() -> dict:
    """自检用：扫到了什么、跳过了什么、哪些会真的发"""
    channels = all_channels()
    return {
        "enabled": bool(config.NOTIFIERS_ENABLED),
        "dir": str(config.NOTIFIERS_DIR),
        "count": len(channels),
        "active": len(select()),
        "channels": [c.describe() for c in channels],
        "problems": problems(),
    }
