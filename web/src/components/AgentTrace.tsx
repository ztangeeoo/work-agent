import { useEffect, useState } from 'react';
import type { StageRow, TraceState } from '../lib/stages';

/** 各环节进行中的文案（后端会给 detail 时优先用 detail） */
const RUNNING_TEXT: Record<string, string> = {
  planner: '规划考察大纲…',
  interviewer: '出题中…',
  grader: '分析回答中…',
};

function fmtSec(ms: number): string {
  if (!ms || ms < 0) return '0.0s';
  return (ms / 1000).toFixed(1) + 's';
}

function stepText(r: StageRow): string {
  switch (r.status) {
    case 'running': return r.detail || RUNNING_TEXT[r.key] || '工作中…';
    case 'tool': return r.detail || '调用工具…';
    case 'done': return r.detail ? '完成 · ' + r.detail : '完成';
    case 'skipped': return '本次跳过';
    case 'error': return r.detail || '执行失败';
    default: return '待命';
  }
}

interface Props {
  trace: TraceState;
  /** 生成是否进行中，用于显示/隐藏 */
  active: boolean;
}

/**
 * 智能体执行流水线面板
 * 运行中自动展开、实时显示哪个智能体在干活；结束后折叠成一行摘要，点击可再次展开。
 */
export default function AgentTrace({ trace, active }: Props) {
  const { rows, finished, hasEvents, totalMs, startedAt } = trace;
  const [open, setOpen] = useState(true);
  const [now, setNow] = useState(Date.now());

  /* 运行中：每 100ms 刷新计时 */
  const running = active && !finished;
  /* 用户中途停止：有事件但流程没走完，且已不再 active */
  const aborted = hasEvents && !active && !finished;
  useEffect(() => {
    if (!running) return;
    const t = window.setInterval(() => setNow(Date.now()), 100);
    return () => window.clearInterval(t);
  }, [running]);

  /* 开始运行 → 展开；结束或被中止 → 稍等后自动折叠 */
  useEffect(() => {
    if (running) { setOpen(true); return; }
    if (finished || aborted) {
      const t = window.setTimeout(() => setOpen(false), 1800);
      return () => window.clearTimeout(t);
    }
  }, [running, finished, aborted]);

  if (!hasEvents) return null;

  const elapsed = startedAt ? Math.max(now - startedAt, 0) : totalMs;
  const doneCount = rows.filter(r => r.status === 'done').length;
  const skippedCount = rows.filter(r => r.status === 'skipped').length;
  const badge = finished
    ? `已完成 ${doneCount} 个环节${skippedCount ? ` · 跳过 ${skippedCount}` : ''} · 共 ${fmtSec(totalMs)}`
    : aborted
      ? `已中止${totalMs ? ' · 已用 ' + fmtSec(totalMs) : ''}`
      : (() => {
          const cur = rows.find(r => r.status === 'running' || r.status === 'tool');
          return cur ? `${cur.label}执行中 · ${fmtSec(elapsed)}` : '调度中…';
        })();

  return (
    <div className={'agent-trace' + (finished ? ' is-done' : running ? ' is-running' : ' is-idle') + (open ? ' is-open' : '')}>
      <button className="trace-head" onClick={() => setOpen(v => !v)} type="button">
        <span className={'trace-pulse' + (finished ? ' done' : '')}></span>
        <span className="trace-title">智能体协同</span>
        <span className="trace-badge">{badge}</span>
        <span className="spacer"></span>
        <span className={'trace-chevron' + (open ? ' up' : '')}>▾</span>
      </button>
      {open && (
        <div className="trace-body">
          {rows.map((r, i) => {
            // 被中止时，仍处于 running/tool 的环节降级成「未完成」
            const st = aborted && (r.status === 'running' || r.status === 'tool') ? 'idle' : r.status;
            return (
              <div key={r.key} className={'trace-step s-' + st}>
                <span className="trace-rail">
                  <span className="trace-dot">{i + 1}</span>
                  {i < rows.length - 1 && <span className="trace-line"></span>}
                </span>
                <span className="trace-name">{r.label}</span>
                <span className="trace-text">{aborted && st === 'idle' ? '未完成' : stepText(r)}</span>
                <span className="trace-ms">{r.ms !== undefined ? fmtSec(r.ms) : ''}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
