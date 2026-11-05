# -*- coding: utf-8 -*-
"""持久层 · 代码审核：仓库配置 / PR 记录 / 审核报告"""

import json
import uuid

from .db import DEFAULT_USER, init, lock, now


def _map_repo(r, with_token: bool = False) -> dict | None:
    if not r:
        return None
    out = {
        "id": r["id"],
        "url": r["url"],
        "provider": r["provider"],
        "host": r["host"] or "",
        "owner": r["owner"],
        "name": r["name"],
        "intervalSec": r["interval_sec"],
        "autoPost": bool(r["auto_post"]),
        "enabled": bool(r["enabled"]),
        "lastPollAt": r["last_poll_at"] or 0,
        "lastError": r["last_error"] or "",
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
        # token 一律不下发给前端，只告诉前端「有没有配」
        "hasToken": bool(r["token"]),
    }
    if with_token:
        out["token"] = r["token"] or ""
    keys = r.keys()
    if "open_prs" in keys:
        out["openPrs"] = r["open_prs"]
    if "review_count" in keys:
        out["reviewCount"] = r["review_count"]
    return out


def list_repos(user: str = DEFAULT_USER) -> list:
    """仓库列表（默认不含 token）"""
    db = init()
    with lock:
        rows = db.execute(
            """SELECT r.*,
                      (SELECT COUNT(*) FROM pr p WHERE p.repo_id = r.id AND p.state = 'open') AS open_prs,
                      (SELECT COUNT(*) FROM review v WHERE v.repo_id = r.id) AS review_count
                 FROM repo r WHERE r.user_key = ? ORDER BY r.created_at ASC""",
            (user,),
        ).fetchall()
    return [_map_repo(r) for r in rows]


def get_repo(rid: str, user: str = DEFAULT_USER, with_token: bool = True) -> dict | None:
    db = init()
    with lock:
        r = db.execute("SELECT * FROM repo WHERE id = ? AND user_key = ?", (str(rid or ""), user)).fetchone()
    return _map_repo(r, with_token) if r else None


def get_repo_by_id(rid: str, with_token: bool = True) -> dict | None:
    """轮询器用：按 id 取仓库，不限定用户"""
    db = init()
    with lock:
        r = db.execute("SELECT * FROM repo WHERE id = ?", (str(rid or ""),)).fetchone()
    return _map_repo(r, with_token) if r else None


def list_all_repos() -> list:
    """轮询器用：所有仓库（含 token）"""
    db = init()
    with lock:
        rows = db.execute("SELECT * FROM repo ORDER BY created_at ASC").fetchall()
    return [_map_repo(r, True) for r in rows]


def save_repo(data: dict, user: str = DEFAULT_USER) -> dict:
    """
    新建 / 更新仓库配置。
      - 同一用户同一地址只保留一条（重复添加等于改配置）
      - token 传 None 表示「不改」，传空串表示「清除」
    """
    db = init()
    ts = now()
    url = str(data.get("url") or "").strip()
    if not url:
        raise ValueError("缺少仓库地址")
    if not data.get("owner") or not data.get("name"):
        raise ValueError("仓库地址解析失败（需要形如 https://github.com/owner/repo）")

    rid = str(data.get("id") or "").strip()
    with lock:
        if not rid:
            exist = db.execute("SELECT id FROM repo WHERE user_key = ? AND url = ?", (user, url)).fetchone()
            rid = exist["id"] if exist else "repo_" + uuid.uuid4().hex[:12]
        old = db.execute("SELECT token, url FROM repo WHERE id = ? AND user_key = ?", (rid, user)).fetchone()
        if (not old) and data.get("id"):
            raise ValueError("仓库不存在：" + rid)

        # 换了仓库地址：旧 PR 与审核报告属于另一个仓库，留着只会张冠李戴
        if old and str(old["url"] or "").strip() != url:
            db.execute("DELETE FROM pr WHERE repo_id = ?", (rid,))
            db.execute("DELETE FROM review WHERE repo_id = ?", (rid,))

        if data.get("token") is None:
            token = (old["token"] if old else "") or ""
        else:
            token = str(data.get("token") or "").strip()

        try:
            interval = min(max(int(data.get("intervalSec") or 120), 30), 3600)
        except Exception:
            interval = 120
        provider = str(data.get("provider") or "github").lower()
        if provider not in ("github", "gitlab", "gitee"):
            provider = "github"
        enabled = 1 if data.get("enabled") is None else (1 if data.get("enabled") else 0)

        db.execute(
            """INSERT INTO repo (id, user_key, url, provider, host, owner, name, token,
                                 interval_sec, auto_post, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                    url = excluded.url, provider = excluded.provider, host = excluded.host,
                    owner = excluded.owner, name = excluded.name, token = excluded.token,
                    interval_sec = excluded.interval_sec, auto_post = excluded.auto_post,
                    enabled = excluded.enabled, updated_at = excluded.updated_at""",
            (
                rid, user, url, provider, str(data.get("host") or ""), str(data.get("owner")), str(data.get("name")),
                token, interval, 1 if data.get("autoPost") else 0, enabled, ts, ts,
            ),
        )
        db.commit()
    return get_repo(rid, user, with_token=False)


def delete_repo(rid: str, user: str = DEFAULT_USER) -> bool:
    db = init()
    with lock:
        cur = db.execute("DELETE FROM repo WHERE id = ? AND user_key = ?", (str(rid or ""), user))
        db.commit()
        return cur.rowcount > 0


def set_repo_provider(rid: str, provider: str) -> bool:
    """把平台探测结果写回仓库（自建 GitLab 被误判成 GitHub 时自动纠正）。"""
    provider = str(provider or "").strip()
    if not provider:
        return False
    db = init()
    with lock:
        cur = db.execute("UPDATE repo SET provider = ?, updated_at = ? WHERE id = ? AND provider != ?",
                         (provider, now(), str(rid), provider))
        db.commit()
        return cur.rowcount > 0


def set_repo_enabled(rid: str, enabled: bool, user: str = DEFAULT_USER) -> dict | None:
    """暂停 / 恢复自动轮询（只改开关，不碰地址与 Token）"""
    db = init()
    with lock:
        cur = db.execute(
            "UPDATE repo SET enabled = ?, updated_at = ? WHERE id = ? AND user_key = ?",
            (1 if enabled else 0, now(), str(rid), user),
        )
        db.commit()
        if not cur.rowcount:
            return None
    return get_repo(rid, user, with_token=False)


def touch_repo(rid: str, at: int | None = None, error: str = ""):
    """记录一次轮询结果（时间戳 / 错误信息）"""
    db = init()
    with lock:
        db.execute(
            "UPDATE repo SET last_poll_at = ?, last_error = ? WHERE id = ?",
            (now() if at is None else int(at), str(error or ""), str(rid)),
        )
        db.commit()


def clear_repo_errors(marker: str) -> int:
    """清掉 last_error 里含 marker 的仓库错误（返回清了几条）

    典型用法：模型配置刚补好，「未配置模型」这条历史错误立刻就不成立了。
    不主动清的话，得等每个仓库轮到下一轮轮询（间隔可能几分钟）才会消失，
    期间左侧会话列表一直挂着「未配模型」角标，看起来像没配成功。
    """
    marker = str(marker or "")
    if not marker:
        return 0
    db = init()
    with lock:
        cur = db.execute(
            "UPDATE repo SET last_error = '' WHERE last_error LIKE ?",
            ("%" + marker + "%",),
        )
        db.commit()
        return cur.rowcount


def upsert_pr(repo_id: str, pr: dict) -> dict | None:
    """写入 / 更新一条 PR（按 head_sha 判断是否有新提交）"""
    db = init()
    ts = now()
    with lock:
        db.execute(
            """INSERT INTO pr (repo_id, number, title, author, url, base_branch, head_branch,
                               head_sha, state, draft, pr_updated_at, seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(repo_id, number) DO UPDATE SET
                    title = excluded.title, author = excluded.author, url = excluded.url,
                    base_branch = excluded.base_branch, head_branch = excluded.head_branch,
                    head_sha = excluded.head_sha, state = excluded.state, draft = excluded.draft,
                    pr_updated_at = excluded.pr_updated_at, seen_at = excluded.seen_at""",
            (
                str(repo_id), int(pr.get("number") or 0), str(pr.get("title") or ""), str(pr.get("author") or ""),
                str(pr.get("url") or ""), str(pr.get("baseBranch") or ""), str(pr.get("headBranch") or ""),
                str(pr.get("headSha") or ""), str(pr.get("state") or "open"), 1 if pr.get("draft") else 0,
                int(pr.get("updatedAt") or ts), ts,
            ),
        )
        db.commit()
    return get_pr(repo_id, pr.get("number"))


def list_prs(repo_id: str, limit: int = 20, include_closed: bool = False) -> list:
    """PR 列表：附带「当前 head 是否已审」的结论"""
    db = init()
    where = "" if include_closed else "AND p.state = 'open'"
    with lock:
        rows = db.execute(
            f"""SELECT p.*, v.id AS review_id, v.risk AS review_risk, v.decision AS review_decision,
                       v.issue_count, v.created_at AS reviewed_at
                  FROM pr p
                  LEFT JOIN review v
                         ON v.repo_id = p.repo_id AND v.pr_number = p.number AND v.head_sha = p.head_sha
                 WHERE p.repo_id = ? {where}
                 ORDER BY p.pr_updated_at DESC LIMIT ?""",
            (str(repo_id), min(int(limit or 20), 100)),
        ).fetchall()
    return [
        {
            "repoId": r["repo_id"],
            "number": r["number"],
            "title": r["title"],
            "author": r["author"],
            "url": r["url"],
            "baseBranch": r["base_branch"],
            "headBranch": r["head_branch"],
            "headSha": r["head_sha"],
            "state": r["state"],
            "draft": bool(r["draft"]),
            "updatedAt": r["pr_updated_at"],
            "seenAt": r["seen_at"],
            "review": (
                {
                    "id": r["review_id"], "risk": r["review_risk"], "decision": r["review_decision"],
                    "issueCount": r["issue_count"], "createdAt": r["reviewed_at"],
                }
                if r["review_id"] else None
            ),
        }
        for r in rows
    ]


def get_pr(repo_id: str, number) -> dict | None:
    db = init()
    with lock:
        r = db.execute(
            "SELECT * FROM pr WHERE repo_id = ? AND number = ?",
            (str(repo_id), int(number or 0)),
        ).fetchone()
    if not r:
        return None
    return {
        "repoId": r["repo_id"], "number": r["number"], "title": r["title"], "author": r["author"], "url": r["url"],
        "baseBranch": r["base_branch"], "headBranch": r["head_branch"], "headSha": r["head_sha"],
        "state": r["state"], "draft": bool(r["draft"]), "updatedAt": r["pr_updated_at"], "seenAt": r["seen_at"],
    }


def _map_review(r) -> dict | None:
    if not r:
        return None
    try:
        issues = json.loads(r["issues_json"]) if r["issues_json"] else []
    except Exception:
        issues = []
    try:
        stages = json.loads(r["stages_json"]) if r["stages_json"] else []
    except Exception:
        stages = []
    keys = r.keys()
    return {
        "id": r["id"],
        "repoId": r["repo_id"],
        "repoUrl": (r["repo_url"] if "repo_url" in keys else "") or "",
        "owner": (r["owner"] if "owner" in keys else "") or "",
        "name": (r["name"] if "name" in keys else "") or "",
        "prNumber": r["pr_number"],
        "headSha": r["head_sha"],
        "title": (r["title"] if "title" in keys else "") or "",
        "author": (r["author"] if "author" in keys else "") or "",
        "prUrl": (r["pr_url"] if "pr_url" in keys else "") or "",
        "risk": r["risk"] or "low",
        "decision": r["decision"] or "comment",
        "summary": r["summary"] or "",
        "reportMd": r["report_md"] or "",
        "issues": issues,
        "issueCount": r["issue_count"],
        "stages": stages,
        "model": r["model"] or "",
        "posted": bool(r["posted"]),
        "postError": r["post_error"] or "",
        "createdAt": r["created_at"],
        "updatedAt": r["updated_at"],
    }


def save_review(data: dict) -> dict | None:
    """保存审核报告：同一 PR 同一 head 只留一条（重复轮询不刷屏）"""
    db = init()
    repo_id = str(data.get("repoId") or "")
    pr_number = int(data.get("prNumber") or 0)
    head_sha = str(data.get("headSha") or "")
    if not repo_id or not pr_number:
        raise ValueError("缺少 repoId / prNumber")

    with lock:
        exist = db.execute(
            "SELECT id FROM review WHERE repo_id = ? AND pr_number = ? AND head_sha = ?",
            (repo_id, pr_number, head_sha),
        ).fetchone()
        rid = exist["id"] if exist else (data.get("id") or "rev_" + uuid.uuid4().hex[:12])
        ts = now()
        issues = data.get("issues") or []
        db.execute(
            """INSERT INTO review (id, repo_id, pr_number, head_sha, risk, decision, summary, report_md,
                                   issues_json, issue_count, stages_json, model, posted, post_error,
                                   created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                    risk = excluded.risk, decision = excluded.decision, summary = excluded.summary,
                    report_md = excluded.report_md, issues_json = excluded.issues_json,
                    issue_count = excluded.issue_count, stages_json = excluded.stages_json,
                    model = excluded.model, updated_at = excluded.updated_at""",
            (
                rid, repo_id, pr_number, head_sha,
                str(data.get("risk") or "low"), str(data.get("decision") or "comment"),
                str(data.get("summary") or ""), str(data.get("reportMd") or ""),
                json.dumps(issues, ensure_ascii=False), int(data.get("issueCount") or len(issues)),
                json.dumps(data.get("stages") or [], ensure_ascii=False), str(data.get("model") or ""),
                1 if data.get("posted") else 0, str(data.get("postError") or ""),
                ts, ts,
            ),
        )
        db.commit()
    return get_review(rid)


_REVIEW_SELECT = """SELECT v.*, r.url AS repo_url, r.owner, r.name, p.title, p.author, p.url AS pr_url
                      FROM review v
                      JOIN repo r ON r.id = v.repo_id
                      LEFT JOIN pr p ON p.repo_id = v.repo_id AND p.number = v.pr_number"""


def get_review(rid: str) -> dict | None:
    db = init()
    with lock:
        r = db.execute(_REVIEW_SELECT + " WHERE v.id = ?", (str(rid or ""),)).fetchone()
    return _map_review(r)


def find_review(repo_id: str, pr_number, head_sha: str) -> dict | None:
    db = init()
    with lock:
        r = db.execute(
            "SELECT id FROM review WHERE repo_id = ? AND pr_number = ? AND head_sha = ?",
            (str(repo_id), int(pr_number or 0), str(head_sha or "")),
        ).fetchone()
    return get_review(r["id"]) if r else None


def list_reviews(user: str = DEFAULT_USER, repo_id: str = "", limit: int = 20) -> list:
    """审核报告列表（跨仓库，供审核工作台展示）"""
    db = init()
    lim = min(int(limit or 20), 100)
    with lock:
        if repo_id:
            rows = db.execute(
                _REVIEW_SELECT + " WHERE r.user_key = ? AND v.repo_id = ? ORDER BY v.created_at DESC LIMIT ?",
                (user, str(repo_id), lim),
            ).fetchall()
        else:
            rows = db.execute(
                _REVIEW_SELECT + " WHERE r.user_key = ? ORDER BY v.created_at DESC LIMIT ?",
                (user, lim),
            ).fetchall()
    return [_map_review(r) for r in rows]


def mark_review_posted(rid: str, posted: bool = True, error: str = "") -> dict | None:
    db = init()
    with lock:
        db.execute(
            "UPDATE review SET posted = ?, post_error = ?, updated_at = ? WHERE id = ?",
            (1 if posted else 0, str(error or ""), now(), str(rid)),
        )
        db.commit()
    return get_review(rid)
