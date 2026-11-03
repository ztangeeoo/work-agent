# -*- coding: utf-8 -*-
"""
Harness adapter 加载层（Hermit 兼容，可缺省）

作用：把 `backend/adapters/<name>/ADAPTER.md` 这段**可训练的外挂指令**接到各 Agent 的
system prompt 末尾。指令本身是纯文本文件，训练流程（hermit train）可以改写它，
而不需要动 prompts/ 里的任何代码。

设计约束：
- **零硬依赖**：没装 `hermit-harness`、没有 adapters 目录、或清单不合法时，
  自动退回「直接读文件」；再不行就返回空串，compose() 原样返回 base。
  也就是说：这一层失效时，行为和接入前逐字节一致。
- **零模型调用**：context() 只读本地文件，不产生 token 消耗、不增加首字延迟。
- **热更新**：按目录内最大 mtime 缓存，改完文件下一次请求即生效，不用重启。
- **可关**：HARNESS_ADAPTER=0 时完全旁路（等价于接入前的行为）。

环境变量：
  HARNESS_ADAPTER      1/0，是否启用（默认 1）
  HARNESS_ADAPTER_MAX  单份 adapter 文本的字符上限（默认 6000），防止误写超长文件
"""

import os
import threading
from pathlib import Path

from .. import config

# 目录在 import 时确定（便于测试热切换 HARNESS_ADAPTER_DIR：reload 即可生效）
ADAPTERS_DIR = Path(os.environ.get("HARNESS_ADAPTER_DIR") or config.ADAPTERS_DIR)

ENABLED = os.environ.get("HARNESS_ADAPTER", "1") not in ("0", "false", "False")
MAX_CHARS = int(os.environ.get("HARNESS_ADAPTER_MAX", "6000"))

_cache: dict[str, tuple[float, str]] = {}
_lock = threading.Lock()
_last_backend = "none"


def _signature(d: Path) -> float:
    """目录签名：所有文件里最大的修改时间，用来判断是否需要重新加载"""
    try:
        return max((p.stat().st_mtime for p in d.rglob("*") if p.is_file()), default=0.0)
    except OSError:
        return 0.0


def _read_via_hermit(d: Path) -> str:
    """优先走 hermit Session，拿到的就是 Hermit 训练流程演化出来的那份 context"""
    global _last_backend
    try:
        from hermit import Session  # 没装就走下面的文件兜底
    except Exception:
        return ""
    try:
        text = Session(d).context() or ""
        _last_backend = "hermit"
        return text
    except Exception as e:  # 清单不合法 / 接口变动 —— 不阻断主流程
        print(f"[harness] Session 加载失败，退回文件模式：{e}", flush=True)
        return ""


def _read_file(d: Path) -> str:
    global _last_backend
    md = d / "ADAPTER.md"
    if not md.is_file():
        return ""
    try:
        text = md.read_text(encoding="utf-8")
        if _last_backend == "none":
            _last_backend = "file"
        return text
    except OSError:
        return ""


def adapter_text(name: str) -> str:
    """返回某个 adapter 的指令文本；不可用时返回空串"""
    if not ENABLED or not name:
        return ""
    d = ADAPTERS_DIR / name
    if not d.is_dir():
        return ""

    sig = _signature(d)
    with _lock:
        hit = _cache.get(name)
        if hit and hit[0] == sig:
            return hit[1]

    text = (_read_via_hermit(d) or _read_file(d)).strip()[:MAX_CHARS]
    with _lock:
        _cache[name] = (sig, text)
    return text


def compose(base: str, name: str) -> str:
    """把 adapter 指令追加到 base 之后；没有 adapter 时原样返回 base（逐字节一致）"""
    extra = adapter_text(name)
    return f"{base}\n\n{extra}" if extra else base


def status() -> dict:
    """给 /api/db/stats 用的自检信息（先测一遍各 adapter，backend 才准确）"""
    names = sorted(p.name for p in ADAPTERS_DIR.iterdir() if p.is_dir()) if ADAPTERS_DIR.is_dir() else []
    sizes = {n: len(adapter_text(n)) for n in names}
    backend = "hermit" if _last_backend == "hermit" else ("file" if _last_backend == "file" else "none")
    return {
        "enabled": ENABLED,
        "backend": backend,
        "dir": str(ADAPTERS_DIR),
        "adapters": sizes,
    }
