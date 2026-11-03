# -*- coding: utf-8 -*-
"""运行时基线闸门：本项目按 AgentScope 2.0 的 API 编写

（Agent / reply_stream / Event / structured_schema）。1.x 的 ReActAgent +
agentscope_runtime 写法已不再适用，装错版本会报一堆看不懂的 ImportError，
这里提前拦截并给出可执行的修复命令。
"""

import agentscope

MIN_MAJOR = 2
MIN_VERSION = "2.0.11"


def ensure_compatible() -> str:
    version = str(getattr(agentscope, "__version__", "0.0.0"))
    major = version.split(".")[0]
    if not major.isdigit() or int(major) < MIN_MAJOR:
        raise RuntimeError(
            f"本项目需要 AgentScope {MIN_MAJOR}.0+，当前安装的是 {version}。\n"
            f"请执行：python -m pip install -U 'agentscope>={MIN_VERSION}'"
        )
    return version


AS_VERSION = ensure_compatible()
