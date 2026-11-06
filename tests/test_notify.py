# -*- coding: utf-8 -*-
"""
通知渠道层回归测试（纯标准库，不发网络请求）

覆盖：渠道清单解析 / 坏渠道不拖垮服务 / enabled:false 与「未配 webhook」的区别 /
一个渠道失败不影响其他渠道 / 硬规则在事件层统一执行 / webhook 脱敏 /
企业微信渲染七项要素 / 兼容层仍然可用。

运行：.venv\\Scripts\\python.exe tests/test_notify.py
"""

import asyncio
import os
import shutil
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# 显式指定临时目录根，并统一在末尾删掉。
# 不加 `dir=` 时本机会回落到「当前工作目录」——tempfile 逐个试候选目录，
# 前几个写不进去就退到 cwd，于是仓库根冒出一堆 notif-xxxx/ 脏目录。
_TMP_ROOT = Path(ROOT) / ".tmp"
_TMP_ROOT.mkdir(exist_ok=True)
_made: list[str] = []


def mkdtmp(prefix: str) -> str:
    p = tempfile.mkdtemp(prefix=prefix, dir=str(_TMP_ROOT))
    _made.append(p)
    return p

from backend.app import config  # noqa: E402  （import 时会读仓库根 .env）

# 测试必须与「本机有没有配 WECOM_WEBHOOK」完全无关：
#   1. 有值时会真的往生产群发消息（测试跑出社交事故）
#   2. 「没配置时优雅跳过」这条契约就测不出来了
# 必须在 import 下面几个模块**之前**摘掉：兼容层 wecom 在 import 时就把
# 环境变量读进了模块级常量，之后再 pop 已经晚了。
os.environ.pop("WECOM_WEBHOOK", None)

from backend.app import notify  # noqa: E402
from backend.app.notify import registry  # noqa: E402
from backend.app.services import notify_event, wecom  # noqa: E402

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


def use_dir(path):
    """把渠道根目录指到临时目录并清缓存"""
    config.NOTIFIERS_DIR = Path(path)
    config.NOTIFIERS_ENABLED = True
    registry.reset_cache()


SAMPLE_YAML = """\
id: {cid}
name: {name}
enabled: {enabled}
entry:
  send: notifier:send
options:
  webhook: "{webhook}"
"""

SAMPLE_PY = """\
async def send(event, options):
    return {{"ok": True, "ms": 1, "seen": event.get("pr", {{}}).get("number")}}

def available(options):
    return bool(options.get("webhook"))
"""


def make_channel(root, cid, *, name="测试渠道", enabled="true", webhook="https://x/y",
                 py=SAMPLE_PY):
    d = Path(root) / cid
    d.mkdir(parents=True, exist_ok=True)
    (d / "notifier.yaml").write_text(
        SAMPLE_YAML.format(cid=cid, name=name, enabled=enabled, webhook=webhook),
        encoding="utf-8")
    (d / "notifier.py").write_text(py, encoding="utf-8")


def tiny_event():
    return notify_event.build(
        {"number": 7, "title": "T", "author": "a", "url": "https://x/p/7"},
        {"risk": "low", "decision": "approve", "summary": "ok",
         "feature": "f", "stats": {"files": 1, "additions": 2, "deletions": 3},
         "issues": []},
    )


print("\n[1] 真实渠道：企业微信被扫到且配置可读")
real_dir = config.NOTIFIERS_DIR
ch = registry.get("wecom")
ok(ch is not None, "扫到了 wecom 渠道")
ok(ch and ch.name == "企业微信群机器人", "渠道名来自 yaml")
ok(ch and "send" in ch.entry, "声明了 entry.send")
ok(ch and not registry.problems(), "没有配置问题")

# 仓库里 webhook 一律留空（public 仓库不能带 key），真实值走 WECOM_WEBHOOK。
# 测试开头已经把它摘掉了，所以这里恒为「未配置」——验的是 available() 钩子
# 在没配 key 时说「不可用」，这正是它存在的意义。
ok(ch and ch.available() is False, "仓库里 webhook 留空 → 渠道不可用")

print("\n[2] webhook 脱敏：自检接口不能把 key 原样带出去")
# 用一份带假 key 的 options 直接验脱敏规则本身，不依赖仓库配置里有没有真值
fake = registry._mask({"webhook": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=abcdef12-3456-7890",
                       "max_blocking": 5})
ok(fake["webhook"].startswith("…"), "webhook 只留尾部")
ok("abcdef12" not in fake["webhook"], "key 主体没有泄漏")
ok(fake["max_blocking"] == 5, "非敏感配置原样保留")
ok(registry._mask({"webhook": ""})["webhook"] == "", "空值脱敏后仍是空（不显示成已配）")
# 太短的值（≤6 字符）无法「留尾」——那样等于把整个值露出去，一律显示「已配」
ok(registry._mask({"token": "short"})["token"] == "(已配)", "短值不露内容，只说已配置")
desc = ch.describe()
ok("webhook" in desc["options"] and desc["options"].get("max_blocking") == 5,
   "describe 带出渠道配置（max_blocking 原样）")

print("\n[3] 坏渠道不拖垮服务")
tmp = mkdtmp("notif-")
try:
    make_channel(tmp, "good")
    make_channel(tmp, "noyaml", py=SAMPLE_PY)
    (Path(tmp) / "noyaml" / "notifier.yaml").unlink()      # 没清单 → 直接跳过，不算 problem
    make_channel(tmp, "badyaml")
    (Path(tmp) / "badyaml" / "notifier.yaml").write_text("- 不是字典\n", encoding="utf-8")
    make_channel(tmp, "nosend")
    (Path(tmp) / "nosend" / "notifier.yaml").write_text(
        "id: nosend\nname: 没 send\nentry: {}\n", encoding="utf-8")
    make_channel(tmp, "Bad-Name")                          # 目录名不合法

    use_dir(tmp)
    ids = sorted(c.id for c in registry.all_channels())
    ok(ids == ["good"], f"只装载了合法渠道（实际 {ids}）")
    probs = {p["id"] for p in registry.problems()}
    ok("badyaml" in probs, "顶层不是字典 → 记 problem")
    ok("nosend" in probs, "缺 entry.send → 记 problem")
    ok("Bad-Name" in probs, "目录名不合法 → 记 problem")
    ok("noyaml" not in probs, "没有 notifier.yaml 的目录静默跳过（不当成错误）")

    print("\n[4] enabled:false 与「未配 webhook」是两回事")
    make_channel(tmp, "off", enabled="false")
    registry.reset_cache()
    ok(registry.get("off").enabled is False, "enabled:false 被识别")
    active = sorted(c.id for c in registry.select())
    ok("off" not in active, f"停用的渠道不进 select（实际 {active}）")
    ok("off" in [c.id for c in registry.all_channels()], "停用的渠道仍出现在自检里")

    # available() 钩子：用真实渠道测真正的契约，临时目录里的桩 import 不到，
    # 会走「判断不了就当可用」的兜底，测不出钩子本身
    from backend.notifiers.wecom import notifier as wecom_mod
    ok(wecom_mod.available({}) is False, "空 webhook → 不可用")
    ok(wecom_mod.available({"webhook": "https://x"}) is True, "有 webhook → 可用")
    ok(wecom_mod.available({"webhook": "  "}) is False, "纯空格也算没配")

    # 兜底行为本身也要固定：模块 import 不进来时当作可用，
    # 让 send() 去报真正的错，而不是「静默永不通知」
    broken = registry.get("good")
    ok(broken.available() is True, "模块加载不了时兜底为可用（错误交给 send 报）")

    print("\n[5] 派发：渠道抛异常也会被接住并记录原因")
    # 临时目录里的桩模块在 backend.notifiers.<id> 下 import 不到（包路径是硬编码的），
    # 所以这里测的是「派发器不会因为渠道加载/执行失败而整体炸掉」这条契约：
    # 失败被转成每条结果里的 error，而不是往上抛。
    res = asyncio.run(notify.dispatch(tiny_event()))
    ok(res["ok"] is False, "渠道加载不了时整体不成功")
    ok(bool(res["error"]), "给出了失败原因（而不是静默）")
    ok(len(res["sent"]) == 1 and res["sent"][0]["ok"] is False, "逐条记录了成败")
    ok("ModuleNotFoundError" in res["sent"][0]["error"], "原因具体到「模块加载不了」")

    print("\n[6] 一个渠道都用不了时，说清是哪种情况")
    use_dir(mkdtmp("notif-empty-"))
    res = asyncio.run(notify.dispatch(tiny_event()))
    ok(res["ok"] is False, "没有渠道 → 不成功")
    ok("没有配置任何通知渠道" in res["error"], "说明是「没配渠道」：" + res["error"])

    config.NOTIFIERS_ENABLED = False
    res = asyncio.run(notify.dispatch(tiny_event()))
    ok("NOTIFIERS_ENABLED" in res["error"], "说明是「总开关关了」")
    config.NOTIFIERS_ENABLED = True
finally:
    config.NOTIFIERS_DIR = real_dir
    config.NOTIFIERS_ENABLED = True
    registry.reset_cache()

print("\n[7] 硬规则在事件层统一执行（渠道不再各算各的）")
blocking_review = {
    "risk": "low", "decision": "approve", "summary": "",
    "issues": [{"severity": "blocker", "file": "a.java", "line": 1, "title": "炸"}],
}
ev = notify_event.build({"number": 1}, blocking_review)
ok(ev["decision"] == "request_changes", "有 blocker 时 approve 被纠正")
ok(ev["risk"] == "high", "有 blocker 时风险抬到 high")
ok(ev["mergeable"] is False, "有 blocker 时不可合并")
ok(ev["blocking"] == 1, "blocking 计数正确")

warn_review = {"risk": "medium", "decision": "comment", "issues": [
    {"severity": "minor", "file": "b.java", "line": 2, "title": "小问题"}]}
ev2 = notify_event.build({"number": 2}, warn_review)
ok(ev2["decision"] == "comment", "只有 minor 时保留原结论")
ok(ev2["mergeable"] is True, "只有 minor 时可合并")

clean = notify_event.build({"number": 3}, {"risk": "low", "decision": "approve", "issues": []})
ok(clean["mergeable"] is True and clean["issueCount"] == 0, "没问题时可合并")

print("\n[8] 事件体字段完备且容错")
ok(set(notify_event.EVENT_FIELDS) <= set(clean.keys()), "事件包含全部声明字段")
empty = notify_event.build({}, {})
ok(empty["issueCount"] == 0 and empty["counts"]["blocker"] == 0, "空入参不炸")
weird = notify_event.build({}, {"issues": ["字符串", None, {"severity": "x"}]})
ok(weird["issueCount"] == 3, "脏 issues 仍计入条数")
ok(all(isinstance(i, dict) for i in weird["issues"]), "输出时统一成 dict")
with_repo = notify_event.build({"number": 9}, {}, repo={"owner": "g", "name": "r"})
ok(with_repo["repo"] == "g/r", "pr 没带 repo 时用仓库信息拼")

print("\n[9] 企业微信渲染：七项要素仍在")
pr = {"number": 8, "title": "标题", "author": "hedongyun",
      "url": "https://x/p/8", "repo": "g/r", "headBranch": "dev", "baseBranch": "sit"}
review = {
    "risk": "high", "decision": "request_changes", "summary": "总结",
    "feature": "做了些事", "stats": {"files": 26, "additions": 780, "deletions": 119},
    "issues": [{"severity": "blocker", "file": "a/B.java", "line": 111, "title": "写死"}],
}
ev3 = notify_event.build(pr, review)
md = wecom.build_markdown(pr, review)
for key, label in (
    ("**级别**", "级别"), ("**问题**", "数量"), ("**结论**", "是否可合并"),
    ("**功能**", "功能"), ("**开发者**", "开发者"), ("**代码量**", "代码量"),
):
    ok(key in md, "消息含「" + label + "」")
ok(pr["url"] in md, "消息含 PR 链接")
ok("26 个文件" in md and "+780" in md, "代码量数值正确")
ok("高风险" in md, "级别文案正确")
ok("不建议合并" in md, "结论文案正确")

print("\n[10] max_blocking 可配")
from backend.notifiers.wecom import notifier as wecom_mod  # noqa: E402

opts = dict(registry.get("wecom").options) if registry.get("wecom") else {}
many = dict(review, issues=[{"severity": "major", "file": f"f{i}.java", "line": i,
                             "title": "问题"} for i in range(12)])
ev_many = notify_event.build(pr, many)
md5 = wecom_mod.render(ev_many, {**opts, "max_blocking": 5})
ok(md5.count("- 🟠") == 5, "默认列 5 条")
ok("另有 7 处" in md5, "超出的用「另有 N 处」收尾")
md2 = wecom_mod.render(ev_many, {**opts, "max_blocking": 2})
ok(md2.count("- 🟠") == 2, "改成 2 条后只列 2 条")

print("\n[11] 兼容层：老调用方仍能按原样调用")
ok(callable(wecom.should_notify), "should_notify 还在")
ok(callable(wecom.build_markdown), "build_markdown 还在")
ok(callable(wecom.notify_review), "notify_review 还在")
ok(isinstance(wecom.build_markdown(pr, review), str), "build_markdown 返回正文")
# 没配 webhook 时应当「跳过」而不是抛异常——这正是老调用方依赖的行为
res = asyncio.run(wecom.notify_review(pr, review, webhook=""))
ok(res.get("ok") is False and "skipped" in res, "没配 webhook 时优雅跳过")
ok(wecom.should_notify("") is False, "空 webhook 时 should_notify 说不能发")

print("\n" + "=" * 46)
print(f"结果：{pass_n} 通过 / {fail_n} 失败")

# 临时目录统一在 .tmp/ 下建、在这里删：留在项目根会让 git status 一直不干净，
# 而且下次跑测试又会在同一位置堆一批新的
for p in _made:
    shutil.rmtree(p, ignore_errors=True)

sys.exit(1 if fail_n else 0)
