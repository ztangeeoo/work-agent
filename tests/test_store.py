# -*- coding: utf-8 -*-
"""
持久层回归测试（Python / 标准库 sqlite3）

覆盖：会话读写 / 覆盖重写后 FTS 清理 / 中文 trigram / 短词 LIKE 回退 /
多词 AND / 排序 / 级联删除 / 用户隔离 / 400 条截断 / id 校验 / stats /
仓库配置（token 留存与不下发、interval 上下限）/ PR 记录 /
审核报告（同 head 去重、列表、回帖标记、级联删除）。

运行：.venv/Scripts/python.exe tests/test_store.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_tmpdir = tempfile.mkdtemp(prefix="store_test_")
os.environ["DB_PATH"] = os.path.join(_tmpdir, "interview.db")

import store  # noqa: E402  必须在设置 DB_PATH 之后导入

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
    store.init()

    # ---------------------------------------------------------------- 会话
    section("会话读写")
    r = store.save_convo({
        "id": "c1",
        "title": "高并发面试",
        "messages": [
            {"id": "m1", "role": "user", "content": "请出 5 道缓存方向的题"},
            {"id": "m2", "role": "assistant", "content": "### 第 1 题【缓存穿透】\n布隆过滤器与缓存空值，注意缓存雪崩的解决方案"},
        ],
        "profile": {"track": "后端", "level": "中级"},
    })
    ok(r["id"] == "c1" and r["count"] == 2, "保存会话返回 id 与消息数")
    lst = store.list_convos()
    ok(len(lst) == 1 and lst[0]["id"] == "c1", "会话列表可读")
    ok(len(lst[0]["messages"]) == 2, "会话列表默认带消息")
    ok(lst[0]["profile"]["track"] == "后端", "profile 快照往返一致")
    ok(store.get_convo("c1")["title"] == "高并发面试", "单会话读取")
    ok(store.get_convo("nope") is None, "不存在的会话返回 None")
    ok("messages" not in (store.list_convos(limit=1, with_messages=False)[0]), "messages=0 时不带消息")

    ok(store.save_convo({"id": "c2", "title": "新会话", "messages": []})["count"] == 0, "空会话可保存")

    # 角色收敛：非 user 一律 assistant
    store.save_convo({"id": "c3", "messages": [{"id": "x1", "role": "system", "content": "hi"}]})
    ok(store.get_convo("c3")["messages"][0]["role"] == "assistant", "非 user 角色收敛为 assistant")

    # 400 条截断（msg.id 全局唯一，模拟真实客户端：每个会话用不同 id）
    many = [{"id": f"b{i}", "role": "user", "content": f"第{i}条"} for i in range(450)]
    ok(store.save_convo({"id": "c4", "messages": many})["count"] == store.MAX_MSGS, "单会话消息上限 400 截断")
    ok(len(store.get_convo("c4")["messages"]) == store.MAX_MSGS, "落库消息数等于上限")

    section("排序 / 删除 / 隔离")
    store.delete_convo("c2")
    ids = [c["id"] for c in store.list_convos()]
    ok("c2" not in ids, "删除会话生效")
    ok(store.delete_convo("nope") is False, "删不存在的会话返回 False")
    ok(store.list_convos("other") == [], "用户隔离：别的 user 看不到")
    ok(store.get_convo("c1", "other") is None, "用户隔离：跨用户取单会话为 None")

    # 更新时间倒序
    store.save_convo({"id": "old", "messages": [], "updatedAt": 1000})
    store.save_convo({"id": "new", "messages": [], "updatedAt": 9_000_000_000_000})
    ok(store.list_convos()[0]["id"] == "new", "会话列表按 updatedAt 倒序")

    # ---------------------------------------------------------------- 检索
    section("全文检索")
    ok(len(store.search_messages("缓存雪崩")) == 1, "FTS5 trigram：长词命中")
    ok(len(store.search_messages("缓存 穿透")) == 1, "多词 AND 命中")
    ok(store.search_messages("绝不相干的词xyz") == [], "无命中返回空")
    ok(len(store.search_messages("布隆过滤器")) == 1, "命中内容可取")
    hit = store.search_messages("布隆过滤器")[0]
    ok(hit["convoId"] == "c1" and hit["convoTitle"] == "高并发面试", "检索结果带会话信息")
    ok(not any(ch in hit["snippet"] for ch in ("\x02", "\x03")), "snippet 已剥离高亮标记")
    ok(store.search_messages("") == [] and store.search_messages("   ") == [], "空查询返回空")

    # 短词回退：击穿 / 空值 都是 2 字，trigram 无法命中，走 LIKE
    store.save_convo({"id": "c6", "messages": [{"id": "s1", "role": "user", "content": "缓存击穿与空值处理"}]})
    ok(len(store.search_messages("击穿")) == 1, "2 字短词走 LIKE 回退命中")
    ok(len(store.search_messages("击穿 空值")) == 1, "多短词 LIKE AND 命中")

    section("覆盖重写后 FTS 同步")
    store.save_convo({"id": "c1", "title": "改标题", "messages": [{"id": "m1", "role": "user", "content": "完全不同的内容"}]})
    ok(store.search_messages("布隆过滤器") == [], "旧内容已从 FTS 清理")
    ok(len(store.search_messages("完全不同")) == 1, "新内容已进 FTS")
    ok(store.get_convo("c1")["title"] == "改标题", "标题被覆盖更新")

    # ---------------------------------------------------------------- 仓库
    section("仓库配置")
    repo = store.save_repo({
        "url": "https://github.com/ztangeeoo/work-agent", "provider": "github", "host": "github.com",
        "owner": "ztangeeoo", "name": "work-agent", "token": "t0ken", "intervalSec": 60, "autoPost": True,
    })
    rid = repo["id"]
    ok(repo["owner"] == "ztangeeoo" and repo["name"] == "work-agent", "仓库字段落库")
    ok(repo["hasToken"] is True, "hasToken=true")
    ok("token" not in repo, "token 不下发给前端")
    ok(store.get_repo(rid, with_token=True)["token"] == "t0ken", "with_token=True 可取到 token")
    ok(repo["intervalSec"] == 60 and repo["autoPost"] is True and repo["enabled"] is True, "间隔 / 自动回帖 / 启用")

    repo2 = store.save_repo({
        "url": "https://github.com/ztangeeoo/work-agent", "provider": "github", "host": "github.com",
        "owner": "ztangeeoo", "name": "work-agent", "intervalSec": 90,
    })
    ok(repo2["id"] == rid, "同地址重复添加 = 改配置（id 不变）")
    ok(repo2["hasToken"] is True, "token 留空串/未传时保留原值")
    ok(repo2["intervalSec"] == 90 and repo2["autoPost"] is False, "间隔被更新、auto_post 被重置")
    ok(store.save_repo({**repo2, "intervalSec": 5})["intervalSec"] == 30, "interval 下限 30s")
    ok(store.save_repo({**repo2, "intervalSec": 99999})["intervalSec"] == 3600, "interval 上限 3600s")
    ok(store.save_repo({**repo2, "token": ""})["hasToken"] is False, "显式传空串可清除 token")

    ok(len(store.list_repos()) == 1, "仓库列表可读")
    ok(store.list_repos("other") == [], "仓库用户隔离")
    ok(store.list_all_repos()[0].get("token") == "", "list_all_repos 含 token 字段（供轮询用）")
    ok(len(store.list_repos()) == 1 and store.list_repos()[0]["openPrs"] == 0, "仓库带 openPrs 统计")

    # 校验
    for bad in ({"url": "", "owner": "a", "name": "b"}, {"url": "u", "owner": "", "name": "b"}):
        try:
            store.save_repo(bad)
            ok(False, f"非法仓库应报错：{bad}")
        except ValueError:
            ok(True, "缺少地址 / owner 时报错")
    try:
        store.save_repo({**repo2, "id": "repo_not_exist"})
        ok(False, "指定不存在的 id 应报错")
    except ValueError:
        ok(True, "指定不存在的 id 时报错")

    # ---------------------------------------------------------------- PR
    section("PR 记录")
    store.upsert_pr(rid, {"number": 7, "title": "feat: test", "author": "bob", "url": "u7",
                          "baseBranch": "main", "headBranch": "feat", "headSha": "abc123",
                          "updatedAt": 1_700_000_000_000})
    store.upsert_pr(rid, {"number": 8, "title": "fix: y", "author": "eve", "headSha": "def456",
                          "updatedAt": 1_800_000_000_000})
    prs = store.list_prs(rid)
    ok(len(prs) == 2, "PR 列表 2 条")
    ok(prs[0]["number"] == 8, "PR 按更新时间倒序")
    ok(prs[0]["review"] is None, "未审 PR 的 review 为 None")
    ok(store.list_prs(rid, limit=1) and len(store.list_prs(rid, limit=1)) == 1, "limit 生效")
    ok(store.get_pr(rid, 7)["headSha"] == "abc123", "单 PR 读取")
    ok(store.get_pr(rid, 999) is None, "不存在的 PR 返回 None")

    # upsert 更新（轮询时平台总会带上 url）
    store.upsert_pr(rid, {"number": 7, "title": "feat: renamed", "url": "u7", "author": "bob",
                          "headSha": "abc999", "updatedAt": 1_900_000_000_000})
    ok(store.get_pr(rid, 7)["title"] == "feat: renamed" and store.get_pr(rid, 7)["headSha"] == "abc999", "PR upsert 覆盖更新")
    ok(store.get_pr(rid, 7)["draft"] is False, "draft 默认 False")

    # ---------------------------------------------------------------- 审核报告
    section("审核报告")
    rev = store.save_review({
        "repoId": rid, "prNumber": 7, "headSha": "abc999", "risk": "high",
        "decision": "request_changes", "summary": "有阻塞问题",
        "reportMd": "## 审核报告\n- 重试无上限",
        "issues": [{"severity": "blocker", "file": "a.js", "line": 3, "title": "无限递归"}],
        "stages": [{"stage": "scout", "label": "侦察官", "ms": 100}],
        "model": "mock-model",
    })
    ok(rev["id"].startswith("rev_"), "审核报告生成 id")
    ok(rev["risk"] == "high" and rev["decision"] == "request_changes", "风险 / 结论落库")
    ok(rev["issueCount"] == 1 and rev["issues"][0]["severity"] == "blocker", "问题清单往返一致")
    ok(len(rev["stages"]) == 1 and rev["stages"][0]["label"] == "侦察官", "流水线耗时往返一致")
    ok(rev["owner"] == "ztangeeoo" and rev["title"] == "feat: renamed", "报告带仓库与 PR 信息")
    ok(rev["prUrl"] == "u7", "报告带 PR 链接")
    ok(rev["posted"] is False, "默认未回帖")

    rev2 = store.save_review({
        "repoId": rid, "prNumber": 7, "headSha": "abc999", "risk": "low",
        "decision": "approve", "summary": "改好了", "reportMd": "# md2", "issues": [],
    })
    ok(rev2["id"] == rev["id"], "同 PR 同 head 只留一条（id 复用）")
    ok(rev2["risk"] == "low" and rev2["issueCount"] == 0, "重复保存覆盖为新结论")

    store.save_review({"repoId": rid, "prNumber": 8, "headSha": "def456", "risk": "medium",
                       "decision": "comment", "summary": "小问题", "issues": []})
    ok(len(store.list_reviews(repo_id=rid)) == 2, "同仓库 2 份报告")
    ok(len(store.list_reviews()) == 2, "跨仓库列表 2 份")

    ok(store.find_review(rid, 7, "abc999") is not None, "find_review 命中")
    ok(store.find_review(rid, 7, "zzz") is None, "find_review 不同 head 未命中")

    plist = store.list_prs(rid)
    hit7 = [p for p in plist if p["number"] == 7][0]
    ok(hit7["review"]["risk"] == "low", "PR 列表带当前 head 的审核结论")
    ok(hit7["review"]["issueCount"] == 0, "PR 列表带问题数")

    marked = store.mark_review_posted(rev["id"], posted=True)
    ok(marked["posted"] is True, "标记已回帖")
    failed = store.mark_review_posted(rev["id"], posted=False, error="403 无权限")
    ok(failed["posted"] is False and failed["postError"] == "403 无权限", "回帖失败原因落库")

    ok(store.get_review("nope") is None, "不存在的报告返回 None")
    for bad in ({"prNumber": 1}, {"repoId": "x"}):
        try:
            store.save_review(bad)
            ok(False, f"缺字段应报错：{bad}")
        except ValueError:
            ok(True, "缺少 repoId / prNumber 时报错")

    # ---------------------------------------------------------------- stats / 级联
    section("统计与级联删除")
    st = store.stats()
    ok(st["convos"] >= 5 and st["messages"] > 0, "stats 统计会话与消息")
    ok(st["repos"] == 1 and st["reviews"] == 2, "stats 统计仓库与审核报告")
    ok(st["path"].endswith("interview.db") and st["sizeKB"] >= 0, "stats 带库路径与体积")

    ok(store.delete_repo(rid) is True, "删除仓库成功")
    ok(store.list_reviews() == [], "级联删除审核报告")
    ok(store.list_prs(rid) == [], "级联删除 PR 记录")
    ok(store.get_repo(rid) is None, "仓库已不存在")

    # ---------------------------------------------------------------- 参数校验
    section("参数校验")
    try:
        store.save_convo({"id": ""})
        ok(False, "缺少会话 id 应报错")
    except ValueError:
        ok(True, "缺少会话 id 时报错")

    ok(store.available() is True, "store.available() 为真")

    # 库文件已落盘
    ok(os.path.exists(store.DB_PATH), "数据库文件已落盘")
    ok(os.path.exists(store.DB_PATH + "-wal") or os.path.exists(store.DB_PATH + "-shm")
       or os.path.getsize(store.DB_PATH) > 0, "WAL 模式相关文件存在或有内容")

    store.close()
    print(f"\n结果：{pass_n} 通过 / {fail_n} 失败\n")
    return 1 if fail_n else 0


if __name__ == "__main__":
    code = main()
    try:
        import shutil
        shutil.rmtree(_tmpdir, ignore_errors=True)
    except Exception:
        pass
    sys.exit(code)
