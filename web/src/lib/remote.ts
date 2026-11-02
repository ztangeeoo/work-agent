/**
 * 与服务端 SQLite 的同步层。
 *
 * 策略：
 *  - 浏览器 localStorage 降级为「离线缓存」，服务端 SQLite 是主存储
 *  - 写入去抖 1.2s 合并，避免流式输出时每条 token 都打一次请求
 *  - 服务端不可用时静默失败，前端继续用本地缓存（不阻断使用）
 */

import type { Convo } from '../types';

async function req<T = any>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...((init && init.headers) || {}) },
  });
  const text = await r.text();
  let data: any = null;
  try { data = text ? JSON.parse(text) : null; } catch (_) { data = null; }
  if (!r.ok) {
    const msg = (data && data.error && data.error.message) || `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return data as T;
}

/** 拉取服务端全部会话；服务端不可用或未启用 DB 时返回 null（调用方转纯本地模式） */
export async function fetchRemoteConvos(): Promise<Convo[] | null> {
  try {
    const d = await req<{ dbAvailable?: boolean; list?: Convo[] }>('/api/convos');
    if (!d || d.dbAvailable === false) return null;
    return Array.isArray(d.list) ? d.list : [];
  } catch (_) {
    return null;
  }
}

/* ---------- 写入去抖 ---------- */
const pending = new Map<string, Convo>();
let timer = 0;

async function flush(): Promise<void> {
  timer = 0;
  if (!pending.size) return;
  const batch = [...pending.values()];
  pending.clear();
  for (const c of batch) {
    try {
      await req(`/api/convos/${encodeURIComponent(c.id)}`, {
        method: 'POST',
        body: JSON.stringify({
          id: c.id, title: c.title, profile: c.profile,
          messages: c.messages, createdAt: c.createdAt, updatedAt: c.updatedAt,
        }),
      });
    } catch (_) {
      /* 静默：localStorage 仍持有最新副本，下次启动会重新回推 */
    }
  }
}

/** 排队保存（1.2s 内的多次变更合并成一次） */
export function queueSaveConvo(c: Convo): void {
  if (!c || !c.id) return;
  pending.set(c.id, c);
  if (timer) return;
  timer = window.setTimeout(() => { void flush(); }, 1200);
}

/** 立即落库（切换会话 / 页面隐藏时调用，避免丢最后一次变更） */
export function flushNow(): void {
  if (timer) { window.clearTimeout(timer); timer = 0; }
  void flush();
}

export async function deleteRemoteConvo(id: string): Promise<void> {
  pending.delete(id);
  try {
    await req(`/api/convos/${encodeURIComponent(id)}`, { method: 'DELETE' });
  } catch (_) {}
}

/* ---------- 跨会话检索 ---------- */
export interface SearchHit {
  id: string;
  convoId: string;
  convoTitle: string;
  role: string;
  snippet: string;
  createdAt: number;
}

export async function searchMessages(q: string): Promise<SearchHit[]> {
  const query = (q || '').trim();
  if (!query) return [];
  try {
    const d = await req<{ results?: SearchHit[] }>('/api/search?q=' + encodeURIComponent(query));
    return Array.isArray(d.results) ? d.results : [];
  } catch (_) {
    return [];
  }
}

/** 服务端历史库状态（用于界面提示） */
export async function fetchDbStats(): Promise<{ dbAvailable: boolean; convos?: number; messages?: number; sizeKB?: number } | null> {
  try {
    return await req('/api/db/stats');
  } catch (_) {
    return null;
  }
}
