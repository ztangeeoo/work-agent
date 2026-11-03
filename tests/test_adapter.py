# -*- coding: utf-8 -*-
"""
harness adapter 加载层回归测试（纯标准库，不需要 hermit-harness）

覆盖：base 原样保留 / adapter 追加位置 / 缺失目录降级 / 全局开关 /
超长截断 / 热更新（mtime 变化重新加载）/ 清单合法性 / status() /
真实 adapter 注入后关键格式契约仍在。

运行：.venv\\Scripts\\python.exe tests/test_adapter.py
"""

import importlib
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTERS = os.path.join(ROOT, "adapters")

pass_n = 0
fail_n = 0


def ok(cond, label):
    global pass_n, fail_n
    if cond:
        pass_n += 1
        print(f"  PASS  {label}")
    else:
        fail_n += 1
        print(f"  FAIL  {label}")


def fresh(**env):
    """按给定环境变量重新加载模块（模块在 import 时读配置）"""
    for k in ("HARNESS_ADAPTER", "HARNESS_ADAPTER_MAX", "HARNESS_ADAPTER_DIR"):
        os.environ.pop(k, None)
    os.environ.update({k: str(v) for k, v in env.items()})
    import harness_adapter
    return importlib.reload(harness_adapter)


BASE = "你是面试官。\n\n### 第 N 题【题型】\n【考察点】一句话。"

print("\n[1] 缺省降级：没有 adapter 时 base 逐字节不变")
ha = fresh()
ok(ha.compose(BASE, "not-exist") == BASE, "不存在的 adapter → 原样返回")
ok(ha.compose(BASE, "") == BASE, "空名字 → 原样返回")
ok(ha.adapter_text("not-exist") == "", "adapter_text 返回空串")

print("\n[2] 真实 adapter：追加在 base 之后，base 是前缀")
ha = fresh()
i_text = ha.adapter_text("interview")
r_text = ha.adapter_text("review")
ok(len(i_text) > 0, "interview/ADAPTER.md 已加载")
ok(len(r_text) > 0, "review/ADAPTER.md 已加载")
merged = ha.compose(BASE, "interview")
ok(merged.startswith(BASE), "base 仍是前缀（没被改写）")
ok(merged == BASE + "\n\n" + i_text, "拼接方式为 base + 空行 + adapter")
ok("### 第 N 题【题型】" in merged, "题目卡片格式契约仍在")
ok("【考察点】" in merged, "考察点标记仍在")
ok(i_text.strip() != r_text.strip(), "两份 adapter 内容不同（没有串用）")

print("\n[3] 开关：HARNESS_ADAPTER=0 完全旁路")
ha = fresh(HARNESS_ADAPTER="0")
ok(ha.compose(BASE, "interview") == BASE, "关闭后等于接入前行为")
ok(ha.adapter_text("interview") == "", "关闭后不读文件")
ok(ha.status()["enabled"] is False, "status 反映关闭状态")

print("\n[4] 截断：超过 HARNESS_ADAPTER_MAX 只取前 N 个字符")
tmp = tempfile.mkdtemp(prefix="ha_test_")
d = os.path.join(tmp, "big")
os.makedirs(d)
with open(os.path.join(d, "ADAPTER.md"), "w", encoding="utf-8") as f:
    f.write("规" * 500)
ha = fresh(HARNESS_ADAPTER_DIR=tmp, HARNESS_ADAPTER_MAX="100")
ok(len(ha.adapter_text("big")) == 100, "按上限截断到 100 字")
ok(ha.compose(BASE, "big").startswith(BASE), "截断后 base 仍完整")

print("\n[5] 热更新：改文件后自动重新加载（不用重启）")
md = os.path.join(d, "ADAPTER.md")
with open(md, "w", encoding="utf-8") as f:
    f.write("第一版")
ha = fresh(HARNESS_ADAPTER_DIR=tmp)
ok(ha.adapter_text("big") == "第一版", "读到第一版")
time.sleep(0.02)
with open(md, "w", encoding="utf-8") as f:
    f.write("第二版")
os.utime(md, (time.time() + 1, time.time() + 1))
ok(ha.adapter_text("big") == "第二版", "mtime 变化后读到第二版")

print("\n[6] 清单合法性：adapters/*/adapter.yaml 符合 hermit 约定")
for name in ("interview", "review"):
    p = os.path.join(ADAPTERS, name, "adapter.yaml")
    ok(os.path.isfile(p), f"{name}/adapter.yaml 存在")
    if not os.path.isfile(p):
        continue
    raw = open(p, encoding="utf-8").read()
    ok(f"id: {name}" in raw, f"{name} 的 template.id 与目录名一致")
    ok("version:" in raw, f"{name} 声明了 template.version")
    ok("learnable:" in raw and "ADAPTER.md" in raw, f"{name} 把 ADAPTER.md 标为可学习")
    ok(os.path.isfile(os.path.join(ADAPTERS, name, "ADAPTER.md")), f"{name}/ADAPTER.md 存在")

print("\n[7] status()：给 /api/db/stats 的自检信息")
ha = fresh()
st = ha.status()
ok(set(st) == {"enabled", "backend", "dir", "adapters"}, "字段齐全")
ok(st["adapters"].get("interview", 0) > 0, "报告 interview 的字符数")
ok(st["adapters"].get("review", 0) > 0, "报告 review 的字符数")
ok(st["backend"] in ("hermit", "file", "none"), "backend 取值合法")

shutil.rmtree(tmp, ignore_errors=True)

print("\n[8] 接线守卫：6 个 system prompt 都经过 compose（改 prompt 时别把接线漏掉）")
import ast  # noqa: E402

WANT = {
    "interviewer_sys_prompt": "interview",
    "planner_sys_prompt": "interview",
    "scout_sys_prompt": "review",
    "reviewer_sys_prompt": "review",
    "judge_sys_prompt": "review",
    "reviewer_chat_sys_prompt": "review",
}
src = open(os.path.join(ROOT, "agent_server.py"), encoding="utf-8").read()
tree = ast.parse(src)
found = {}
for node in tree.body:
    if isinstance(node, ast.FunctionDef) and node.name in WANT:
        ret = next((n for n in ast.walk(node) if isinstance(n, ast.Return)), None)
        val = getattr(ret, "value", None)
        if isinstance(val, ast.Call) and getattr(val.func, "attr", "") == "compose":
            found[node.name] = val.args[1].value if len(val.args) > 1 else None
for fn, name in WANT.items():
    ok(found.get(fn) == name, f"{fn} → compose(..., {name!r})")

# 评分官故意不接 adapter：评分标准要稳定可复现，接之前先同步 README 的取舍说明
ok("evaluator_sys_prompt" not in found, "评分官保持不接（有意的取舍）")

print(f"\n{'=' * 46}\n{pass_n} passed, {fail_n} failed\n")
sys.exit(1 if fail_n else 0)
