# -*- coding: utf-8 -*-
"""
企业微信推送回归测试（纯标准库，不发网络请求）

覆盖：七项必备要素齐全 / 级别与数量口径 / 「有阻塞就不许说可以合入」硬规则 /
issue 缺字段不炸 / 超长裁剪 / 未配置 webhook 时优雅跳过。

运行：.venv\\Scripts\\python.exe tests/test_wecom.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app.services import wecom  # noqa: E402

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


PR = {
    "number": 8,
    "title": "dev 合入 sit：线下支付统一为 OFFLINE",
    "author": "hedongyun",
    "url": "https://git.example.com/group/repo/pulls/8",
    "repo": "group/repo",
    "headBranch": "dev",
    "baseBranch": "sit",
}

REVIEW = {
    "risk": "high",
    "decision": "request_changes",
    "summary": "接入编号写死，存量数据无迁移。",
    "feature": "线下支付统一为 OFFLINE，新增线下支付登记与 PDF 导出。",
    "stats": {"files": 26, "additions": 780, "deletions": 119},
    "issues": [
        {"severity": "blocker", "file": "a/B.java", "line": 111, "title": "编号写死"},
        {"severity": "major", "file": "a/C.java", "line": 45, "title": "存量单失联"},
        {"severity": "minor", "file": "a/D.java", "line": 9, "title": "命名不规范"},
    ],
}

print("\n[1] 七项必备要素齐全")
md = wecom.build_markdown(PR, REVIEW)
ok("**级别**" in md, "有「级别」")
ok("**问题**" in md and "3 处" in md, "有「数量」（3 处）")
ok("**结论**" in md, "有「是否可合并」")
ok(PR["url"] in md, "带上了 PR 链接")
ok("线下支付统一为 OFFLINE" in md, "有「对应的功能」")
ok("hedongyun" in md, "有「开发者」")
ok("26 个文件" in md and "+780" in md and "119" in md, "有「代码量」")

print("\n[2] 级别与数量口径")
ok("高风险" in md, "high → 高风险")
ok("🔴阻塞 1" in md, "列出阻塞数")
ok("🟠重要 1" in md, "列出重要数")
ok("🚫 不建议合并" in md, "request_changes → 不建议合并")

print("\n[3] 「有阻塞就不许说可以合入」硬规则")
soft = dict(REVIEW, decision="approve", risk="low")
md_soft = wecom.build_markdown(PR, soft)
ok("不建议合并" in md_soft, "有 blocker 时，approve 被纠正为不建议合并")
ok("高风险" in md_soft, "有 blocker 时，级别抬到高风险")

clean = dict(REVIEW, decision="approve", risk="low", issues=[])
md_clean = wecom.build_markdown(PR, clean)
ok("✅ 可以合入" in md_clean, "真没问题时才是可以合入")
ok("无问题" in md_clean, "没有问题时不罗列 0 处")
ok("低风险" in md_clean, "low → 低风险")

suggest = dict(REVIEW, decision="comment", risk="medium",
               issues=[{"severity": "minor", "file": "a/D.java", "line": 9, "title": "命名"}])
md_suggest = wecom.build_markdown(PR, suggest)
ok("可合入，建议先改" in md_suggest, "comment → 可合入建议先改")
ok("中风险" in md_suggest, "medium → 中风险")

print("\n[4] 缺字段 / 脏数据不能炸")
bare = wecom.build_markdown({}, {})
ok(isinstance(bare, str) and len(bare) > 0, "空入参也能生成消息")
ok("（未指明）" in bare, "没有开发者时写明未指明")
weird = dict(REVIEW, issues=["字符串", None, {"severity": "胡说"}, {"no_severity": 1}])
md_weird = wecom.build_markdown(PR, weird)
ok(isinstance(md_weird, str), "issues 里混入脏值不抛异常")
nostats = dict(REVIEW, stats=None, feature="")
md_nostats = wecom.build_markdown(PR, nostats)
ok("0 个文件" in md_nostats, "没有代码量时显示 0")
ok("未提取到功能描述" in md_nostats, "没有功能时给出说明而不是空白")
nourl = wecom.build_markdown(dict(PR, url=""), REVIEW)
ok("查看 PR" not in nourl, "没有 url 时不生成空链接")

print("\n[5] 过长时裁剪")
many = {
    "severity": "major", "file": "x" * 200, "line": 1, "title": "很长的问题" * 20,
}
big = dict(REVIEW, issues=[many] * 30, summary="摘要" * 300)
md_big = wecom.build_markdown(PR, big)
ok(len(md_big.encode("utf-8")) <= wecom.MAX_BYTES, "超长消息被裁到上限内")
ok("**级别**" in md_big and "**结论**" in md_big, "裁剪后仍保留级别与结论")
ok("26 个文件" in md_big, "裁剪后仍保留代码量")
ok(PR["url"] in md_big, "裁剪后仍保留 PR 链接")

# 极端：超长文件名 + 60 条问题，砍掉摘要后仍超限，要继续砍清单
huge = dict(
    REVIEW,
    summary="摘" * 600,
    feature="功" * 600,
    stats={"files": 99, "additions": 9999, "deletions": 8888},
    issues=[{"severity": "major", "file": "/".join(["dir"] * 80) + "/F.java",
             "line": i, "title": "严重问题描述" * 15} for i in range(60)],
)
md_huge = wecom.build_markdown(dict(PR, title="标题" * 80), huge)
ok(len(md_huge.encode("utf-8")) <= wecom.MAX_BYTES, "极端超长也裁到上限内（分级裁剪）")
for key in ("**级别**", "**结论**", "**功能**", "**开发者**", "**代码量**"):
    ok(key in md_huge, "极端裁剪后仍保留 " + key)
ok(PR["url"] in md_huge, "极端裁剪后仍保留 PR 链接")

print("\n[6] 未配置 webhook 时优雅跳过")
# 配置搬到了 notifier.yaml（渠道化之后），不再有全局 WEBHOOK_URL 可改。
# 直接测渠道实现层的契约：空 webhook 应当返回 skipped，而不是抛异常。
from backend.notifiers.wecom import notifier as wecom_impl  # noqa: E402

# 摘掉环境变量：config.py 会读仓库根的 .env，本机配了 WECOM_WEBHOOK 时
# 这里再测「没配」就永远是 False，而且 send() 会真的往生产群发消息。
os.environ.pop("WECOM_WEBHOOK", None)

ok(wecom_impl.available({}) is False, "空 webhook 时渠道不可用")
res = asyncio.run(wecom_impl.send({"pr": {"number": 1}}, {}))
ok(res.get("ok") is False and "skipped" in res, "发送被跳过而不是抛异常")
ok(wecom_impl.available({"webhook": "https://x"}) is True, "配了 webhook 才可用")

# 渲染与发送是分开的：没配 webhook 时仍然要能渲出正文来排查
ok(isinstance(wecom_impl.render({"pr": {"number": 1}}, {}), str),
   "没配 webhook 也能渲染（便于排查格式）")

print("\n[7] _clip 截断")
ok(wecom._clip("abc", 10) == "abc", "短文本原样")
ok(len(wecom._clip("a" * 100, 10)) == 10, "长文本截到指定长度")
ok(wecom._clip("a\nb", 10) == "a b", "换行被压成空格")
ok(wecom._clip(None, 10) == "", "None 变成空串")

print("\n" + "=" * 46)
print(f"结果：{pass_n} 通过 / {fail_n} 失败")
sys.exit(1 if fail_n else 0)
