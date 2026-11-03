import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { ChatMessage } from '../types';
import { esc, aiBlocks } from '../lib/markdown';

interface Props {
  messages: ChatMessage[];
  isLoading: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  /** 列表顶部的自定义内容（代码审核模式的工作台） */
  header?: ReactNode;
  /** 输入框上方的自定义内容（智能体切换） */
  inputTop?: ReactNode;
  /** 空状态；传 null 表示不渲染（工作台自己会占位） */
  empty?: ReactNode;
  avatar?: string;
  placeholder?: string;
}

/** AI 消息：按块渲染（一题一卡）。每块独立 DOM 且 key 稳定，流式时已完成的卡片不重建 */
function AiBubble({ content, streaming }: { content: string; streaming: boolean }) {
  const blocks = aiBlocks(content, streaming);
  const isCards = blocks.some(b => b.kind === 'q');
  return (
    <div
      className={'bubble' + (isCards ? ' bubble-qcards' : '') + (streaming ? ' is-streaming' : '')}
      onClick={(e) => {
        const btn = (e.target as HTMLElement).closest('.code-copy') as HTMLButtonElement | null;
        if (!btn) return;
        const code = btn.closest('.code-block')?.querySelector('code');
        if (code && navigator.clipboard) {
          navigator.clipboard.writeText(code.textContent || '').then(() => {
            btn.textContent = '已复制';
            window.setTimeout(() => { btn.textContent = '复制'; }, 1500);
          });
        }
      }}
    >
      {blocks.map(b => (
        <div
          key={b.key}
          className={'ai-block' + (b.live ? ' is-live' : '')}
          dangerouslySetInnerHTML={{ __html: b.html }}
        />
      ))}
    </div>
  );
}

export default function ChatPanel({
  messages, isLoading, onSend, onStop, header, inputTop, empty, avatar = '面', placeholder,
}: Props) {
  const [text, setText] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  /** 是否吸附在底部：用户往上翻看历史时置 false，不再强制拉回 */
  const stickRef = useRef(true);

  /* 监听用户滚动，判断是否贴底（留 80px 容差） */
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const onScroll = () => {
      stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  /* 仅当贴底时才自动跟随新内容；rAF 保证在布局完成后滚，避免和用户拖动打架 */
  useEffect(() => {
    const el = listRef.current;
    /* 没有消息时不滚到底：审核模式的 PR 工作台在最上面，滚到底会把它顶出视野 */
    if (!el || !stickRef.current || !messages.length) return;
    const id = requestAnimationFrame(() => {
      if (stickRef.current) el.scrollTop = el.scrollHeight;
    });
    return () => cancelAnimationFrame(id);
  }, [messages, isLoading]);

  const submit = () => {
    const t = text.trim();
    if (!t || isLoading) return;
    setText('');
    stickRef.current = true; // 自己发消息，拉回底部
    onSend(t);
  };

  /* reset() / 新建会话时 CopilotKit 会留一条空占位消息，不要渲染成空气泡 */
  const shown = messages.filter(m => m.role === 'user' || String(m.content ?? '').trim().length > 0);

  return (
    <>
      <div className="chat-list" ref={listRef}>
        {header}
        {shown.length === 0 && (empty === undefined ? (
          <div className="empty-state">
            <div className="big">面</div>
            <p>点输入框上方的「面试配置」选好方向与级别，再点「确认并生成面试题」</p>
            <p>也可以直接在输入框里提问，例如「再出 3 道算法题」</p>
            <p>换一个智能体？点输入框上方的「代码审核官」</p>
          </div>
        ) : empty)}
        {shown.map((m, i) => {
          const isUser = m.role === 'user';
          const streaming = !isUser && isLoading && i === shown.length - 1;
          return (
            <div key={m.id || i} className={'msg ' + (isUser ? 'user' : 'ai')}>
              <div className="avatar">{isUser ? '我' : avatar}</div>
              {isUser
                ? <div className="bubble" dangerouslySetInnerHTML={{ __html: esc(m.content).replace(/\n/g, '<br>') }} />
                : <AiBubble content={m.content} streaming={streaming} />}
            </div>
          );
        })}
        {isLoading && shown.length === 0 && (
          <div className="msg ai">
            <div className="avatar">{avatar}</div>
            <div className="bubble"><span className="typing"><i></i><i></i><i></i></span></div>
          </div>
        )}
      </div>

      {inputTop}

      <div className="chat-input">
        <textarea
          value={text}
          placeholder={placeholder || '输入消息…（Enter 发送，Shift+Enter 换行）'}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
        />
        {isLoading
          ? <button className="send-btn" onClick={onStop}>停止</button>
          : <button className="send-btn" onClick={submit} disabled={!text.trim()}>发送</button>}
      </div>
    </>
  );
}
