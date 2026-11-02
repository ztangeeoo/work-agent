/* 代码审核前端接口层：仓库配置 / PR 列表 / 审核报告 / 事件订阅 */

import { useEffect, useRef } from 'react';
import type { PRItem, Repo, ReviewEvent, ReviewItem } from '../types';

async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
  });
  let json: any = null;
  try { json = await res.json(); } catch (_) { /* 空响应 */ }
  if (!res.ok) {
    const msg = json?.error?.message || json?.message || ('请求失败（HTTP ' + res.status + '）');
    throw new Error(msg);
  }
  return json as T;
}

/* ---------- 仓库 ---------- */

export async function fetchRepos(): Promise<Repo[]> {
  const j = await api<{ list?: Repo[] }>('/api/repos');
  return j.list || [];
}

export interface RepoInput {
  id?: string;
  url: string;
  provider?: string;
  token?: string;
  intervalSec?: number;
  autoPost?: boolean;
  enabled?: boolean;
}

export async function saveRepo(input: RepoInput): Promise<Repo> {
  const j = await api<{ repo: Repo }>('/api/repos', { method: 'POST', body: JSON.stringify(input) });
  return j.repo;
}

export async function removeRepo(id: string): Promise<void> {
  await api('/api/repos/' + encodeURIComponent(id), { method: 'DELETE' });
}

/** 测试地址 / Token 是否可用 */
export async function testRepo(id: string): Promise<{ openPrs: number }> {
  return api<{ openPrs: number }>('/api/repos/' + encodeURIComponent(id) + '/test', { method: 'POST' });
}

/** 立即轮询（异步，结果通过事件推送） */
export async function pollRepo(id: string): Promise<void> {
  await api('/api/repos/' + encodeURIComponent(id) + '/poll', { method: 'POST' });
}

export async function fetchPRs(repoId: string): Promise<PRItem[]> {
  const j = await api<{ list?: PRItem[] }>('/api/repos/' + encodeURIComponent(repoId) + '/prs');
  return j.list || [];
}

export async function reviewPR(repoId: string, number: number): Promise<ReviewItem> {
  const j = await api<{ review: ReviewItem }>(
    '/api/repos/' + encodeURIComponent(repoId) + '/prs/' + number + '/review',
    { method: 'POST' },
  );
  return j.review;
}

/* ---------- 审核报告 ---------- */

export async function fetchReviews(repoId?: string, limit = 20): Promise<ReviewItem[]> {
  const qs = new URLSearchParams();
  if (repoId) qs.set('repoId', repoId);
  qs.set('limit', String(limit));
  const j = await api<{ list?: ReviewItem[] }>('/api/reviews?' + qs.toString());
  return j.list || [];
}

/** 把审核意见回帖到 PR */
export async function postReviewToPR(id: string): Promise<{ url: string; review: ReviewItem }> {
  return api('/api/reviews/' + encodeURIComponent(id) + '/post', { method: 'POST' });
}

/* ---------- 事件订阅 ---------- */

/**
 * 订阅轮询 / 审核事件。回调放 ref 里，避免因为函数身份变化反复重连 SSE。
 */
export function useReviewEvents(enabled: boolean, onEvent: (ev: ReviewEvent) => void) {
  const cbRef = useRef(onEvent);
  cbRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return;
    const es = new EventSource('/api/review/events');
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as ReviewEvent;
        if (ev && ev.type) cbRef.current(ev);
      } catch (_) { /* 心跳 */ }
    };
    es.onerror = () => { /* 浏览器自动重连 */ };
    return () => es.close();
  }, [enabled]);
}

/* ---------- 展示辅助 ---------- */

export const RISK_META: Record<string, { label: string; cls: string; icon: string }> = {
  high: { label: '风险高', cls: 'r-high', icon: '🔴' },
  medium: { label: '风险中', cls: 'r-medium', icon: '🟠' },
  low: { label: '风险低', cls: 'r-low', icon: '🟢' },
};

export const DECISION_META: Record<string, { label: string; cls: string }> = {
  request_changes: { label: '打回修改', cls: 'd-block' },
  comment: { label: '建议修改', cls: 'd-comment' },
  approve: { label: '可以合入', cls: 'd-approve' },
};

export function riskMeta(risk?: string) {
  return RISK_META[String(risk || 'low')] || RISK_META.low;
}

export function decisionMeta(decision?: string) {
  return DECISION_META[String(decision || 'comment')] || DECISION_META.comment;
}

/** 把 PR 列表排一下：待审的在前，其次按风险从高到低 */
const RISK_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 };

export function sortPRs(list: PRItem[]): PRItem[] {
  return [...list].sort((a, b) => {
    const ra = a.review ? (RISK_ORDER[a.review.risk] ?? 3) : -1;
    const rb = b.review ? (RISK_ORDER[b.review.risk] ?? 3) : -1;
    if (ra !== rb) return ra - rb;
    return (b.updatedAt || 0) - (a.updatedAt || 0);
  });
}

export function fmtAgo(ts?: number): string {
  if (!ts) return '尚未轮询';
  const diff = Date.now() - ts;
  if (diff < 60 * 1000) return '刚刚';
  if (diff < 3600 * 1000) return Math.floor(diff / 60000) + ' 分钟前';
  if (diff < 86400 * 1000) return Math.floor(diff / 3600000) + ' 小时前';
  return Math.floor(diff / 86400000) + ' 天前';
}

/** 审核流水线耗时概览：🔍 侦察官 2.1s → 🛡️ 审核官 8.4s → ⚖️ 裁决官 3.0s */
export function stagesLine(review?: ReviewItem | null): string {
  if (!review || !review.stages?.length) return '';
  return review.stages
    .map(s => `${s.icon || '🤖'} ${s.label || s.stage}${s.ms ? ' ' + (s.ms / 1000).toFixed(1) + 's' : ''}`)
    .join(' → ');
}
