import type { Repo } from '../types';
import { fmtAgo } from '../lib/review';

interface Props {
  repos: Repo[];
  repoId: string;
  onPick: (id: string) => void;
  polling: boolean;
  prCount: number;
  lastError: string;
}

/**
 * 审核模式的状态信息（并进对话头部一行）：仓库选择 / 轮询状态 / 开放 PR 数。
 * 按钮（立即轮询 / 仓库配置 / 清空对话）由头部统一渲染，避免两行重复。
 */
export default function ReviewStatus({ repos, repoId, onPick, polling, prCount, lastError }: Props) {
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

      <span className={'rb-status' + (lastError ? ' is-err' : '')} title={lastError || undefined}>
        {polling
          ? '⏳ 正在轮询 / 审核…'
          : lastError
            ? '⚠️ ' + lastError
            : '上次轮询：' + fmtAgo(repo?.lastPollAt)}
      </span>

      {repo && <span className="rb-count">{prCount} 个开放 PR</span>}
      {repo && !repo.hasToken && <span className="rb-warn">未配 Token</span>}
    </div>
  );
}
