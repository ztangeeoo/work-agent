/* 智能体阶段事件的纯函数归并逻辑（无 React 依赖，便于单测） */

export type StageStatus = 'idle' | 'running' | 'tool' | 'done' | 'skipped' | 'error';

export interface StageEvent {
  stage: string;
  label: string;
  icon: string;
  status: StageStatus;
  ts: number;
  ms?: number;
  detail?: string;
}

export interface StageRow {
  key: string;
  label: string;
  icon: string;
  status: StageStatus;
  ms?: number;
  detail?: string;
}

export type StageMode = 'interview' | 'review';

/** 面试模式三环节（后端只发实际执行的，未出现的按待命处理） */
export const STAGE_ORDER: Array<{ key: string; label: string; icon: string }> = [
  { key: 'planner', label: '策划官', icon: '🧭' },
  { key: 'interviewer', label: '面试官', icon: '🎯' },
  { key: 'grader', label: '评分官', icon: '🧑‍⚖️' },
];

/** 审核模式：自动审核的侦察官/审核官/裁决官在后台跑，聊天里只展示答疑环节 */
export const REVIEW_STAGE_ORDER: Array<{ key: string; label: string; icon: string }> = [
  { key: 'advisor', label: '审核答疑', icon: '💬' },
];

export function stageOrderFor(mode: StageMode = 'interview') {
  return mode === 'review' ? REVIEW_STAGE_ORDER : STAGE_ORDER;
}

export function emptyRows(mode: StageMode = 'interview'): StageRow[] {
  return stageOrderFor(mode).map(o => ({ ...o, status: 'idle' as StageStatus }));
}

/** 把事件流合并成每个环节的最新状态 */
export function reduceStageEvents(events: StageEvent[], mode: StageMode = 'interview'): StageRow[] {
  const map = new Map<string, StageRow>();
  for (const o of stageOrderFor(mode)) map.set(o.key, { ...o, status: 'idle' });
  for (const ev of events) {
    if (!ev || !ev.stage) continue;
    const prev = map.get(ev.stage);
    map.set(ev.stage, {
      key: ev.stage,
      label: ev.label || prev?.label || ev.stage,
      icon: ev.icon || prev?.icon || '🤖',
      status: ev.status,
      ms: ev.ms !== undefined ? ev.ms : prev?.ms,
      detail: ev.detail || prev?.detail,
    });
  }
  return [...map.values()];
}

/** 所有环节是否都已结束 */
export function isTraceFinished(rows: StageRow[]): boolean {
  return rows.every(r => r.status === 'done' || r.status === 'skipped' || r.status === 'error');
}

export function sumStageMs(rows: StageRow[]): number {
  return rows.reduce((n, r) => n + (r.ms || 0), 0);
}
