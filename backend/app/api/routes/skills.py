# -*- coding: utf-8 -*-
"""技能接口：/api/skills

技能本身是 backend/skills/<目录>/SKILL.md，不由用户上传，所以这里**只读**：
前端拿它渲染「每位专家绑定了哪些技能」，排障时也能一眼看出哪个 SKILL.md 写错了。

返回两部分：
- skills：全部技能的元数据（含未绑定 / 有问题的，enabled=false）
- assignments：按「全局 / 专家」分组后的有效技能，前端直接渲染
"""

from fastapi import APIRouter

from ... import config
from ...services import skills as skill_svc

router = APIRouter(tags=["skills"])


@router.get("/api/skills")
async def api_skills():
    return {
        "ok": True,
        "enabled": bool(config.SKILLS_ENABLED),
        "dir": str(config.SKILLS_DIR),
        "skills": skill_svc.catalog(),
        "assignments": skill_svc.assignments(),
    }
