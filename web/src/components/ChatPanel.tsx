import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
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
  /** 禁用输入（例如会话还没关联专家）：输入框与发送按钮都不可用 */
  disabled?: boolean;
  /**
   * 会话标识（当前会话 id）。切换会话时它变化 → 强制滚到最新一条。
   * 必须由父级传入：只有父级知道「换了一场对话」，组件内部看 messages 分辨不出来
   * （两场对话都有消息，看上去只是内容变了）。
   */
  threadKey?: string;
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
  messages, isLoading, onSend, onStop, header, inputTop, empty, avatar = '面', placeholder, disabled = false,
  threadKey,
}: Props) {
  const [text, setText] = useState('');
  const listRef = useRef<HTMLDivElement>(null);
  /** 是否吸附在底部：用户往上翻看历史时置 false，不再强制拉回 */
  const stickRef = useRef(true);
  /** 当前是否有消息；供延迟执行的滚动回调读取实时值（闭包里的 messages 会过期） */
  const hasMsgsRef = useRef(false);
  hasMsgsRef.current = messages.length > 0;
  /**
   * 吸附保护窗口。
   * 内容分批渲染（Markdown、题目卡片）时容器高度会连续增长，而 scrollTop 还来不及跟上，
   * 于是 scroll 事件算出「离底部很远」→ 被误判成用户在翻历史 → 后续自动滚动全部跳过。
   * 这就是首屏/流式时「看着没在底部」的根因。
   * 窗口期内忽略 scroll 事件的「解除吸附」判断，并持续把视图钉在底部。
   */
  const pinRef = useRef(true);
  const pinTimerRef = useRef(0);
  const pinTickRef = useRef(0);

  /** 进入吸附保护窗口：期间持续钉底，直到 ms 内没有新的内容变化 */
  const startPin = (ms = 800) => {
    pinRef.current = true;
    window.clearTimeout(pinTimerRef.current);
    pinTimerRef.current = window.setTimeout(() => { pinRef.current = false; }, ms);
  };

  /* 安装时进入保护窗口：首屏消息是分批渲染的，高度会连续变化 */
  useEffect(() => {
    startPin(1200);
    return () => {
      window.clearTimeout(pinTimerRef.current);
      window.clearInterval(pinTickRef.current);
    };
  }, []);

  /* 保护窗口期内持续钉底（60ms 一次，覆盖异步渲染带来的高度变化） */
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    pinTickRef.current = window.setInterval(() => {
      if (!pinRef.current || !stickRef.current || !hasMsgsRef.current) return;
      el.scrollTop = el.scrollHeight;
    }, 60);
    return () => window.clearInterval(pinTickRef.current);
  }, []);

  /**
   * 判断是否吸附底部。
   * 关键：scroll 事件既可能来自用户滚动，也可能来自内容变高，二者分不出来，
   * 所以这里只做「回到底部就重新吸附」，解除吸附交给下面的用户手势事件。
   */
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const onScroll = () => {
      if (pinRef.current) { stickRef.current = true; return; }
      if (el.scrollHeight - el.scrollTop - el.clientHeight < 80) stickRef.current = true;
    };
    el.addEventListener('scroll', onScroll, { passive: true });
    return () => el.removeEventListener('scroll', onScroll);
  }, []);

  /** 用户主动往上翻：滚轮 / 触摸 / 翻页键 才算数（此时解除吸附） */
  useEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const up = () => { pinRef.current = false; window.clearTimeout(pinTimerRef.current); stickRef.current = false; };
    const onWheel = (e: WheelEvent) => { if (e.deltaY < 0) up(); };
    const onKey = (e: KeyboardEvent) => {
      if (['PageUp', 'ArrowUp', 'Home'].includes(e.key)) up();
    };
    el.addEventListener('wheel', onWheel, { passive: true });
    el.addEventListener('touchmove', up, { passive: true });
    el.addEventListener('keydown', onKey);
    return () => {
      el.removeEventListener('wheel', onWheel);
      el.removeEventListener('touchmove', up);
      el.removeEventListener('keydown', onKey);
    };
  }, []);

  /**
   * 贴底时自动跟随新内容。
   * 消息每批变化都重新进入保护窗口，所以「还在陆续渲染」期间会一直钉底，
   * 直到内容稳定 800ms 后自然结束。
   */
  useEffect(() => {
    const el = listRef.current;
    /* 没有消息时不滚到底：审核模式的 PR 工作台在最上面，滚到底会把它顶出视野 */
    if (!el || !stickRef.current || !messages.length) return;
    startPin();
    const go = () => { if (stickRef.current) el.scrollTop = el.scrollHeight; };
    const id = requestAnimationFrame(go);
    return () => cancelAnimationFrame(id);
  }, [messages, isLoading]);

  /**
   * 切换会话：复位吸附并跳到最新一条。
   * 上面那个跟随 effect 只看 messages，切会话时若两边恰好都「贴底」就不会触发滚动，
   * 于是停在上一场的滚动位置 —— 这里显式复位。
   * 判断「有没有消息」必须读 ref：回调延迟执行，闭包里的 messages 是切换那一刻的旧值。
   * 空会话（只有审核工作台）回到顶部，免得把工作台顶出视野。
   */
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    stickRef.current = true;
    if (!hasMsgsRef.current) { el.scrollTop = 0; return; }
    startPin(1200);
    const go = () => { if (stickRef.current) el.scrollTop = el.scrollHeight; };
    go();
    const raf = requestAnimationFrame(() => { go(); requestAnimationFrame(go); });
    const t = window.setTimeout(go, 150);
    return () => { cancelAnimationFrame(raf); window.clearTimeout(t); };
  }, [threadKey]);

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
            <p>在左侧「专家」里选择面试出题官，填好方向与级别后确认，即可生成面试题</p>
            <p>也可以直接在输入框里提问，例如「再出 3 道算法题」</p>
            <p>想换个专家？点左侧「专家」或标题栏的专家徽标</p>
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
          disabled={disabled}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
        />
        {isLoading
          ? <button className="send-btn" onClick={onStop}>停止</button>
          : <button className="send-btn" onClick={submit} disabled={disabled || !text.trim()}>发送</button>}
      </div>
    </>
  );
}
