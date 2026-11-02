/* 多会话持久化 + 模型配置（localStorage） */

import type { ChatMessage, Convo, ModelCfg, Profile } from '../types';
import { DEFAULT_PROFILE } from '../data/tracks';

export const LS_CFG = 'interviewer_cfg_v1';
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

/* ---- 模型配置 ---- */
export function loadCfg(): ModelCfg {
  try { return JSON.parse(localStorage.getItem(LS_CFG) || '{}'); } catch (_) { return {} as ModelCfg; }
}
export function saveCfg(cfg: ModelCfg): void {
  localStorage.setItem(LS_CFG, JSON.stringify(cfg));
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
