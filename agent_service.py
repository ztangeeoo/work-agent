# -*- coding: utf-8 -*-
"""AgentScope 2.0 官方 Agent Service（默认端口 8799）

用 `agentscope.app.create_app()` —— 2.0 内建的服务化入口，替掉已归档的
`agentscope-runtime`。它一次性提供：多租户 / 多会话、会话与状态持久化、
工作区（沙箱）生命周期、模型与凭据管理、RAG、MCP/Skill Hub、定时任务、
IM 渠道，以及一套 REST + SSE 接口和 /docs。

这里把它跑成「零外部依赖」形态，方便本地一键起来：

    持久化   SQLite（AsyncSQLAlchemyStorage + aiosqlite）—— 不需要 Redis
    消息总线 InMemoryMessageBus —— 单进程够用；多副本时换 RedisMessageBus
    工作区   LocalWorkspaceManager（默认）/ DockerWorkspaceManager（SANDBOX=docker）

环境变量：
    AGENT_SERVICE_PORT   端口，默认 8799（出题官 8790、Node 8787，错开不冲突）
    AGENT_SERVICE_DB     SQLite 文件，默认 data/agent_service.db
    AGENT_WORKSPACE_DIR  工作区根目录，默认 data/workspaces
    SANDBOX              local（默认）| docker（需要 Docker 引擎在运行）
    AGENT_SERVICE_TITLE  服务标题，默认「IT 面试出题官 · Agent 服务」

启动：
    .venv\\Scripts\\python.exe agent_service.py
    浏览器打开 http://localhost:8799/docs
"""

import os

import uvicorn

from agentscope.app import create_app
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import AsyncSQLAlchemyStorage
from agentscope.app.workspace_manager import (
    DockerWorkspaceManager,
    LocalWorkspaceManager,
    WorkspaceManagerBase,
)

ROOT = os.path.dirname(os.path.abspath(__file__))

PORT = int(os.environ.get("AGENT_SERVICE_PORT", "8799"))
SANDBOX = (os.environ.get("SANDBOX") or "local").strip().lower()
DB_PATH = os.path.abspath(
    os.environ.get("AGENT_SERVICE_DB") or os.path.join(ROOT, "data", "agent_service.db"),
)
WORKSPACE_DIR = os.path.abspath(
    os.environ.get("AGENT_WORKSPACE_DIR") or os.path.join(ROOT, "data", "workspaces"),
)
TITLE = os.environ.get("AGENT_SERVICE_TITLE") or "IT 面试出题官 · Agent 服务"


def sqlite_url(path: str) -> str:
    """Windows 绝对路径 → SQLAlchemy 异步 SQLite URL（正斜杠三斜线形式）"""
    return "sqlite+aiosqlite:///" + path.replace("\\", "/").lstrip("/")


def build_workspace_manager() -> WorkspaceManagerBase:
    """按 SANDBOX 选择工作区（= 工具/代码执行环境）后端"""
    if SANDBOX == "docker":
        # 每个 workspace 一个容器，workdir 绑定挂载到 /workspace，工具在容器内执行
        return DockerWorkspaceManager(basedir=WORKSPACE_DIR)
    # 本地目录工作区：不隔离，直接在宿主机跑工具（开发/单机够用，别对公网开放）
    return LocalWorkspaceManager(basedir=WORKSPACE_DIR)


app = create_app(
    storage=AsyncSQLAlchemyStorage(sqlite_url(DB_PATH), create_tables=True),
    message_bus=InMemoryMessageBus(),
    workspace_manager=build_workspace_manager(),
    title=TITLE,
)


if __name__ == "__main__":
    print("")
    print("  🧩 AgentScope Agent Service 启动中…")
    print(f"  ➜  端口: {PORT}（接口文档 /docs）")
    print(f"  ➜  存储: SQLite {DB_PATH}")
    print(f"  ➜  工作区: {WORKSPACE_DIR}（SANDBOX={SANDBOX}）")
    if SANDBOX != "docker":
        print("  ➜  提示: 当前是本地工作区，工具直接在宿主机执行；需要隔离请设 SANDBOX=docker 并启动 Docker")
    print("")
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="info", timeout_keep_alive=300)
