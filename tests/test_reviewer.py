# -*- coding: utf-8 -*-
"""
代码托管平台适配层测试（Python / reviewer.py）

覆盖：仓库地址解析（GitHub / GitLab / 简写 / SSH / 带后缀 / 非法）、
diff 统计与截断、代理选择规则。不发起真实外网请求（那部分在 e2e 里验证）。

运行：.venv/Scripts/python.exe tests/test_reviewer.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import reviewer as rv  # noqa: E402

pass_n = 0
fail_n = 0


def ok(cond, label):
    global pass_n, fail_n
    if cond:
        pass_n += 1
        print(f"  ✓ {label}")
    else:
        fail_n += 1
        print(f"  ✗ {label}")


def section(title):
    print(f"\n— {title} —")


def main():
    section("地址解析 · GitHub")
    p = rv.parse_repo_url("https://github.com/ztangeeoo/work-agent")
    ok(p == {"provider": "github", "host": "github.com", "owner": "ztangeeoo",
             "name": "work-agent", "url": "https://github.com/ztangeeoo/work-agent"},
       "标准 HTTPS 地址")
    ok(rv.parse_repo_url("https://github.com/owner/repo.git")["name"] == "repo", "去掉 .git 后缀")
    ok(rv.parse_repo_url("github.com/owner/repo.git")["owner"] == "owner", "无协议头也能解析")
    ok(rv.parse_repo_url("git@github.com:owner/repo.git")["url"] == "https://github.com/owner/repo", "SSH scp 形式")
    ok(rv.parse_repo_url("owner/repo")["url"] == "https://github.com/owner/repo", "owner/repo 简写默认 GitHub")
    ok(rv.parse_repo_url("https://github.com/owner/repo/-/tree/main")["name"] == "repo", "剥离 /-/tree 后缀")
    ok(rv.parse_repo_url("  https://github.com/a/b  ")["owner"] == "a", "自动去空白")
    ok(rv.parse_repo_url("https://github.com/A/B", "github")["name"] == "B", "provider 显式传 github")

    section("地址解析 · GitLab")
    g = rv.parse_repo_url("https://gitlab.com/group/sub/repo")
    ok(g["provider"] == "gitlab" and g["owner"] == "group/sub" and g["name"] == "repo", "GitLab 子组（多级 owner）")
    ok(rv.parse_repo_url("https://gitlab.example.com/a/b")["provider"] == "gitlab", "自建 GitLab 域名识别")
    ok(rv.parse_repo_url("https://example.com/a/b", "gitlab")["provider"] == "gitlab", "provider 覆盖生效")
    ok(rv.parse_repo_url("https://gitlab.com/g/a/b/-/merge_requests/3")["owner"] == "g/a", "GitLab MR 网页地址")

    section("地址解析 · 非法输入")
    for bad in ("", "   ", None):
        try:
            rv.parse_repo_url(bad)
            ok(False, f"空地址应报错：{bad!r}")
        except ValueError:
            ok(True, "空地址报错")
    try:
        rv.parse_repo_url("https://github.com/onlyowner")
        ok(False, "缺仓库名应报错")
    except ValueError as e:
        ok("没有仓库名" in str(e), "缺仓库名报错并给出提示")

    section("diff 统计")
    d = ("diff --git a/x.js b/x.js\n--- a/x.js\n+++ b/x.js\n@@ -1,2 +1,3 @@\n context\n+added1\n+added2\n-removed\n"
         "diff --git a/y.css b/y.css\n+one\n")
    st = rv.diff_stats(d)
    ok(st["files"] == 2, "文件数统计")
    ok(st["additions"] == 3, "新增行统计（忽略 +++）")
    ok(st["deletions"] == 1, "删除行统计（忽略 ---）")
    ok(rv.diff_stats("")["files"] == 0, "空 diff 统计为 0")

    section("diff 截断")
    big = "diff --git a/f b/f\n" + "+x\n" * 40000
    out = rv.truncate_diff(big, 3)
    ok(len(out) <= rv.MAX_DIFF_CHARS + 160, "超长 diff 被截断")
    ok("已截断" in out and "3 个文件" in out, "截断提示含文件数")
    ok(rv.truncate_diff("短内容") == "短内容", "短 diff 原样返回")
    ok(rv.truncate_diff("   ") == "", "空白 diff 返回空串")

    section("代理选择规则")
    saved = {k: os.environ.get(k) for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")}
    try:
        for k in saved:
            os.environ.pop(k, None)
        ok(rv._proxy_for("https://api.github.com/x") is None, "无代理环境变量 → 直连")
        ok(rv._proxy_for("http://127.0.0.1:9999/x") is None, "localhost 永远直连")

        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
        ok(rv._proxy_for("https://api.github.com/x") == "http://127.0.0.1:7890", "https 目标走 HTTPS_PROXY")
        ok(rv._proxy_for("http://example.com/x") is None, "http 目标不误用 HTTPS_PROXY")

        os.environ["HTTP_PROXY"] = "http://proxy.corp:8080"
        ok(rv._proxy_for("http://example.com/x") == "http://proxy.corp:8080", "http 目标走 HTTP_PROXY")

        os.environ["HTTPS_PROXY"] = "http://api.github.com:8080"
        ok(rv._proxy_for("https://api.github.com/x") != "http://api.github.com:8080",
           "代理地址与目标同主机 → 直连（防自环）")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    section("常量")
    ok(rv.MAX_REVIEWS_PER_POLL == 3, "单轮审核上限 3 个")
    ok(rv.MAX_DIFF_CHARS == 60000, "diff 上限 60000 字符")

    print(f"\n结果：{pass_n} 通过 / {fail_n} 失败\n")
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
