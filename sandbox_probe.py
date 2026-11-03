# -*- coding: utf-8 -*-
"""AgentScope 2.0 Sandbox 自检（默认容器工作区，SANDBOX=local 走宿主机）

做什么：建一个 workspace（= 工具/代码执行环境），在里面真的跑几条命令、读写文件，
把「沙箱到底通不通」一次性验证出来。AgentScope 2.0 的 workspace 是 Agent 执行
工具（Shell / 读写文件 / MCP）的落点：

    DockerWorkspace     每个 workspace 一个容器，workdir 挂载到 /workspace（默认）
    LocalWorkspace      宿主机直接执行，不隔离 —— 开发机够用，别对公网开放

用法：
    .venv\\Scripts\\python.exe sandbox_probe.py                       （默认容器，需 Docker 引擎）
    set SANDBOX=local && .venv\\Scripts\\python.exe sandbox_probe.py  （不隔离，快）
"""

import asyncio
import os
import sys
import time

from agentscope.workspace import DockerWorkspace, LocalWorkspace

ROOT = os.path.dirname(os.path.abspath(__file__))
SANDBOX = (os.environ.get("SANDBOX") or "docker").strip().lower()
WORKDIR = os.path.abspath(
    os.environ.get("AGENT_WORKSPACE_DIR") or os.path.join(ROOT, "data", "probe-workspace"),
)


def build_workspace():
    if SANDBOX == "docker":
        # host_workdir 会绑定挂载到容器里的 /workspace
        return DockerWorkspace(host_workdir=WORKDIR)
    return LocalWorkspace(workdir=WORKDIR)


async def main() -> int:
    print(f"\n沙箱后端：{SANDBOX}    工作目录：{WORKDIR}\n")
    ws = build_workspace()
    t0 = time.time()
    try:
        await ws.initialize()
        print(f"✓ 初始化完成（{time.time() - t0:.1f}s）· workdir={ws.workdir}")

        tools = await ws.list_tools()
        print(f"✓ 可用工具 {len(tools)} 个：{', '.join(t.name for t in tools)[:200]}")

        backend = ws.get_backend()

        # 容器里没有宿主机的 python.exe，命令要用容器内的解释器
        py = "python3" if SANDBOX == "docker" else sys.executable

        # 1) 跑命令（不经 shell，直接给 argv；stdout/stderr 是 bytes）
        r = await backend.exec_shell([py, "-c", "print(2**10)"], cwd=ws.workdir)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        ok_cmd = r.exit_code == 0 and "1024" in out
        detail = out.strip()[:60] if ok_cmd else f"{out.strip()[:80]} {err.strip()[:120]}"
        print(f"{'✓' if ok_cmd else '✗'} 执行 Python：exit={r.exit_code} {detail}")

        # 2) 写文件 → 读文件（沙箱内的文件系统）
        target = os.path.join(ws.workdir, "probe.txt").replace("\\", "/")
        await backend.write_file(target, "沙箱写入测试 hello".encode("utf-8"))
        content = (await backend.read_file(target)).decode("utf-8", "replace")
        ok_file = "沙箱写入测试" in content
        print(f"{'✓' if ok_file else '✗'} 文件读写：{content[:40]!r}")

        # 3) 失败命令要能正确回报非零退出码（工具出错不能静默）
        bad = await backend.exec_shell([py, "-c", "import sys; sys.exit(3)"], cwd=ws.workdir)
        ok_exit = bad.exit_code == 3
        print(f"{'✓' if ok_exit else '✗'} 退出码透传：exit={bad.exit_code}（期望 3）")

        print()
        if SANDBOX == "local":
            print("提示：本地工作区不隔离，工具直接在宿主机执行；需要隔离请启 Docker 后设 SANDBOX=docker")
        return 0 if (ok_cmd and ok_file and ok_exit) else 1
    except Exception as e:
        print(f"✗ 沙箱不可用：{type(e).__name__}: {e}")
        if SANDBOX == "docker":
            print("  检查 Docker 引擎是否在运行：docker version")
        return 1
    finally:
        try:
            await ws.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
