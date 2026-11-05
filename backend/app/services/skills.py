# -*- coding: utf-8 -*-
"""技能（Skill）注册与归属

一个技能 = `backend/skills/<目录>/SKILL.md`（Anthropic Agent Skills 格式）。
AgentScope 2.0 的 Toolkit 已经能吃这套格式：初始化时收集 name/description 拼进
system prompt，运行时由内建的 `Skill` 查看器按名字读取 SKILL.md 全文。

这一层只做 Toolkit 不做的事：**决定某个 Agent 装载哪些技能**。

归属三选一（写在 SKILL.md 的 frontmatter 里）：

    scope: global                 → 所有角色都装载
    scope: expert + experts: [..] → 只给列出的专家（面试出题官 / 代码审核官）
    不写 scope                     → 不生效，catalog 里标「未绑定」

可选的 agents: [..] 在专家范围内再收窄到具体角色（planner / grader / scout …）。

为什么缺失 scope 就不生效：从市面上拖进来的技能是**外部内容**，默认让它常驻所有
Agent 的 system prompt 等于把未经审阅的指令塞进每一次调用。宁可显式声明，也不要
静默注入。UI 会把未绑定的列出来提醒，但不会装载。

环境变量见 config.py：SKILLS_ENABLED / SKILLS_DIR。
"""

import re
import threading
from pathlib import Path

from .. import config
from ..core import stages as stage_catalog

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_cache: dict = {"sig": None, "items": []}
_lock = threading.Lock()


# ---------------------------------------------------------------- frontmatter
def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        return v[1:-1]
    return v


def _parse_frontmatter(lines: list[str]) -> dict:
    """极简 YAML 解析：只支持 `key: value`、`key: [a, b]`、`key:` 后跟 `- item`。

    够用且零依赖——技能清单本来就不该塞复杂结构（复杂内容放正文或 references/）。
    """
    out: dict = {}
    key = ""
    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- ") and key:
            if not isinstance(out.get(key), list):
                out[key] = []
            out[key].append(_unquote(stripped[2:]))
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        v = v.strip()
        if not v:
            out[key] = []          # 待续的块列表；没续上就是空
        elif v.startswith("[") and v.endswith("]"):
            inner = v[1:-1].strip()
            out[key] = [_unquote(x) for x in inner.split(",") if x.strip()] if inner else []
        else:
            out[key] = _unquote(v)
    return out


def split_frontmatter(text: str) -> tuple[dict, str]:
    """把 SKILL.md 拆成 (元数据, 正文)。没有 frontmatter 时返回 ({}, 原文)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for i in range(1, len(lines)):
        if lines[i].strip() in ("---", "..."):
            return _parse_frontmatter(lines[1:i]), "\n".join(lines[i + 1:])
    return {}, text


def _as_list(v) -> list[str]:
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str) and v.strip():
        return [x.strip() for x in v.split(",") if x.strip()]
    return []


# ---------------------------------------------------------------- 扫描与归属
def _catalog_version() -> str:
    """专家 / 智能体表的版本戳；库没连上时返回空串（此时也不会走库）"""
    try:
        from .. import store
        if store.connected():
            return store.catalog_version()
    except Exception:
        pass
    return ""


def _experts() -> list[dict]:
    """「专家 → 角色」的归属映射

    权威数据最终是**插件清单**（backend/plugins/*/plugin.yaml）；库里的
    expert / agent 两张表是它的投影。当前处于过渡期：库里有数据就先用库
    （不改变已有部署的行为），库空着（新装 / 单测）则直接从插件清单现算。

    返回 [{"id", "mode", "label", "icon", "stages": [...]}]。
    """
    try:
        from .. import store
        # 用 connected() 而不是 available()：后者会顺手 init() 建库，
        # 读一次技能不该拉起一个数据库文件。
        if store.connected():
            rows = store.list_experts(with_agents=True)
            if rows:
                return [
                    {
                        "id": r["id"],
                        "mode": r["mode"],
                        "label": r["name"],
                        "icon": r.get("icon") or "",
                        "stages": [a["stage"] for a in r.get("agents", [])],
                    }
                    for r in rows
                ]
    except Exception:
        pass

    return [
        {
            "id": e["id"],
            "mode": e["mode"],
            "label": e["label"],
            "icon": e["icon"],
            "stages": list(e["stages"]),
        }
        for e in stage_catalog.experts()
    ]


def _experts_source() -> str:
    """归属映射这次是从库读的还是回退到常量（自检用）"""
    try:
        from .. import store
        if store.connected() and store.list_experts(with_agents=False):
            return "db"
    except Exception:
        pass
    return "fallback"


def _all_stages(experts: list[dict]) -> list[str]:
    """所有已知角色（按专家顺序去重）；库里一个都没有时回退插件清单

    全局技能的 stages 取自这里 —— 否则库里新增的角色永远吃不到全局技能。
    """
    out: list[str] = []
    for e in experts:
        for s in e["stages"]:
            if s not in out:
                out.append(s)
    return out or list(stage_catalog.stages().keys())


def _signature(root: Path) -> tuple:
    try:
        files = tuple(sorted((str(p), p.stat().st_mtime) for p in root.rglob("SKILL.md")))
    except OSError:
        return ()
    # 库里的专家 / 智能体变了也要重算：只改库不改 SKILL.md 时，归属同样会变
    return (files, _catalog_version())


def _load_one(skill_dir: Path, md: Path, experts: list[dict],
              all_stages: list[str]) -> dict | None:
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return None

    fm, body = split_frontmatter(text)
    folder = skill_dir.name
    name = str(fm.get("name") or folder).strip()
    desc = str(fm.get("description") or "").strip()
    scope_raw = str(fm.get("scope") or "").strip().lower()
    known_experts = {e["id"] for e in experts}
    known_stages = set(all_stages)
    experts_sel = [e for e in _as_list(fm.get("experts")) if e in known_experts]
    unknown_experts = [e for e in _as_list(fm.get("experts")) if e not in known_experts]
    agents = [a for a in _as_list(fm.get("agents")) if a in known_stages]
    unknown_agents = [a for a in _as_list(fm.get("agents")) if a not in known_stages]

    issues: list[str] = []
    if not name:
        issues.append("缺少 name")
    elif not _NAME_RE.match(name):
        issues.append("name 需为 kebab-case（小写字母 / 数字 / 连字符）")
    if not desc:
        issues.append("缺少 description（模型靠它判断何时该用这个技能）")
    if not scope_raw:
        issues.append("未声明归属：加 scope: global 或 scope: expert + experts")
    elif scope_raw not in ("global", "expert"):
        issues.append(f"scope 只能是 global / expert，收到 {scope_raw}")
    elif scope_raw == "expert" and not experts_sel:
        issues.append("scope=expert 但 experts 为空或不是已知专家")
    if unknown_experts:
        issues.append("未知专家：" + "、".join(unknown_experts))
    if unknown_agents:
        issues.append("未知角色：" + "、".join(unknown_agents))

    scope = scope_raw if scope_raw in ("global", "expert") else ""

    # 最终生效的角色列表（顺序跟 all_stages 走，保证展示稳定）
    if scope == "global":
        stages = list(all_stages)
    elif scope == "expert" and experts_sel:
        allowed = {s for e in experts if e["id"] in experts_sel for s in e["stages"]}
        stages = [s for s in all_stages if s in allowed]
        if agents:
            stages = [s for s in stages if s in agents]
    else:
        stages = []

    return {
        "id": folder,
        "name": name,
        # name 必须是 kebab-case（AgentScope 按它查找），界面展示用 title 更友好
        "title": str(fm.get("title") or "").strip(),
        "description": desc,
        "scope": scope or "unassigned",
        "experts": experts_sel,
        "agents": agents,
        "stages": stages,
        "enabled": not issues and bool(stages),
        "issues": issues,
        "path": str(skill_dir),
        "chars": len(body),
    }


def scan() -> list[dict]:
    """扫描技能目录（SKILL.md 或库里的专家/智能体一变就重算，不用重启）"""
    root = Path(config.SKILLS_DIR)
    if not config.SKILLS_ENABLED or not root.is_dir():
        return []

    sig = _signature(root)
    with _lock:
        if _cache["sig"] is not None and _cache["sig"] == sig:
            return list(_cache["items"])

    experts = _experts()
    all_stages = _all_stages(experts)

    items: list[dict] = []
    for md in sorted(root.rglob("SKILL.md")):
        item = _load_one(md.parent, md, experts, all_stages)
        if item:
            items.append(item)

    # 同名技能会在 AgentScope 里互相覆盖，提前标出来
    seen: dict[str, int] = {}
    for it in items:
        seen[it["name"]] = seen.get(it["name"], 0) + 1
    for it in items:
        if seen.get(it["name"], 0) > 1:
            it["issues"].append("名称重复，只会生效其中一个")
            it["enabled"] = False

    items.sort(key=lambda x: (x["scope"] != "global", x["id"]))
    with _lock:
        _cache["sig"] = sig
        _cache["items"] = items
    return list(items)


def reset_cache() -> None:
    """测试用：强制下次重新扫描"""
    with _lock:
        _cache["sig"] = None
        _cache["items"] = []


def for_stage(stage: str) -> list[str]:
    """某个角色该装载的技能目录（交给 Toolkit 的 skills_or_loaders）

    全局技能不看角色名，一律装载；专家技能按解析出的 stages 判断。
    传入空 stage 视为调用方漏传，一律不装载（避免把技能塞给来路不明的 Agent）。
    """
    if not stage:
        return []
    return [
        it["path"] for it in scan()
        if it["enabled"] and (it["scope"] == "global" or stage in it["stages"])
    ]


def catalog() -> list[dict]:
    """全部技能的元数据；不含正文，前端用来展示"""
    return [
        {
            "id": it["id"],
            "name": it["name"],
            "title": it["title"],
            "description": it["description"],
            "scope": it["scope"],
            "experts": it["experts"],
            "agents": it["agents"],
            "stages": it["stages"],
            "enabled": it["enabled"],
            "issues": it["issues"],
            "chars": it["chars"],
        }
        for it in scan()
    ]


def assignments() -> dict:
    """按「全局 / 各专家」分组，前端直接渲染，不用自己再算一遍"""
    items = scan()
    live = [it for it in items if it["enabled"]]

    def brief(it: dict) -> dict:
        return {
            "id": it["id"],
            "name": it["name"],
            "title": it["title"],
            "description": it["description"],
            "stages": it["stages"],
        }

    experts = []
    for e in _experts():
        bound = [it for it in live if it["scope"] == "expert" and e["id"] in it["experts"]]
        experts.append({
            "id": e["id"],
            # 前端专家对象按 mode 匹配（库里 expert.id 与 mode 一一对应，两个键都能对上）
            "mode": e["mode"],
            "label": e["label"],
            "icon": e["icon"],
            "stages": list(e["stages"]),
            "skills": [brief(it) for it in bound],
        })

    return {
        "global": [brief(it) for it in live if it["scope"] == "global"],
        "experts": experts,
        "unassigned": [
            {"id": it["id"], "name": it["name"], "issues": it["issues"]}
            for it in items
            if not it["enabled"]
        ],
    }


def status() -> dict:
    """自检信息，挂到 /api/db/stats 与 /health"""
    items = scan()
    experts = _experts()
    return {
        "enabled": bool(config.SKILLS_ENABLED),
        "dir": str(config.SKILLS_DIR),
        # 归属来自库还是常量，排障时一眼能看出用的是哪套
        "source": _experts_source(),
        "count": len(items),
        "active": len([it for it in items if it["enabled"]]),
        "byStage": {
            s: len([it for it in items if it["enabled"] and s in it["stages"]])
            for s in _all_stages(experts)
        },
    }
