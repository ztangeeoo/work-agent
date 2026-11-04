/**
 * 技能（Skill）读取层。
 *
 * 技能是磁盘上的 SKILL.md（backend/skills/），前端只读、不编辑：
 * 这里拿到的是「全部技能 + 按专家分好的绑定关系」，用于在专家卡片上展示。
 * 服务端不可用时返回 null，卡片自动隐藏技能区，不影响选专家。
 */

import type { SkillsPayload } from '../types';

export async function fetchSkills(): Promise<SkillsPayload | null> {
  try {
    const r = await fetch('/api/skills', { headers: { 'Content-Type': 'application/json' } });
    if (!r.ok) return null;
    const d = await r.json() as SkillsPayload;
    if (!d || !d.assignments) return null;
    return d;
  } catch (_) {
    return null;
  }
}

/** 把角色 id 翻成中文标签（和智能体协同面板保持一致） */
export const STAGE_LABELS: Record<string, string> = {
  planner: '策划官',
  interviewer: '面试官',
  grader: '评分官',
  scout: '侦察官',
  reviewer: '审核官',
  judge: '裁决官',
  advisor: '审核答疑',
};

export function stageNames(stages: string[]): string {
  return (stages || []).map(s => STAGE_LABELS[s] || s).join('、');
}
