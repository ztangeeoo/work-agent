/* 出题提示词构造（画像 → 系统提示 / 出题指令） */

import type { Profile } from '../types';
import { TRACK_SUBS } from '../data/tracks';

export function buildSystemPrompt(p: Profile): string {
  return `你是一位资深 IT 技术面试官。请根据候选人信息出面试题并点评。

候选人画像：
- 技术方向：${p.track}
- 细分方向：${p.sub === '随机' ? `随机（请在该方向的不同子领域间均衡分布，可参考：${(TRACK_SUBS[p.track] || []).join('、')}）` : p.sub}
- 级别：${p.level}
- 工作经验：${p.exp}
- 行业背景：${p.industry}
- 出题语言：${p.lang}
${p.resumeText ? `\n候选人简历摘要（出题时请针对简历中的技术栈和项目经历提问）：\n<<<\n${p.resumeText.slice(0, 3000)}\n>>>` : ''}

出题要求：
1. 题目难度必须匹配级别和经验（${p.level} / ${p.exp}）。
2. 如有简历，优先围绕简历中的技术栈、项目深挖提问（项目细节、技术选型原因、难点与方案）。
3. 涵盖：基础知识、场景设计、排查定位、深挖追问等类型，避免纯背书题。
4. 用 ${p.lang} 回答；使用 Markdown 排版。每道题必须单独成段，以「**第 N 题：一句标题**」开头（N 从 1 连续编号），题与题之间空一行。
5. 每道题后附上【考察点】一行说明。
6. ${p.industry === '不限'
    ? '行业不限：使用通用互联网业务场景即可。'
    : `题目需结合「${p.industry}」行业的真实业务场景与约束（如数据规模、合规要求、稳定性/实时性诉求），场景题要具体可信，避免脱离行业的教科书式提问。`}`;
}

/** 「确认并生成面试题」时发给智能体的首条用户消息 */
export function buildGeneratePrompt(p: Profile): string {
  const q = p.count;
  const subText = p.sub === '随机' ? '' : `（重点考察 ${p.sub}）`;
  const subRule = p.sub === '随机'
    ? `细分方向随机：请在 ${p.track} 的不同子领域间均衡分布题目。`
    : `细分方向固定为 ${p.sub}：请围绕该子领域出题，深度优先于广度。`;
  const indText = p.industry === '不限' ? '' : `，行业背景为${p.industry}`;
  let prompt: string;
  if (p.resumeText) {
    prompt = `请根据候选人简历，为${p.track}方向${subText}的${p.level}（${p.exp}经验${indText}）候选人出 ${q} 道针对性面试题。优先深挖简历中的项目与技术栈，题目需考察真实能力而非背书。${subRule}`;
  } else {
    prompt = `请为${p.track}方向${subText}的${p.level}（${p.exp}经验${indText}）候选人出 ${q} 道面试题。涵盖基础、场景、排查与深挖追问。${subRule}`;
  }
  if (p.note) prompt += `\n补充要求：${p.note}`;
  return prompt;
}
