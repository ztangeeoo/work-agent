import { useEffect, useRef, useState } from 'react';
import {
  isTraceFinished, reduceStageEvents, sumStageMs,
  type StageEvent, type StageMode, type StageRow,
} from './stageReducer';

export type { StageEvent, StageRow, StageMode, StageStatus } from './stageReducer';
export { STAGE_ORDER, REVIEW_STAGE_ORDER, stageOrderFor, reduceStageEvents } from './stageReducer';

export interface TraceState {
  rows: StageRow[];
  /** 所有环节都已结束（done/skipped/error） */
  finished: boolean;
  /** 各环节耗时之和 */
  totalMs: number;
  /** 是否至少收到过一个事件 */
  hasEvents: boolean;
  /** 第一个事件的时间戳，用于实时计时 */
  startedAt: number;
}

/**
 * 订阅某次运行的智能体阶段事件。
 * @param runId 每次发送消息前由前端生成，并随模型配置同步到服务端
 * @param enabled 是否保持订阅（生成结束后关掉）
 */
export function useAgentStages(runId: string, enabled: boolean, mode: StageMode = 'interview'): TraceState {
  const [events, setEvents] = useState<StageEvent[]>([]);
  const esRef = useRef<EventSource | null>(null);
  const seenRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!enabled || !runId) {
      esRef.current?.close();
      esRef.current = null;
      return;
    }
    // 新一轮开始，清空旧状态
    if (!seenRef.current.has('__init__' + runId)) {
      seenRef.current.add('__init__' + runId);
      setEvents([]);
    }

    const es = new EventSource('/api/stages?runId=' + encodeURIComponent(runId));
    esRef.current = es;
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as StageEvent;
        if (ev && ev.stage) setEvents(prev => [...prev, ev]);
      } catch (_) { /* 心跳等非 JSON 行忽略 */ }
    };
    es.onerror = () => { /* 断线时浏览器自动重连，无需处理 */ };

    return () => {
      es.close();
      if (esRef.current === es) esRef.current = null;
    };
  }, [runId, enabled]);

  const rows = reduceStageEvents(events, mode);
  const hasEvents = events.length > 0;
  const finished = hasEvents && isTraceFinished(rows);
  const totalMs = sumStageMs(rows);
  const startedAt = events.length ? events[0].ts : 0;

  return { rows, finished, totalMs, hasEvents, startedAt };
}

export function stageSummaryIcon(rows: StageRow[]): string {
  if (rows.some(r => r.status === 'error')) return '⚠️';
  if (rows.some(r => r.status === 'running' || r.status === 'tool')) return '◐';
  return '✓';
}
