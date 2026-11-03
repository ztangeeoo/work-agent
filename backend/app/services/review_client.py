# -*- coding: utf-8 -*-
"""
代码审核 · 代码托管平台适配层（Python / httpx 异步）

负责三件事（其余交给 app/agents/review.py 的三智能体审核流水线）：
  1. 解析用户填的「代码地址」→ provider / owner / name
  2. 拉取开放 PR（GitHub Pull Request / GitLab Merge Request）与 diff
  3. 把审核意见回帖到 PR（可选，默认关闭）

网络说明：本机若有 HTTP(S)_PROXY（公司代理 / 科学上网），https 目标
会通过代理 CONNECT 隧道访问；localhost 与「代理自身地址」直连。
"""

import os
import re
import json
from urllib.parse import urlparse, unquote, quote

import httpx

UA = "interview-agent-code-reviewer/1.0"
MAX_DIFF_CHARS = 60000
MAX_REVIEWS_PER_POLL = 3  # 单次轮询最多审几个，避免一次刷爆模型额度


# ------------------------------------------------------------------ 地址解析

_SCP_RE = re.compile(r"^git@([^:]+):(.+)$")
_OWNER_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


def parse_repo_url(raw_input, provider_override=None) -> dict:
    """
    解析代码地址。支持：
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      git@github.com:owner/repo.git
      owner/repo                      （默认 GitHub）
      https://gitlab.com/group/sub/repo
    """
    raw = str(raw_input or "").strip()
    if not raw:
        raise ValueError("请填写代码仓库地址")

    m = _SCP_RE.match(raw)
    if m:
        raw = "https://" + m.group(1) + "/" + m.group(2)

    if not re.match(r"^https?://", raw, re.I):
        if _OWNER_REPO_RE.match(raw):
            raw = "https://github.com/" + raw
        else:
            raw = "https://" + raw

    try:
        u = urlparse(raw)
    except Exception:
        raise ValueError("仓库地址无法解析：" + str(raw_input))
    if not u.hostname:
        raise ValueError("仓库地址无法解析：" + str(raw_input))

    segs = [s for s in re.split(r"[/]+", u.path) if s]
    segs = [unquote(s) for s in segs]
    if segs and re.search(r"\.git$", segs[-1], re.I):
        segs[-1] = re.sub(r"\.git$", "", segs[-1], flags=re.I)
    # GitLab 网页地址可能带 /-/tree/main 之类后缀
    if "-" in segs:
        dash = segs.index("-")
        if dash > 0:
            segs = segs[:dash]

    if len(segs) < 2:
        raise ValueError("地址里没有仓库名，应形如 https://github.com/owner/repo")

    name = segs.pop()
    owner = "/".join(segs)
    host = (u.hostname or "").lower()

    provider = str(provider_override or "").lower()
    if provider not in ("github", "gitlab"):
        provider = "gitlab" if "gitlab" in host else "github"

    netloc = u.netloc
    return {
        "provider": provider,
        "host": host,
        "owner": owner,
        "name": name,
        "url": "https://" + netloc + "/" + owner + "/" + name,
    }


# ------------------------------------------------------------------ HTTP 请求

def _is_local_host(host: str) -> bool:
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _proxy_for(url: str) -> str | None:
    """按 reviewer.js 的规则挑代理：localhost 与代理自身地址直连。"""
    try:
        u = urlparse(url)
    except Exception:
        return None
    if _is_local_host(u.hostname or ""):
        return None
    if u.scheme == "https":
        raw = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
               or os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))
    else:
        raw = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
    if not raw:
        return None
    try:
        p = urlparse(raw)
    except Exception:
        return None
    if not p.hostname:
        return None
    if p.hostname == u.hostname:
        return None
    return raw


async def request(url: str, method: str = "GET", headers: dict | None = None,
                  body=None, timeout: float = 30.0, redirects: int = 3) -> httpx.Response:
    """通用请求：自动跟随若干次重定向（对齐 reviewer.js 的 request）。"""
    h = {"User-Agent": UA, **(headers or {})}
    content = None
    if body is not None:
        content = body if isinstance(body, (str, bytes)) else json.dumps(body, ensure_ascii=False).encode("utf-8")
        if not any(k.lower() == "content-type" for k in h):
            h["Content-Type"] = "application/json"

    proxy = _proxy_for(url)
    kwargs = {"timeout": timeout, "follow_redirects": True, "max_redirects": redirects}
    if proxy:
        kwargs["proxy"] = proxy
    else:
        # 不走代理时避免 httpx 自行读取环境变量
        kwargs["trust_env"] = False

    async with httpx.AsyncClient(**kwargs) as client:
        return await client.request(method, url, headers=h, content=content)


def _html_title(text: str) -> str:
    """从 HTML 错误页里取出 <title>，避免把整页 HTML 塞进报错。"""
    m = re.search(r"<title[^>]*>(.*?)</title>", text or "", re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def _is_html(text: str, content_type: str = "") -> bool:
    if "html" in (content_type or "").lower():
        return True
    t = (text or "").lstrip()[:200].lower()
    return t.startswith("<!doctype html") or t.startswith("<html") or "<html" in t


def _api_message(text: str, content_type: str = "") -> str:
    try:
        j = json.loads(text)
        return j.get("message") or j.get("error_description") or (j.get("error") or {}).get("message") or ""
    except Exception:
        pass
    if _is_html(text, content_type):
        title = _html_title(text)
        return "网页错误页：" + title if title else "接口返回了网页而不是 JSON"
    return ""


async def api_json(url: str, token: str = "", headers: dict | None = None, method: str = "GET",
                   body=None, provider: str = "github"):
    h = {"Accept": "application/json", **(headers or {})}
    if token:
        if provider == "gitlab":
            h["PRIVATE-TOKEN"] = token
        else:
            h["Authorization"] = "Bearer " + token
    if provider == "github":
        h["X-GitHub-Api-Version"] = "2022-11-28"
    res = await request(url, method=method, headers=h, body=body)
    if res.status_code < 200 or res.status_code >= 300:
        ct = res.headers.get("content-type") or ""
        html = _is_html(res.text, ct)
        msg = _api_message(res.text, ct) or res.text[:200]
        hint = ""
        if res.status_code in (401, 403):
            hint = "（Token 无效、权限不足或触发限流）"
        elif res.status_code == 404 and html:
            hint = ("（接口路径不存在，不是仓库问题：自建实例请在「平台」里选对类型"
                    "——GitLab 用 /api/v4，Gitea 等用 /api/v3）")
        elif res.status_code == 404:
            hint = "（仓库不存在、是私有仓库但未配 Token，或地址写错）"
        raise RuntimeError(f"平台接口返回 HTTP {res.status_code}{hint}：{msg}")
    try:
        return res.json()
    except Exception:
        return None


# ------------------------------------------------------------------ 平台适配

# 自建实例的平台探测缓存：host -> "gitlab" | "github"
_PROVIDER_CACHE: dict[str, str] = {}
_GITHUB_HOSTS = ("github.com", "www.github.com", "api.github.com")


async def detect_provider(host: str, token: str = "") -> str:
    """
    判断自建实例是 GitLab 还是 GitHub 系（GitHub / Gitea）。

    判据是 /api/v4/projects 有没有 JSON 响应：
      - GitLab：有该路由，200（公开）或 401（未授权）都返回 JSON；
      - Gitea / 其它：该路径不存在，返回自家的 HTML 404 页面。
    只看域名里有没有 "gitlab" 会漏掉 lggitee.xxx 这类自建实例，所以改用探测。
    """
    host = (host or "").lower()
    if not host or host in _GITHUB_HOSTS:
        return "github"
    if host in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[host]

    provider = "github"
    try:
        h = {"Accept": "application/json"}
        if token:
            h["PRIVATE-TOKEN"] = token
        res = await request("https://" + host + "/api/v4/projects?per_page=1",
                            headers=h, timeout=8.0)
        ct = (res.headers.get("content-type") or "").lower()
        if "json" in ct or res.status_code in (200, 401, 403):
            provider = "gitlab"
    except Exception:
        # 探测失败（断网/代理不通）时不缓存，沿用调用方传进来的判断
        return _PROVIDER_CACHE.get(host, provider)

    _PROVIDER_CACHE[host] = provider
    return provider


async def ensure_provider(repo: dict) -> dict:
    """自建实例自动校准 provider（就地改写 repo['provider']），返回同一个 dict。"""
    if not isinstance(repo, dict):
        return repo
    host = (repo.get("host") or "").lower()
    if not host or host in _GITHUB_HOSTS:
        repo["provider"] = "github"
        return repo
    if repo.get("provider") == "gitlab":
        return repo          # 已明确是 GitLab，不必探测
    repo["provider"] = await detect_provider(host, repo.get("token") or "")
    return repo


def _repo_api_base(repo: dict) -> str:
    if repo.get("provider") == "gitlab":
        return "https://" + repo["host"] + "/api/v4"
    if repo.get("host") == "github.com":
        return "https://api.github.com"
    return "https://" + repo["host"] + "/api/v3"


def _gitlab_project_id(repo: dict) -> str:
    return quote(repo["owner"] + "/" + repo["name"], safe="")


def _github_headers(token: str) -> dict:
    h = {"Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = "Bearer " + token
    h["X-GitHub-Api-Version"] = "2022-11-28"
    return h


async def list_open_prs(repo: dict, limit: int = 20, max_age_days: int = 45) -> list:
    """列出开放的 PR / MR（按更新时间倒序）"""
    repo = await ensure_provider(repo)
    token = repo.get("token") or ""
    out = []

    if repo.get("provider") == "gitlab":
        url = (_repo_api_base(repo) + "/projects/" + _gitlab_project_id(repo)
               + "/merge_requests?state=opened&order_by=updated_at&sort=desc&per_page=" + str(limit))
        arr = await api_json(url, token=token, provider="gitlab") or []
        for mr in arr:
            out.append({
                "number": mr.get("iid"),
                "title": mr.get("title") or "",
                "author": ((mr.get("author") or {}).get("username")) or "",
                "url": mr.get("web_url") or "",
                "baseBranch": mr.get("target_branch") or "",
                "headBranch": mr.get("source_branch") or "",
                "headSha": mr.get("sha") or "",
                "state": "open" if mr.get("state") == "opened" else (mr.get("state") or "open"),
                "draft": bool(mr.get("draft") or mr.get("work_in_progress")),
                "updatedAt": _parse_ts(mr.get("updated_at")),
            })
    else:
        url = (_repo_api_base(repo) + "/repos/" + repo["owner"] + "/" + repo["name"]
               + "/pulls?state=open&sort=updated&direction=desc&per_page=" + str(limit))
        arr = await api_json(url, token=token, headers={"Accept": "application/vnd.github+json"}) or []
        for pr in arr:
            out.append({
                "number": pr.get("number"),
                "title": pr.get("title") or "",
                "author": ((pr.get("user") or {}).get("login")) or "",
                "url": pr.get("html_url") or "",
                "baseBranch": ((pr.get("base") or {}).get("ref")) or "",
                "headBranch": ((pr.get("head") or {}).get("ref")) or "",
                "headSha": ((pr.get("head") or {}).get("sha")) or "",
                "state": pr.get("state") or "open",
                "draft": bool(pr.get("draft")),
                "updatedAt": _parse_ts(pr.get("updated_at")),
            })

    cutoff = _now_ms() - max_age_days * 24 * 3600 * 1000
    return [p for p in out if p["number"] and (p["updatedAt"] or 0) >= cutoff]


def _parse_ts(s) -> int:
    """把 ISO8601 字符串转毫秒时间戳（对齐 JS 的 Date.parse）。"""
    if not s:
        return _now_ms()
    try:
        from datetime import datetime, timezone
        t = str(s).replace("Z", "+00:00")
        # 去掉超过 6 位的微秒
        t = re.sub(r"(\.\d{6})\d+", r"\1", t)
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return _now_ms()


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


async def fetch_pr_diff(repo: dict, number) -> str:
    """拉取某个 PR 的 unified diff 文本（超长截断，附提示）"""
    repo = await ensure_provider(repo)
    if repo.get("provider") == "gitlab":
        url = (_repo_api_base(repo) + "/projects/" + _gitlab_project_id(repo)
               + "/merge_requests/" + str(number) + "/changes")
        j = await api_json(url, token=repo.get("token") or "", provider="gitlab")
        changes = ((j or {}).get("changes")) or []
        parts = []
        for c in changes:
            src = c.get("old_path") or c.get("new_path")
            dst = c.get("new_path") or c.get("old_path")
            parts.append("diff --git a/" + str(src) + " b/" + str(dst) + "\n" + (c.get("diff") or "(二进制或空改动)"))
        return truncate_diff("\n".join(parts), len(changes))

    url = _repo_api_base(repo) + "/repos/" + repo["owner"] + "/" + repo["name"] + "/pulls/" + str(number)
    res = await request(url, headers={**_github_headers(repo.get("token") or ""),
                                      "Accept": "application/vnd.github.v3.diff"}, timeout=45.0)
    ok = 200 <= res.status_code < 300
    if not ok and res.status_code != 406:
        raise RuntimeError(f"拉取 PR diff 失败（HTTP {res.status_code}）："
                           + (_api_message(res.text) or res.text[:160]))
    return truncate_diff(res.text, None)


def diff_stats(diff: str) -> dict:
    """统计 diff 的文件数与增删行数（供报告页脚展示）。"""
    files = 0
    additions = 0
    deletions = 0
    for line in str(diff or "").split("\n"):
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1
    return {"files": files, "additions": additions, "deletions": deletions}


def truncate_diff(text, file_count=None) -> str:
    t = str(text or "")
    if not t.strip():
        return ""
    if len(t) <= MAX_DIFF_CHARS:
        return t
    head = t[:MAX_DIFF_CHARS]
    note = ("\n\n...（diff 过长已截断，原始 " + str(len(t)) + " 字符"
            + (f"，共 {file_count} 个文件" if file_count else "")
            + "；审核结论仅基于截断前的内容）")
    return head + note


async def post_pr_comment(repo: dict, number, body: str) -> dict:
    """回帖：GitHub 用 issue comments，GitLab 用 notes"""
    repo = await ensure_provider(repo)
    if repo.get("provider") == "gitlab":
        url = (_repo_api_base(repo) + "/projects/" + _gitlab_project_id(repo)
               + "/merge_requests/" + str(number) + "/notes")
        await api_json(url, token=repo.get("token") or "", provider="gitlab", method="POST", body={"body": body})
        return {"ok": True, "url": repo["url"] + "/-/merge_requests/" + str(number)}
    url = _repo_api_base(repo) + "/repos/" + repo["owner"] + "/" + repo["name"] + "/issues/" + str(number) + "/comments"
    j = await api_json(url, token=repo.get("token") or "", method="POST", body={"body": body})
    return {"ok": True, "url": ((j or {}).get("html_url")) or (repo["url"] + "/pull/" + str(number))}


async def test_repo(repo: dict) -> dict:
    """测试连通性：验证地址、Token、能否读到 PR 数"""
    prs = await list_open_prs(repo, limit=10, max_age_days=3650)
    return {"ok": True, "openPrs": len(prs), "preview": prs[:3]}
