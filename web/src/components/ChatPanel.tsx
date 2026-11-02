import { useEffect, useRef, useState } from 'react';
import type { ChatMessage } from '../types';
import { esc, renderAiHtml, renderAiHtmlStreaming } from '../lib/markdown';

interface Props {
  messages: ChatMessage[];
  isLoading: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

/** AI 消息：题目卡片化渲染（流式期间不拆卡，结束后由上层重渲染触发拆卡） */
function AiBubble({ content, streaming }: { content: string; streaming: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const html = streaming ? renderAiHtmlStreaming(content) : renderAiHtml(content);
  return (
    <div
      className="bubble"
      ref={ref}
      dangerouslySetInnerHTML={{ __html: html }}
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
    />
  );
}

export default function ChatPanel({ messages, isLoading, onSend, onStop }: Props) {
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
    if (!el || !stickRef.current) return;
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

  return (
    <>
      <div className="chat-list" ref={listRef}>
        {messages.length === 0 && (
          <div className="empty-state">
            <div className="big">🎯</div>
            <p>点击下方「面试出题官」卡片，新建面试会话</p>
            <p>填写面试配置（方向 / 级别 / 经验 / 简历）并确认，即可生成面试题</p>
            <p>也可以直接在下方输入任何指令，例如"再出 3 道算法题"</p>
          </div>
        )}
        {messages.map((m, i) => {
          const isUser = m.role === 'user';
          const streaming = !isUser && isLoading && i === messages.length - 1;
          return (
            <div key={m.id || i} className={'msg ' + (isUser ? 'user' : 'ai')}>
              <div className="avatar">{isUser ? '🧑' : '🎯'}</div>
              {isUser
                ? <div className="bubble" dangerouslySetInnerHTML={{ __html: esc(m.content).replace(/\n/g, '<br>') }} />
                : <AiBubble content={m.content} streaming={streaming} />}
            </div>
          );
        })}
        {isLoading && messages.length === 0 && (
          <div className="msg ai">
            <div className="avatar">🎯</div>
            <div className="bubble"><span className="typing"><i></i><i></i><i></i></span></div>
          </div>
        )}
      </div>

      <div className="chat-input">
        <textarea
          value={text}
          placeholder="输入消息…（Enter 发送，Shift+Enter 换行）"
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
