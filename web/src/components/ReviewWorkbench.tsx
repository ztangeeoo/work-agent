import { useMemo } from 'react';
import type { PRItem, Repo, ReviewItem } from '../types';
import { decisionMeta, riskMeta, stagesLine } from '../lib/review';
import { renderAiHtml } from '../lib/markdown';

interface Props {
  repo: Repo | null;
  prs: PRItem[];
  reviews: ReviewItem[];
  selected: number | null;
  onSelect: (n: number) => void;
  onReview: (n: number) => void;
  onPost: (r: ReviewItem) => void;
  onAddRepo: () => void;
  onPoll: () => void;
  polling: boolean;
  runningPr: number | null;
  onAsk: (text: string) => void;
}

/**
 * 审核工作台：PR 列表 + 当前 PR 的审核报告。
 * 作为聊天列表的头部渲染，追问的回答会自然出现在报告下方。
 */
export default function ReviewWorkbench({
  repo, prs, reviews, selected, onSelect, onReview, onPost, onAddRepo, onPoll,
  polling, runningPr, onAsk,
}: Props) {
  const review = useMemo(
    () => (selected == null ? null : reviews.find(r => r.prNumber === selected) || null),
    [reviews, selected],
  );
  const pr = useMemo(
    () => (selected == null ? null : prs.find(p => p.number === selected) || null),
    [prs, selected],
  );

  if (!repo) {
    return (
      <div className="rw-empty">
        <div className="big">审</div>
        <p><b>代码审核官</b>：配置一个代码仓库，我会按设定的间隔轮询开放 PR，自动审核并给出修改意见。</p>
        <p className="dim">支持 GitHub / GitLab，私有仓库填 Access Token；意见默认只给你看，也可以一键回帖到 PR。</p>
        <button className="btn btn-primary btn-sm" onClick={onAddRepo}>添加代码仓库</button>
      </div>
    );
  }

  return (
    <div className="rw">
      <div className="rw-head">
        <span className="rw-head-title">开放 PR</span>
        <span className="rw-head-count">{prs.length}</span>
        <span className="rw-head-repo">{repo.owner}/{repo.name}</span>
        <div className="spacer" />
        <button className="btn btn-ghost btn-sm" onClick={onPoll} disabled={polling}>
          {polling ? '轮询中…' : '刷新 PR'}
        </button>
      </div>

      {prs.length === 0 ? (
        <div className="rw-hint">
          还没有开放 PR。点「刷新 PR」立刻拉一次；也可以在仓库设置里调整自动轮询间隔。
        </div>
      ) : (
        <div className="rw-prs">
          {prs.map(p => {
            const rm = p.review ? riskMeta(p.review.risk) : null;
            const running = runningPr === p.number;
            return (
              <button
                key={p.number}
                className={'pr-chip' + (selected === p.number ? ' active' : '') + (p.draft ? ' is-draft' : '')}
                onClick={() => onSelect(p.number)}
                title={p.title}
              >
                <span className="pr-no">#{p.number}</span>
                <span
                  className={'pr-dot' + (running ? ' is-running' : '')}
                  data-risk={p.review?.risk || 'none'}
                />
                <span className="pr-title">{p.title || '(无标题)'}</span>
                {running && <span className="pr-tag is-running">审核中</span>}
                {!running && p.review && <span className={'pr-tag ' + rm!.cls}>{rm!.label}</span>}
                {!running && !p.review && <span className="pr-tag is-pending">{p.draft ? '草稿' : '待审'}</span>}
              </button>
            );
          })}
        </div>
      )}

      {selected == null ? (
        <div className="rw-hint">↑ 选择一个 PR，查看审核结论与逐条问题</div>
      ) : !review ? (
        <div className="rw-report rw-report-empty">
          <div className="rw-empty-mini">
            <b>PR #{selected} 还没有审核报告</b>
            <p>{pr?.title || ''}</p>
            <button
              className="btn btn-primary btn-sm"
              disabled={runningPr === selected}
              onClick={() => onReview(selected)}
            >
              {runningPr === selected ? '审核中…' : '立即审核这个 PR'}
            </button>
          </div>
        </div>
      ) : (
        <div className="rw-report">
          <div className="rw-rep-head">
            <a className="rw-rep-title" href={review.prUrl || pr?.url} target="_blank" rel="noreferrer">
              #{review.prNumber} {review.title || pr?.title}
            </a>
            <span className={'rw-badge ' + riskMeta(review.risk).cls}>
              {riskMeta(review.risk).icon} {riskMeta(review.risk).label}
            </span>
            <span className={'rw-badge ' + decisionMeta(review.decision).cls}>
              {decisionMeta(review.decision).label}
            </span>
            <span className="rw-issue-count">{review.issueCount} 处问题</span>
            {review.author && <span className="rw-author">@{review.author}</span>}
          </div>

          {stagesLine(review) && <div className="rw-stages">{stagesLine(review)}</div>}

          <div
            className="rw-md"
            dangerouslySetInnerHTML={{ __html: renderAiHtml(review.reportMd) }}
          />

          <div className="rw-actions">
            <button className="btn btn-ghost btn-sm" onClick={() => onAsk('请解释这次审核里最严重的问题，并给出可以直接粘贴的修复代码')}>
              讲修复方案
            </button>
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                if (navigator.clipboard) {
                  navigator.clipboard.writeText(review.reportMd).catch(() => {});
                }
              }}
            >
              复制报告
            </button>
            {repo.hasToken && (
              <button
                className="btn btn-ghost btn-sm"
                disabled={review.posted}
                onClick={() => onPost(review)}
              >
                {review.posted ? '已回帖' : '回帖到 PR'}
              </button>
            )}
            <a className="btn btn-ghost btn-sm" href={review.prUrl || pr?.url} target="_blank" rel="noreferrer">
              在平台打开
            </a>
            {review.postError && <span className="rw-err">回帖失败：{review.postError}</span>}
          </div>
        </div>
      )}
    </div>
  );
}
