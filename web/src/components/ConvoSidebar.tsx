import { useEffect, useRef, useState } from 'react';
import type { Convo } from '../types';
import { fmtTime } from '../lib/storage';
import { searchMessages, type SearchHit } from '../lib/remote';

interface Props {
  convos: { activeId: string; list: Convo[] };
  activeId: string;
  onSwitch: (id: string) => void;
  onDelete: (id: string) => void;
  onNew: () => void;
  banner: React.ReactNode;
  /** 打开右侧的专家选择视图 */
  onOpenExperts: () => void;
  /** 专家视图当前是否展开（入口高亮用） */
  expertsOpen: boolean;
  /** 可选的专家数量，显示在入口徽标上 */
  expertCount: number;
}

export default function ConvoSidebar({
  convos, activeId, onSwitch, onDelete, onNew, banner, onOpenExperts, expertsOpen, expertCount,
}: Props) {
  /* ---------- 跨会话全文检索 ---------- */
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const seqRef = useRef(0);

  useEffect(() => {
    const query = q.trim();
    if (!query) { setHits([]); setSearching(false); return; }
    setSearching(true);
    const seq = ++seqRef.current;
    const t = window.setTimeout(async () => {
      const r = await searchMessages(query);
      if (seq !== seqRef.current) return; // 丢弃过期结果
      setHits(r);
      setSearching(false);
    }, 320);
    return () => window.clearTimeout(t);
  }, [q]);

  const isSearching = !!q.trim();

  const openHit = (convoId: string) => {
    onSwitch(convoId);
    setQ('');
  };

  return (
    <aside>
      {banner}

      {/* 专家入口：位于历史会话之上 */}
      <div className="side-experts">
        <div className="section-title">专家</div>
        <button
          type="button"
          className={'expert-entry' + (expertsOpen ? ' active' : '')}
          onClick={onOpenExperts}
          title="查看全部专家"
        >
          <span className="expert-entry-ico" aria-hidden="true">专</span>
          <span className="expert-entry-main">
            <span className="expert-entry-title">
              专家中心
              <span className="expert-entry-count">{expertCount}</span>
            </span>
            <span className="expert-entry-desc">面试出题官 · 代码审核官</span>
          </span>
          <span className="expert-entry-arrow" aria-hidden="true">›</span>
        </button>
      </div>

      <div className="side-head">
        <div className="section-title">历史会话</div>
        <button className="btn btn-ghost btn-sm" onClick={onNew} title="新建空白会话">新建</button>
      </div>

      <div className="side-search">
        <input
          type="search"
          value={q}
          placeholder="搜索历史记录…"
          onChange={(e) => setQ(e.target.value)}
          aria-label="跨会话搜索历史消息"
        />
        {q && (
          <button className="side-search-clear" title="清空" onClick={() => setQ('')}>✕</button>
        )}
      </div>

      {isSearching ? (
        <div className="convo-list">
          {searching && !hits.length && <div className="convo-empty">检索中…</div>}
          {!searching && !hits.length && <div className="convo-empty">没有匹配的历史消息</div>}
          {hits.map(h => (
            <div key={h.id} className="search-hit" onClick={() => openHit(h.convoId)}
                 title="点击跳转到该会话">
              <div className="search-hit-head">
                <span className="search-hit-title">{h.convoTitle || '未命名会话'}</span>
                <span className="search-hit-time">{fmtTime(h.createdAt)}</span>
              </div>
              <div className="search-hit-snip">{h.snippet}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="convo-list">
          {convos.list.length === 0 && <div className="convo-empty">暂无历史会话</div>}
          {convos.list.map(c => {
            const isReview = c.mode === 'review';
            const meta = [
              fmtTime(c.updatedAt || c.createdAt),
              (c.messages || []).length + ' 条消息',
              isReview ? '代码审核' : (c.profile ? (c.profile.track + (c.profile.sub && c.profile.sub !== '随机' ? ' / ' + c.profile.sub : '')) : '')
            ].filter(Boolean).join(' · ');
            return (
              <div key={c.id} className={'convo-item' + (c.id === activeId ? ' active' : '')} onClick={() => onSwitch(c.id)}>
                <div className="convo-main">
                  <div className="convo-title">
                    <span className={'convo-badge' + (isReview ? ' is-review' : '')}>{isReview ? '审' : '面'}</span>
                    {c.title || '新会话'}
                  </div>
                  <div className="convo-meta">{meta}</div>
                </div>
                <button className="convo-del" title="删除此会话"
                  onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}>✕</button>
              </div>
            );
          })}
        </div>
      )}
    </aside>
  );
}
