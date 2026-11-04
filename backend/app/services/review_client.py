# -*- coding: utf-8 -*-
"""
代码审核 · 代码托管平台适配层（Python / httpx 异步）

负责三件事（其余交给 app/agents/review.py 的三智能体审核流水线）：
  1. 解析用户填的「代码地址」→ provider / owner / name
  2. 拉取开放 PR（GitHub Pull Request / GitLab Merge Request / Gitee Pull Request）与 diff
  3. 把审核意见回帖到 PR（可选，默认关闭）

支持的平台（含私有部署）：
  github  api.github.com，自建走 /api/v3
  gitlab  /api/v4（含 lggit.xxx 这类自建实例）
  gitee   /api/v5（Gitee 企业版 / Gitee Premium 私有部署同样是 v5）

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

PROVIDERS = ("github", "gitlab", "gitee")


def host_hint(host: str) -> str:
    """按域名猜平台（猜不准没关系，自建实例后面会探测校准）。"""
    h = (host or "").lower()
    if "gitlab" in h:
        return "gitlab"
    if "gitee" in h or "git.oschina" in h:
        return "gitee"
    return "github"


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
    if provider not in PROVIDERS:
        provider = host_hint(host)

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


def auth_header(provider: str, token: str) -> dict:
    """各平台的鉴权头：GitLab 用 PRIVATE-TOKEN，Gitee 用 Authorization: token，GitHub 用 Bearer。"""
    if not token:
        return {}
    if provider == "gitlab":
        return {"PRIVATE-TOKEN": token}
    if provider == "gitee":
        return {"Authorization": "token " + token}
    return {"Authorization": "Bearer " + token}


async def api_json(url: str, token: str = "", headers: dict | None = None, method: str = "GET",
                   body=None, provider: str = "github"):
    h = {"Accept": "application/json", **(headers or {})}
    h.update(auth_header(provider, token))
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
                    "——GitLab 用 /api/v4，Gitee（含企业版）用 /api/v5，GitHub 企业版用 /api/v3）")
        elif res.status_code == 404:
            hint = "（仓库不存在、是私有仓库但未配 Token、Token 不属于该实例，或地址写错）"
        raise RuntimeError(f"平台接口返回 HTTP {res.status_code}{hint}：{msg}")
    try:
        return res.json()
    except Exception:
        return None


# ------------------------------------------------------------------ 平台适配

# 自建实例的平台探测缓存：host -> "gitlab" | "gitee" | "github"
_PROVIDER_CACHE: dict[str, str] = {}
_GITHUB_HOSTS = ("github.com", "www.github.com", "api.github.com")
_GITLAB_HOSTS = ("gitlab.com", "www.gitlab.com")
_GITEE_HOSTS = ("gitee.com", "www.gitee.com")

# 探测用：路径 -> 平台。GitLab 的 /api/v4 优先（Gitee 企业版同时提供 GitLab 兼容层，
# 两边都能用时走兼容层更稳，v5 只在没有 /api/v4 的纯 Gitee 实例上才用）
_PROBE_PATHS = (("/api/v4/user", "gitlab"), ("/api/v5/user", "gitee"))


async def _probe_json(host: str, path: str, provider: str, token: str) -> bool:
    """探一个路径，判断这个平台的接口是否真的可用。

    带 Token 时要求真的通过鉴权（2xx 且返回 JSON）—— 只看「有没有 JSON」会误判：
    Gitee 企业版的兼容网关对不存在的路径也回 JSON 401，会把实例判成另一家。
    没有 Token 时（公开仓库）只能退化成「返回 JSON 就算存在」。
    """
    h = {"Accept": "application/json", **auth_header(provider, token)}
    try:
        res = await request("https://" + host + path, headers=h, timeout=8.0)
    except Exception:
        return False
    ct = (res.headers.get("content-type") or "").lower()
    if "json" not in ct:
        return False
    if token:
        return 200 <= res.status_code < 300
    body = (res.text or "").lstrip()[:1]
    return body in ("{", "[")


async def detect_provider(host: str, token: str = "", fallback: str = "github") -> str:
    """
    判断实例是 GitLab / Gitee / GitHub 系（GitHub Enterprise）。

    判据是各自的鉴权接口能不能通过（带 Token 要求 2xx）：
      - GitLab：/api/v4/user；
      - Gitee（含 Gitee Premium 私有部署）：/api/v5/user —— 有些企业版同时提供
        GitLab 兼容层，那种实例两边都通，优先按 GitLab 处理（顺序见 _PROBE_PATHS）；
      - 其它（GHE 等）：都探不通 → 落到 fallback。
    只看域名里有没有 "gitlab"/"gitee" 会不稳，所以一律以探测结果为准。
    """
    host = (host or "").lower()
    if not host or host in _GITHUB_HOSTS:
        return "github"
    if host in _GITLAB_HOSTS:
        return "gitlab"
    if host in _GITEE_HOSTS:
        return "gitee"
    if host in _PROVIDER_CACHE:
        return _PROVIDER_CACHE[host]

    for path, provider in _PROBE_PATHS:
        if await _probe_json(host, path, provider, token):
            _PROVIDER_CACHE[host] = provider
            return provider

    # 探测都没命中（断网 / 反代改写）：不缓存，沿用调用方给的值
    return fallback or host_hint(host)


async def ensure_provider(repo: dict) -> dict:
    """自建实例自动校准 provider（就地改写 repo['provider']），返回同一个 dict。

    注意：每次都校准，不因为库里存着 "gitlab" 就直接信任 ——
    早期版本会把 Gitee 私有部署误判成 GitLab，靠这里才能自动纠正。
    """
    if not isinstance(repo, dict):
        return repo
    host = (repo.get("host") or "").lower()
    if not host:
        return repo
    repo["provider"] = await detect_provider(
        host, repo.get("token") or "", fallback=repo.get("provider") or host_hint(host)
    )
    return repo


def _repo_api_base(repo: dict) -> str:
    if repo.get("provider") == "gitlab":
        return "https://" + repo["host"] + "/api/v4"
    if repo.get("provider") == "gitee":
        return "https://" + repo["host"] + "/api/v5"
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


# 统一状态词 -> 各平台实际参数（GitLab 叫 opened，Gitee/GitHub 叫 open）
_STATE_MAP = {
    "gitlab": {"open": "opened", "closed": "closed", "merged": "merged", "all": "all"},
    "gitee": {"open": "open", "closed": "closed", "merged": "merged", "all": "all"},
    "github": {"open": "open", "closed": "closed", "merged": "closed", "all": "all"},
}


async def list_prs(repo: dict, state: str = "open", limit: int = 20,
                   max_age_days: int = 0) -> list:
    """列出 PR / MR（按更新时间倒序）。

    state: open / closed / merged / all —— 审核官追问时经常需要看已合并的 MR，
    所以这里比 list_open_prs 多一个状态维度。max_age_days=0 表示不限时间。
    """
    repo = await ensure_provider(repo)
    token = repo.get("token") or ""
    provider = repo.get("provider")
    st = _STATE_MAP.get(provider, _STATE_MAP["github"]).get(str(state or "open"), "open")
    out = []

    if provider == "gitlab":
        url = (_repo_api_base(repo) + "/projects/" + _gitlab_project_id(repo)
               + "/merge_requests?state=" + st
               + "&order_by=updated_at&sort=desc&per_page=" + str(limit))
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
    elif provider == "gitee":
        url = (_repo_api_base(repo) + "/repos/" + repo["owner"] + "/" + repo["name"]
               + "/pulls?state=" + st + "&sort=updated&direction=desc&per_page=" + str(limit))
        arr = await api_json(url, token=token, provider="gitee") or []
        if isinstance(arr, dict):   # 出错时可能是 {"message": ...}
            arr = []
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
                "updatedAt": _parse_ts(pr.get("updated_at") or pr.get("created_at")),
            })
    else:
        url = (_repo_api_base(repo) + "/repos/" + repo["owner"] + "/" + repo["name"]
               + "/pulls?state=" + st + "&sort=updated&direction=desc&per_page=" + str(limit))
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

    if max_age_days:
        cutoff = _now_ms() - max_age_days * 24 * 3600 * 1000
        out = [p for p in out if (p["updatedAt"] or 0) >= cutoff]
    return [p for p in out if p["number"]]


async def list_open_prs(repo: dict, limit: int = 20, max_age_days: int = 45) -> list:
    """列出开放的 PR / MR（轮询器用）"""
    return await list_prs(repo, "open", limit=limit, max_age_days=max_age_days)


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


def _looks_like_diff(text: str) -> bool:
    t = str(text or "")
    return "diff --git " in t or "\n@@" in t or t.lstrip().startswith("--- ")


async def fetch_pr_diff(repo: dict, number) -> str:
    """拉取某个 PR 的 unified diff 文本（超长截断，附提示）"""
    repo = await ensure_provider(repo)
    if repo.get("provider") == "gitee":
        base = _repo_api_base(repo)
        path = ("/repos/" + repo["owner"] + "/" + repo["name"]
                + "/pulls/" + str(number))
        token = repo.get("token") or ""
        res = await request(base + path + ".diff",
                            headers={**auth_header("gitee", token), "Accept": "text/plain"},
                            timeout=45.0)
        text = res.text or ""
        if 200 <= res.status_code < 300 and _looks_like_diff(text):
            return truncate_diff(text, text.count("\ndiff --git ") + (1 if text.startswith("diff --git ") else 0))
        if res.status_code in (401, 403):
            raise RuntimeError("拉取 PR diff 失败（HTTP " + str(res.status_code)
                               + "）：Token 无效或无权访问该仓库")
        # 部分版本没有 .diff 路由：退回 /files，用每个文件的 patch 拼 diff
        j = await api_json(base + path + "/files", token=token, provider="gitee")
        files = j if isinstance(j, list) else []
        parts = []
        for f in files:
            patch = f.get("patch") or f.get("diff") or ""
            if not str(patch).strip():
                continue
            fn = f.get("filename") or f.get("new_path") or ""
            parts.append("diff --git a/" + str(fn) + " b/" + str(fn) + "\n" + str(patch))
        return truncate_diff("\n".join(parts), len(parts))

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


async def fetch_pr_files(repo: dict, number) -> list:
    """列出某个 PR 的变更文件（路径 + 状态 + 该文件的 patch）"""
    repo = await ensure_provider(repo)
    token = repo.get("token") or ""
    provider = repo.get("provider")
    base = _repo_api_base(repo)

    if provider == "gitlab":
        url = (base + "/projects/" + _gitlab_project_id(repo)
               + "/merge_requests/" + str(number) + "/changes")
        j = await api_json(url, token=token, provider="gitlab") or {}
        out = []
        for c in (j.get("changes") or []):
            status = "modified"
            if c.get("new_file"):
                status = "added"
            elif c.get("deleted_file"):
                status = "deleted"
            elif c.get("renamed_file"):
                status = "renamed"
            out.append({
                "path": c.get("new_path") or c.get("old_path") or "",
                "oldPath": c.get("old_path") or "",
                "status": status,
                "diff": c.get("diff") or "",
            })
        return out

    if provider == "gitee":
        url = (base + "/repos/" + repo["owner"] + "/" + repo["name"]
               + "/pulls/" + str(number) + "/files")
        j = await api_json(url, token=token, provider="gitee")
        files = j if isinstance(j, list) else []
        return [{
            "path": f.get("filename") or "",
            "oldPath": f.get("previous_filename") or "",
            "status": f.get("status") or "modified",
            "diff": f.get("patch") or "",
        } for f in files]

    url = (base + "/repos/" + repo["owner"] + "/" + repo["name"]
           + "/pulls/" + str(number) + "/files")
    j = await api_json(url, token=token, headers={"Accept": "application/vnd.github+json"}) or []
    files = j if isinstance(j, list) else []
    return [{
        "path": f.get("filename") or "",
        "oldPath": f.get("previous_filename") or "",
        "status": f.get("status") or "modified",
        "diff": f.get("patch") or "",
    } for f in files]


_BRANCH_CACHE: dict[str, str] = {}


async def default_branch(repo: dict) -> str:
    """取仓库默认分支。

    GitLab 的 /repository/files/:path/raw 不带 ref 会直接 400，所以读文件前
    必须先知道默认分支；这里按仓库缓存一次，避免每读一个文件都查一遍。
    """
    key = (str(repo.get("host") or "") + "/" + str(repo.get("owner") or "")
           + "/" + str(repo.get("name") or ""))
    if key in _BRANCH_CACHE:
        return _BRANCH_CACHE[key]
    provider = repo.get("provider")
    token = repo.get("token") or ""
    base = _repo_api_base(repo)
    branch = ""
    try:
        if provider == "gitlab":
            j = await api_json(base + "/projects/" + _gitlab_project_id(repo),
                               token=token, provider="gitlab") or {}
        else:
            j = await api_json(base + "/repos/" + repo["owner"] + "/" + repo["name"],
                               token=token, provider=provider) or {}
        if isinstance(j, dict):
            branch = str(j.get("default_branch") or "")
    except Exception:
        branch = ""
    branch = branch or "main"
    _BRANCH_CACHE[key] = branch
    return branch


async def fetch_file_text(repo: dict, path: str, ref: str = "") -> str:
    """读取仓库里某个文件的原始内容（ref 留空则用默认分支）"""
    repo = await ensure_provider(repo)
    token = repo.get("token") or ""
    provider = repo.get("provider")
    base = _repo_api_base(repo)
    p = str(path or "").strip().lstrip("/")
    if not p:
        raise ValueError("缺少文件路径")
    ref = str(ref or "").strip() or await default_branch(repo)

    if provider == "gitlab":
        url = (base + "/projects/" + _gitlab_project_id(repo)
               + "/repository/files/" + quote(p, safe="") + "/raw")
        if ref:
            url += "?ref=" + quote(str(ref), safe="")
        headers = {**auth_header("gitlab", token), "Accept": "text/plain"}
    elif provider == "gitee":
        url = (base + "/repos/" + repo["owner"] + "/" + repo["name"]
               + "/raw/" + quote(p, safe="/"))
        if ref:
            url += "?ref=" + quote(str(ref), safe="")
        headers = {**auth_header("gitee", token), "Accept": "text/plain"}
    else:
        url = (base + "/repos/" + repo["owner"] + "/" + repo["name"]
               + "/contents/" + quote(p, safe="/"))
        if ref:
            url += "?ref=" + quote(str(ref), safe="")
        headers = {**_github_headers(token), "Accept": "application/vnd.github.raw"}

    res = await request(url, headers=headers, timeout=45.0)
    if res.status_code < 200 or res.status_code >= 300:
        ct = res.headers.get("content-type") or ""
        raise RuntimeError("读取文件失败（HTTP " + str(res.status_code) + "）："
                           + (_api_message(res.text, ct) or "路径不存在、分支写错或没有权限"))
    return res.text or ""


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


async def _post_gitee_comment(repo: dict, number, body: str) -> dict:
    """Gitee（含企业版，也含只实现了 v5 的 GitLab 兼容层）回帖：PR comments"""
    url = ("https://" + repo["host"] + "/api/v5/repos/"
           + repo["owner"] + "/" + repo["name"]
           + "/pulls/" + str(number) + "/comments")
    await api_json(url, token=repo.get("token") or "", provider="gitee",
                   method="POST", body={"body": body})
    return {"ok": True, "url": repo["url"] + "/pulls/" + str(number), "via": "gitee-v5"}


async def post_pr_comment(repo: dict, number, body: str) -> dict:
    """回帖：GitHub 用 issue comments，GitLab 用 notes，Gitee 用 PR comments。

    注意：Gitee 企业版的「GitLab 兼容层」只实现了 /changes、/commits 之类的读接口，
    `/merge_requests/:iid/notes` 直接 404（实测）。所以 gitlab 分支失败时按 404
    回退到 v5 的 pulls/comments，否则这类实例永远回帖失败。
    """
    repo = await ensure_provider(repo)
    token = repo.get("token") or ""

    if repo.get("provider") == "gitee":
        return await _post_gitee_comment(repo, number, body)

    if repo.get("provider") == "gitlab":
        url = (_repo_api_base(repo) + "/projects/" + _gitlab_project_id(repo)
               + "/merge_requests/" + str(number) + "/notes")
        try:
            await api_json(url, token=token, provider="gitlab", method="POST", body={"body": body})
            return {"ok": True, "url": repo["url"] + "/-/merge_requests/" + str(number), "via": "gitlab"}
        except RuntimeError as e:
            if "HTTP 404" not in str(e):
                raise
        return await _post_gitee_comment(repo, number, body)

    url = _repo_api_base(repo) + "/repos/" + repo["owner"] + "/" + repo["name"] + "/issues/" + str(number) + "/comments"
    j = await api_json(url, token=token, method="POST", body={"body": body})
    return {"ok": True, "url": ((j or {}).get("html_url")) or (repo["url"] + "/pull/" + str(number)), "via": "github"}


async def test_repo(repo: dict) -> dict:
    """测试连通性：验证地址、Token、能否读到 PR 数"""
    prs = await list_open_prs(repo, limit=10, max_age_days=3650)
    return {"ok": True, "openPrs": len(prs), "preview": prs[:3]}
