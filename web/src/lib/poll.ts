/* 轮询状态展示：把仓库的实时状态翻译成「一句话 + 一盏灯」 */

import { useEffect, useState } from 'react';
import type { Repo } from '../types';

export type PollPhase = 'none' | 'paused' | 'listing' | 'reviewing' | 'error' | 'waiting';

export interface PollView {
  phase: PollPhase;
  /** 主状态：轮询中 / 审核 PR #12 中 / 已暂停 … */
  label: string;
  /** 补充说明：下一轮倒计时 / 间隔 */
  detail: string;
  /** 指示灯：live=呼吸灯（正在跑）、idle=常亮（开着但空闲）、warn=告警、off=关 */
  dot: 'live' | 'idle' | 'warn' | 'off';
}

/** 30 秒 / 2 分钟 / 1 小时 */
export function fmtInterval(sec: number): string {
  const s = Math.max(1, Math.round(Number(sec) || 0));
  if (s < 60) return `${s} 秒`;
  if (s < 3600) return `${Math.round(s / 60)} 分钟`;
  return `${Math.round(s / 3600)} 小时`;
}

/** 倒计时 mm:ss；已到期返回「马上开始」 */
export function fmtCountdown(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return '马上开始';
  const total = Math.ceil(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

/**
 * 仓库当前的轮询状态。
 * 判断顺序很重要：先看「开关」，再看「这一刻在不在跑」，最后才是「下一轮什么时候」——
 * 否则暂停的仓库会一直显示倒计时，看起来像在轮询。
 */
export function pollViewOf(repo: Repo | null | undefined, now: number): PollView {
  if (!repo) {
    return { phase: 'none', label: '还没有仓库', detail: '填好代码地址才会开始轮询', dot: 'off' };
  }
  const every = `每 ${fmtInterval(repo.intervalSec)}`;

  if (!repo.enabled) {
    return { phase: 'paused', label: '自动轮询已暂停', detail: `${every} · 打开开关即恢复`, dot: 'off' };
  }

  if (repo.polling) {
    const reviewing = repo.phase === 'reviewing' && repo.reviewingPr != null;
    const ran = repo.startedAt ? Math.max(1, Math.round((now - repo.startedAt) / 1000)) : 0;
    return {
      phase: reviewing ? 'reviewing' : 'listing',
      label: reviewing ? `审核 PR #${repo.reviewingPr} 中` : '正在拉取 PR 列表',
      detail: [every, ran ? `本轮已跑 ${ran}s` : ''].filter(Boolean).join(' · '),
      dot: 'live',
    };
  }

  if (repo.lastError) {
    const err = String(repo.lastError);
    /* 最常见的不是「轮询失败」，而是「轮询成功但没配模型、审核直接跳过」，
       两者在界面上必须分开说，否则用户会以为轮询挂了 */
    const noModel = err.includes('未配置模型');
    const next = repo.nextPollAt ? `下一轮 ${fmtCountdown(repo.nextPollAt - now)}` : '';
    if (noModel) {
      return {
        phase: 'error',
        label: '只轮询 · 未配置模型',
        detail: [every + ' 拉 PR', next || '配好模型才会自动审核'].filter(Boolean).join(' · '),
        dot: 'warn',
      };
    }
    return {
      phase: 'error',
      label: '最近一轮出错',
      detail: [every + ' 会重试', next].filter(Boolean).join(' · '),
      dot: 'warn',
    };
  }

  if (!repo.lastPollAt) {
    return { phase: 'waiting', label: '已开启，等待首轮', detail: every, dot: 'idle' };
  }

  return {
    phase: 'waiting',
    label: '等待下一轮',
    detail: `${repo.nextPollAt ? fmtCountdown(repo.nextPollAt - now) : '马上开始'} · ${every}`,
    dot: 'idle',
  };
}

/** 左侧会话列表用的短标签（不跑倒计时，列表里够用） */
export function pollShort(repo: Repo | null | undefined): PollView | null {
  if (!repo) return null;
  if (!repo.enabled) return { phase: 'paused', label: '轮询暂停', detail: '', dot: 'off' };
  if (repo.polling) {
    const reviewing = repo.phase === 'reviewing' && repo.reviewingPr != null;
    return {
      phase: reviewing ? 'reviewing' : 'listing',
      label: reviewing ? `审核 #${repo.reviewingPr}` : '轮询中',
      detail: '',
      dot: 'live',
    };
  }
  if (repo.lastError) return {
    phase: 'error',
    label: String(repo.lastError).includes('未配置模型') ? '未配模型' : '轮询出错',
    detail: '',
    dot: 'warn',
  };
  return { phase: 'waiting', label: `每 ${fmtInterval(repo.intervalSec)}`, detail: '', dot: 'idle' };
}

/** 每秒跳一次的时钟：倒计时要动起来 */
export function useNow(stepMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), stepMs);
    return () => window.clearInterval(id);
  }, [stepMs]);
  return now;
}
