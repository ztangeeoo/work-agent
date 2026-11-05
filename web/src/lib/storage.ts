/* 多会话持久化（localStorage）
 *
 * 注意：模型配置**不在这里**。base/key/model 是全局配置，唯一来源是服务端
 * （setting 表 + Node 内存），页面只读取显示；本地留副本只会让旧配置
 * 反过来覆盖服务端（曾经因此把好好的 key 换成了无效 key，审核全 401）。
 */

import type { ChatMessage, Convo, Profile } from '../types';
import { DEFAULT_PROFILE } from '../data/tracks';

export const LS_CONVOS = 'interviewer_convos_v1';

export function genId(): string {
  return 'c_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
}
export function fmtTime(ts?: number): string {
  const diff = Date.now() - (ts || Date.now());
  if (diff < 60e3) return '刚刚';
  if (diff < 3600e3) return Math.floor(diff / 60e3) + ' 分钟前';
  if (diff < 86400e3) return Math.floor(diff / 3600e3) + ' 小时前';
  const d = new Date(ts || Date.now());
  return (d.getMonth() + 1) + '/' + d.getDate();
}
export function titleFromMessages(msgs: ChatMessage[]): string {
  const u = (msgs || []).find(m => m.role === 'user');
  if (!u) return '新会话';
  const t = String(u.content).replace(/\s+/g, ' ').trim();
  return t.length > 24 ? t.slice(0, 24) + '…' : t;
}

/* ---- 会话列表 ---- */
export function loadConvos(): { activeId: string; list: Convo[] } {
  try {
    const raw = localStorage.getItem(LS_CONVOS);
    if (raw) {
      const d = JSON.parse(raw);
      return { activeId: d.activeId || '', list: Array.isArray(d.list) ? d.list : [] };
    }
  } catch (_) {}
  return { activeId: '', list: [] };
}
export function persistConvos(d: { activeId: string; list: Convo[] }): void {
  try { localStorage.setItem(LS_CONVOS, JSON.stringify(d)); } catch (_) {}
}

export function snapshotProfile(p: Profile): Profile {
  return {
    track: p.track, sub: p.sub, level: p.level, exp: p.exp,
    industry: p.industry, count: p.count, lang: p.lang, note: p.note || '',
    resumeName: p.resumeName || '',
    resumeText: (p.resumeText || '').slice(0, 8000),
  };
}

export function newProfile(): Profile {
  return { ...DEFAULT_PROFILE };
}

/**
 * 空白画像：从「专家中心」新建任务时的初始表单。
 * 这不是「已保存的配置」，所有选项都得由用户自己填，所以不能带任何默认值
 * （只有出题语言的默认项，避免下拉框显示空白）。
 */
export function blankProfile(): Profile {
  return {
    track: '', sub: '', level: '', exp: '', industry: '',
    count: 0, lang: '中文', note: '', resumeText: '', resumeName: '',
  };
}
