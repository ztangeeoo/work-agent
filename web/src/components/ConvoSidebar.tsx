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
}

export default function ConvoSidebar({ convos, activeId, onSwitch, onDelete, onNew, banner }: Props) {
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
            const meta = [
              fmtTime(c.updatedAt || c.createdAt),
              (c.messages || []).length + ' 条消息',
              c.profile ? (c.profile.track + (c.profile.sub && c.profile.sub !== '随机' ? ' / ' + c.profile.sub : '')) : ''
            ].filter(Boolean).join(' · ');
            return (
              <div key={c.id} className={'convo-item' + (c.id === activeId ? ' active' : '')} onClick={() => onSwitch(c.id)}>
                <div className="convo-main">
                  <div className="convo-title">{c.title || '新会话'}</div>
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
