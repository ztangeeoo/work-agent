import type { Repo } from '../types';
import { fmtAgo } from '../lib/review';

interface Props {
  repos: Repo[];
  repoId: string;
  onPick: (id: string) => void;
  onPoll: () => void;
  onAdd: () => void;
  onSettings: () => void;
  polling: boolean;
  prCount: number;
  lastError: string;
}

/** 审核模式的状态条：仓库选择 / 轮询状态 / 立即轮询 / 仓库设置 */
export default function ReviewBar({
  repos, repoId, onPick, onPoll, onAdd, onSettings, polling, prCount, lastError,
}: Props) {
  const repo = repos.find(r => r.id === repoId) || null;

  return (
    <div className="review-bar">
      <span className="rb-icon">📦</span>
      {repos.length ? (
        <select className="rb-select" value={repoId} onChange={(e) => onPick(e.target.value)} title="切换仓库">
          {repos.map(r => (
            <option key={r.id} value={r.id}>{r.owner}/{r.name}</option>
          ))}
        </select>
      ) : (
        <span className="rb-empty">还没有配置代码仓库</span>
      )}

      <span className={'rb-status' + (lastError ? ' is-err' : '')}>
        {polling
          ? '⏳ 正在轮询 / 审核…'
          : lastError
            ? '⚠️ ' + lastError
            : '上次轮询：' + fmtAgo(repo?.lastPollAt)}
      </span>

      {repo && <span className="rb-count">{prCount} 个开放 PR</span>}
      {repo && !repo.hasToken && <span className="rb-warn">未配 Token（限流 60 次/时）</span>}

      <div className="spacer" />

      <button className="btn btn-ghost btn-sm" onClick={onPoll} disabled={!repo || polling}>
        {polling ? '轮询中…' : '立即轮询'}
      </button>
      <button className="btn btn-ghost btn-sm" onClick={repo ? onSettings : onAdd}>
        {repo ? '⚙️ 仓库设置' : '＋ 添加仓库'}
      </button>
    </div>
  );
}
