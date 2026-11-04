# -*- coding: utf-8 -*-
"""代码审核官 · 只读仓库工具

把 services/review_client 里已经封装好的平台接口（GitLab / Gitee / GitHub）
暴露成 AgentScope 可调用的工具，让审核官能自己拉 MR 清单、diff、变更文件与
仓库原文，而不是只能等前端把一份报告塞进提示词。

设计约束：
  - 全部只读：不写仓库、不回帖、不改分支（is_read_only=True）；
  - 仓库由服务端绑定（闭包），模型无权切换仓库，防止越权读别的项目；
  - Token 只在服务端使用，绝不写进工具返回值；
  - 输出一律截断，避免一次 diff 把上下文撑爆。
"""

import time

from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import FunctionTool

from ..services import review_client as rv

MAX_FILE_CHARS = 20000


def _allow(message: str) -> PermissionDecision:
    """显式放行：无头服务没人点确认，读操作直接执行"""
    return PermissionDecision(behavior=PermissionBehavior.ALLOW, message=message)


def _fmt_time(ms) -> str:
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ms) / 1000))
    except Exception:
        return "-"


def repo_tools(repo: dict) -> list:
    """为某个仓库构造只读工具集（repo 由服务端解析后绑定，模型看不到 token）"""
    owner_name = (str(repo.get("owner") or "") + "/" + str(repo.get("name") or "")).strip("/")
    tag = "「" + (owner_name or "当前仓库") + "」"

    async def list_mrs(state: str = "open", limit: int = 20) -> str:
        """列出当前仓库的 Merge Request / Pull Request 清单（只读）。

        Args:
            state: 状态过滤，取值 open / closed / merged / all，默认 open
            limit: 最多返回多少条，默认 20，上限 50

        Returns:
            编号、标题、作者、状态、更新时间、源分支与目标分支；没有数据或失败时返回说明文字
        """
        try:
            n = max(1, min(int(limit or 20), 50))
        except Exception:
            n = 20
        try:
            prs = await rv.list_prs(repo, state=str(state or "open"), limit=n)
        except Exception as e:
            return "工具调用失败：" + str(e)
        if not prs:
            return ("state=" + str(state or "open") + " 下没有任何 PR/MR。"
                    "如果用户提了具体编号，可以直接用 get_mr_diff 查那个编号；"
                    "编号也对不上就如实告诉用户查不到，不要猜。")
        lines = ["共 " + str(len(prs)) + " 条（state=" + str(state or "open") + "）："]
        for p in prs:
            lines.append(
                "- #" + str(p["number"]) + " " + (p.get("title") or "")
                + " ｜ 作者 " + (p.get("author") or "-")
                + " ｜ " + (p.get("state") or "-")
                + " ｜ " + _fmt_time(p.get("updatedAt"))
                + " ｜ " + (p.get("headBranch") or "-") + " → " + (p.get("baseBranch") or "-")
            )
        return "\n".join(lines)

    async def get_mr_diff(number: int) -> str:
        """获取某个 PR/MR 的完整 diff（unified 格式，只读）。

        Args:
            number: PR/MR 编号（界面与 URL 上显示的那个编号）

        Returns:
            diff 文本（过长会截断并注明）；失败或没有文本改动时返回说明
        """
        try:
            diff = await rv.fetch_pr_diff(repo, number)
        except Exception as e:
            return "工具调用失败：" + str(e)
        if not str(diff or "").strip():
            return "这个 PR 没有文本改动（可能全是二进制文件或空提交）。"
        stats = rv.diff_stats(diff)
        body = rv.truncate_diff(diff, stats["files"])
        return ("#" + str(number) + " 的 diff：文件 " + str(stats["files"])
                + " 个，+" + str(stats["additions"]) + " / -" + str(stats["deletions"])
                + "\n\n```diff\n" + body + "\n```")

    async def list_mr_files(number: int) -> str:
        """列出某个 PR/MR 改了哪些文件（路径 + 变更类型，只读）。

        Args:
            number: PR/MR 编号

        Returns:
            文件清单；需要具体改动内容时再用 get_mr_diff
        """
        try:
            files = await rv.fetch_pr_files(repo, number)
        except Exception as e:
            return "工具调用失败：" + str(e)
        if not files:
            return "这个 PR 没有文件变更信息（可能接口不支持或改动为空）。"
        lines = ["#" + str(number) + " 共改动 " + str(len(files)) + " 个文件："]
        for f in files:
            extra = ""
            if f.get("oldPath") and f.get("oldPath") != f.get("path"):
                extra = "（原路径 " + str(f["oldPath"]) + "）"
            lines.append("- [" + str(f.get("status") or "modified") + "] "
                         + str(f.get("path") or "") + extra)
        return "\n".join(lines)

    async def read_repo_file(path: str, ref: str = "") -> str:
        """读取当前仓库里某个文件的原文（只读，默认分支）。

        Args:
            path: 仓库内相对路径，例如 src/main/java/com/demo/PayService.java
            ref: 分支 / tag / commit，留空则用默认分支

        Returns:
            文件内容（超过 20000 字会截断并注明）；失败时返回说明
        """
        try:
            text = await rv.fetch_file_text(repo, path, ref)
        except Exception as e:
            return "工具调用失败：" + str(e)
        if not text:
            return "（文件存在但内容为空）"
        lines = text.count("\n") + 1
        if len(text) > MAX_FILE_CHARS:
            return (text[:MAX_FILE_CHARS]
                    + "\n\n...（文件过长已截断：共 " + str(len(text)) + " 字 / "
                    + str(lines) + " 行；需要后半段请指出具体函数或行号范围）")
        return "（" + str(lines) + " 行）\n" + text

    return [
        FunctionTool(list_mrs, description="列出" + tag + "的 PR/MR 清单，可用 state 过滤 open/closed/merged/all",
                     permission=_allow("只读：列出 MR 清单"), is_read_only=True),
        FunctionTool(get_mr_diff, description="获取" + tag + "某个 PR/MR 的完整 diff",
                     permission=_allow("只读：拉取 diff"), is_read_only=True),
        FunctionTool(list_mr_files, description="列出" + tag + "某个 PR/MR 的变更文件清单",
                     permission=_allow("只读：拉取变更文件清单"), is_read_only=True),
        FunctionTool(read_repo_file, description="读取" + tag + "内某个文件的原文",
                     permission=_allow("只读：读取仓库文件"), is_read_only=True),
    ]
