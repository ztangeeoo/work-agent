import type { PollScheduler, Repo } from '../types';
import PollPill from './PollPill';

interface Props {
  repos: Repo[];
  repoId: string;
  onPick: (id: string) => void;
  /** SSE 推送「开始轮询」时的即时状态（服务端状态刷新前先用它点亮指示灯） */
  polling: boolean;
  /** 调度器状态：没运行说明自动轮询不会发生 */
  scheduler?: PollScheduler | null;
  onTogglePolling?: () => void;
  toggleBusy?: boolean;
  prCount: number;
  lastError: string;
}

/**
 * 审核模式的状态信息（并进对话头部一行）：仓库选择 / 轮询状态 / 开放 PR 数。
 * 按钮（立即轮询 / 仓库配置 / 清空对话）由头部统一渲染，避免两行重复。
 */
export default function ReviewStatus({
  repos, repoId, onPick, polling, scheduler, onTogglePolling, toggleBusy, prCount, lastError,
}: Props) {
  const repo = repos.find(r => r.id === repoId) || null;

  return (
    <div className="review-status">
      {repos.length ? (
        <select className="rb-select" value={repoId} onChange={(e) => onPick(e.target.value)} title="切换仓库">
          {repos.map(r => (
            <option key={r.id} value={r.id}>{r.owner}/{r.name}</option>
          ))}
        </select>
      ) : (
        <span className="rb-empty">还没有配置代码仓库</span>
      )}

      {repo && (
        <PollPill
          repo={repo}
          scheduler={scheduler}
          live={polling}
          onToggle={onTogglePolling}
          busy={toggleBusy}
        />
      )}

      {repo && <span className="rb-count">{prCount} 个开放 PR</span>}
      {repo && !repo.hasToken && <span className="rb-warn">未配 Token</span>}
      {repo && lastError && <span className="rb-warn" title={lastError}>⚠️ 上次出错</span>}
    </div>
  );
}
