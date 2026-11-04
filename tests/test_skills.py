# -*- coding: utf-8 -*-
"""
技能注册与归属回归测试（纯标准库）

覆盖：frontmatter 解析 / 全局与专家两档归属 / agents 收窄 /
未声明归属不生效 / 未知专家与未知角色 / 名称重复 / 空目录 /
热更新 / 全局开关 / 真实仓库内建技能的装配结果。

运行：.venv\\Scripts\\python.exe tests/test_skills.py
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, "backend", "skills")

from backend.app import config  # noqa: E402
from backend.app.services import skills as sk  # noqa: E402

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
    """把技能根目录指到临时目录并清缓存"""
    config.SKILLS_DIR = Path(path)
    config.SKILLS_ENABLED = True
    sk.reset_cache()


def ids(stage):
    """某个角色装载的技能目录名（去掉路径）"""
    return sorted(Path(p).name for p in sk.for_stage(stage))


print("\n[1] frontmatter 解析")
fm, body = sk.split_frontmatter("---\nname: a-b\ndescription: \"引用 描述\"\ntags: [x, y]\n---\n正文")
ok(fm.get("name") == "a-b", "普通键值")
ok(fm.get("description") == "引用 描述", "双引号被剥掉")
ok(fm.get("tags") == ["x", "y"], "行内列表")
fm2, _ = sk.split_frontmatter("---\nname: x\nexperts:\n  - interviewer\n  - reviewer\n---\n")
ok(fm2.get("experts") == ["interviewer", "reviewer"], "块列表")
fm3, body3 = sk.split_frontmatter("没有 frontmatter 的正文")
ok(fm3 == {} and body3 == "没有 frontmatter 的正文", "无 frontmatter 原样返回")
ok(sk.split_frontmatter("---\nname: 未闭合\n")[0] == {}, "未闭合的 frontmatter 按无元数据处理")

print("\n[2] 内建技能：识别与归属")
use_dir(SKILLS)
items = {it["id"]: it for it in sk.scan()}
ok(len(items) == 3, f"扫到 3 个技能（实际 {len(items)}）")
ok(items.get("cn-tech-writing", {}).get("scope") == "global", "cn-tech-writing 是全局")
ok(items.get("question-design", {}).get("scope") == "expert", "question-design 绑定专家")
ok(items.get("question-design", {}).get("experts") == ["interviewer"], "绑定到面试出题官")
ok(items.get("question-design", {}).get("title") == "场景化出题", "title 作为展示名解析出来")
ok(items.get("cn-tech-writing", {}).get("title") == "中文技术表达", "全局技能也有展示名")
ok(items.get("code-review-security", {}).get("experts") == ["reviewer"], "绑定到代码审核官")
ok(all(it["enabled"] for it in items.values()), "三个技能都生效")
ok(all(it["issues"] == [] for it in items.values()), "没有校验问题")

print("\n[3] 按角色解析装载清单")
ok(ids("interviewer") == ["cn-tech-writing", "question-design"], "面试官：出题方法论 + 全局")
ok(ids("planner") == ["cn-tech-writing", "question-design"], "策划官：同面试官（agents 显式包含）")
ok(ids("grader") == ["cn-tech-writing"], "评分官：只有全局（agents 未包含）")
ok(ids("reviewer") == ["cn-tech-writing", "code-review-security"], "审核官：安全清单 + 全局")
ok(ids("scout") == ["cn-tech-writing", "code-review-security"], "侦察官：安全清单也给了（预判风险区）")
ok(ids("judge") == ["cn-tech-writing"], "裁决官：只有全局")
ok(ids("advisor") == ["cn-tech-writing"], "审核答疑：只有全局")
ok(ids("") == [], "空角色不装载任何技能")
ok(ids("not-a-stage") == ["cn-tech-writing"], "未知角色至少拿到全局")
ok(sk.for_stage("interviewer")[0].endswith("SKILL.md") is False, "返回的是目录而不是文件")

print("\n[4] assignments：前端展示用的分组")
a = sk.assignments()
ok(len(a["global"]) == 1 and a["global"][0]["id"] == "cn-tech-writing", "全局组只有 1 个")
ok(len(a["experts"]) == 2, "两位专家")
by = {e["id"]: e for e in a["experts"]}
ok([s["id"] for s in by["interviewer"]["skills"]] == ["question-design"], "面试出题官绑 1 个")
ok(by["interviewer"]["skills"][0]["title"] == "场景化出题", "绑定列表带展示名（前端不用显示 kebab-case）")
ok([s["id"] for s in by["reviewer"]["skills"]] == ["code-review-security"], "代码审核官绑 1 个")
ok(by["interviewer"]["stages"] == ["planner", "interviewer", "grader"], "专家覆盖的角色齐全")
ok(a["unassigned"] == [], "没有未生效的技能")

print("\n[5] 配置错误：不该静默生效")
tmp = tempfile.mkdtemp(prefix="skills_test_")
try:
    use_dir(tmp)
    ok(sk.scan() == [], "空目录 → 没有技能")
    ok(sk.for_stage("interviewer") == [], "空目录 → 不装载")

    def put(folder, text):
        d = Path(tmp) / folder
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(text, encoding="utf-8")
        sk.reset_cache()

    put("no-scope", "---\nname: no-scope\ndescription: 没写 scope\n---\n正文")
    it = sk.scan()[0]
    ok(it["enabled"] is False, "未声明 scope → 不生效")
    ok(it["scope"] == "unassigned", "scope 归一为 unassigned")
    ok(it["title"] == "", "没写 title 时为空串（前端回退到 name）")
    ok(any("未声明归属" in s for s in it["issues"]), "issues 提示怎么补")
    ok(sk.for_stage("interviewer") == [], "未声明 scope 的技能不会被装载")

    put("bad-expert", "---\nname: bad-expert\ndescription: 专家写错了\nscope: expert\nexperts: [nobody]\n---\n")
    it = sk.scan()[0]
    ok(it["enabled"] is False, "未知专家 → 不生效")
    ok(any("未知专家" in s for s in it["issues"]), "提示未知专家")

    put("no-desc", "---\nname: no-desc\nscope: global\n---\n")
    it = sk.scan()[0]
    ok(it["enabled"] is False and any("description" in s for s in it["issues"]), "缺 description → 不生效")

    put("bad-name", "---\nname: Bad_Name\ndescription: 名字不规范\nscope: global\n---\n")
    it = sk.scan()[0]
    ok(it["enabled"] is False and any("kebab-case" in s for s in it["issues"]), "非 kebab-case → 不生效")

    shutil.rmtree(Path(tmp) / "no-scope")
    shutil.rmtree(Path(tmp) / "bad-expert")
    shutil.rmtree(Path(tmp) / "no-desc")
    shutil.rmtree(Path(tmp) / "bad-name")
    put("dup", "---\nname: dup\ndescription: 甲\nscope: global\n---\n")
    put("dup2", "---\nname: dup\ndescription: 乙\nscope: global\n---\n")
    dups = sk.scan()
    ok(all(it["enabled"] is False for it in dups), "同名技能全部不生效")
    ok(all(any("名称重复" in s for s in it["issues"]) for it in dups), "提示名称重复")

    print("\n[6] 归属跟随库：库里加智能体，技能自动跟上")
    shutil.rmtree(Path(tmp) / "dup")
    shutil.rmtree(Path(tmp) / "dup2")
    put("whole-expert", "---\nname: whole-expert\ndescription: 绑定整个专家\nscope: expert\nexperts: [interviewer]\n---\n")
    put("global-x", "---\nname: global-x\ndescription: 全局\nscope: global\n---\n")

    # 模拟库里给面试专家新增了一个角色 mentor（常量里没有）
    orig_experts = sk._experts
    sk._experts = lambda: [
        {"id": "interviewer", "mode": "interview", "label": "面试出题官", "icon": "面",
         "stages": ["planner", "interviewer", "grader", "mentor"]},
        {"id": "reviewer", "mode": "review", "label": "代码审核官", "icon": "审",
         "stages": ["scout", "reviewer", "judge", "advisor"]},
    ]
    sk.reset_cache()
    ok(ids("mentor") == ["global-x", "whole-expert"],
       "库里新增的 mentor 自动拿到专家技能与全局技能")
    ok(ids("grader") == ["global-x", "whole-expert"], "原有角色不受影响")
    ok("mentor" in [s for it in sk.scan() if it["id"] == "whole-expert" for s in it["stages"]],
       "专家技能的 stages 按库展开")
    ok("mentor" in [s for it in sk.scan() if it["id"] == "global-x" for s in it["stages"]],
       "全局技能的 stages 也按库展开（不是写死 7 个）")
    sk._experts = orig_experts
    sk.reset_cache()
    ok(ids("mentor") == ["global-x"],
       "回到常量兜底后 mentor 不再属于任何专家，只剩全局技能")

    print("\n[7] agents 收窄 + 热更新")
    put("narrow", "---\nname: narrow\ndescription: 只给评分官\nscope: expert\nexperts: [interviewer]\nagents: [grader]\n---\n")
    ok(ids("grader") == ["global-x", "narrow", "whole-expert"], "agents 收窄后只有评分官装载")
    ok(ids("interviewer") == ["global-x", "whole-expert"], "同专家的其他角色拿不到 narrow")

    md = Path(tmp) / "narrow" / "SKILL.md"
    md.write_text("---\nname: narrow\ndescription: 改了归属\nscope: global\n---\n", encoding="utf-8")
    time.sleep(0.01)
    os.utime(md, None)
    ok(ids("interviewer") == ["global-x", "narrow", "whole-expert"],
       "改 SKILL.md 后热更新生效（无需重启）")

    print("\n[8] 全局开关")
    config.SKILLS_ENABLED = False
    sk.reset_cache()
    ok(sk.scan() == [] and sk.for_stage("interviewer") == [], "SKILLS_ENABLED=0 → 全部不装载")
    ok(sk.status()["enabled"] is False, "status 反映开关状态")
finally:
    shutil.rmtree(tmp, ignore_errors=True)
    config.SKILLS_DIR = Path(SKILLS)
    config.SKILLS_ENABLED = True
    sk.reset_cache()

print("\n[9] 内建技能的书写规范")
use_dir(SKILLS)
ok(sk.status()["source"] == "fallback", "没连库时归属回退常量（source=fallback）")
for it in sk.scan():
    text = (Path(it["path"]) / "SKILL.md").read_text(encoding="utf-8")
    lines = len(text.splitlines())
    ok(lines <= 500, f"{it['id']} 正文 {lines} 行（上限 500）")
    ok("description" in text.split("---")[1], f"{it['id']} 有 description")
    ok(it["description"].strip() != "", f"{it['id']} description 非空")

print(f"\n结果：{pass_n} 通过 / {fail_n} 失败")
sys.exit(1 if fail_n else 0)
