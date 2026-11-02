import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useCopilotChatHeadless_c } from '@copilotkit/react-core';
import { AGENTS } from './data/tracks';
import { buildGeneratePrompt } from './lib/prompt';
import { esc } from './lib/markdown';
import {
  LS_CFG, LS_CONVOS, fmtTime, genId, loadCfg, loadConvos,
  persistConvos, saveCfg, snapshotProfile, titleFromMessages,
} from './lib/storage';
import { checkHealth } from './lib/health';
import { deleteRemoteConvo, fetchRemoteConvos, flushNow, queueSaveConvo } from './lib/remote';
import { useAgentStages } from './lib/stages';
import { newProfile } from './lib/storage';
import type { ChatMessage, Convo, ModelCfg, Profile } from './types';
import ConvoSidebar from './components/ConvoSidebar';
import AgentBar from './components/AgentBar';
import ChatPanel from './components/ChatPanel';
import AgentTrace from './components/AgentTrace';
import ConfigModal from './components/ConfigModal';
import AgentModal from './components/AgentModal';

export default function ChatApp() {
  /* ---------- 模型配置 ---------- */
  const [cfg, setCfg] = useState<ModelCfg>(() => {
    const c = loadCfg();
    return { base: c.base || '', key: c.key || '', model: c.model || '' };
  });
  const [cfgOpen, setCfgOpen] = useState(false);

  /* ---------- 面试配置 ---------- */
  const [profile, setProfile] = useState<Profile>(newProfile);
  const [agentOpen, setAgentOpen] = useState(false);
  const [genErr, setGenErr] = useState('');

  /* ---------- 多会话 ---------- */
  const [convos, setConvos] = useState<{ activeId: string; list: Convo[] }>(() => {
    const d = loadConvos();
    if (!d.list.length) {
      const id = genId();
      const conv: Convo = { id, title: '新会话', createdAt: Date.now(), updatedAt: Date.now(), messages: [], profile: null };
      const nd = { activeId: id, list: [conv] };
      persistConvos(nd);
      return nd;
    }
    if (!d.activeId || !d.list.find(c => c.id === d.activeId)) d.activeId = d.list[0].id;
    return d;
  });
  const convoId = convos.activeId;

  /* ---------- 服务状态 ---------- */
  const [health, setHealth] = useState({ proxyOk: false, agentOk: false });
  const [toastMsg, setToastMsg] = useState('');
  const toastTimer = useRef<number>(0);
  const toast = useCallback((msg: string) => {
    setToastMsg(msg);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToastMsg(''), 2200);
  }, []);

  /* ---------- CopilotKit 聊天 ---------- */
  const { messages, setMessages, sendMessage, isLoading, stopGeneration, reset } = useCopilotChatHeadless_c();

  /* ---------- 智能体执行流水线 ---------- */
  const [runId, setRunId] = useState('');
  const trace = useAgentStages(runId, isLoading);

  /* 初始恢复当前会话消息 */
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    const conv = convos.list.find(c => c.id === convoId);
    if (conv && conv.messages && conv.messages.length) {
      setMessages(conv.messages.map(m => ({ id: m.id, role: m.role, content: m.content })));
      if (conv.profile) setProfile({ ...newProfile(), ...conv.profile });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* ---------- 与服务端 SQLite 合并（服务端为主，本地更新的回推上去） ---------- */
  const [synced, setSynced] = useState(false);
  useEffect(() => {
    let alive = true;
    (async () => {
      const remote = await fetchRemoteConvos();
      if (!alive) return;
      if (!remote) { setSynced(true); return; } // 服务端未启用 DB → 纯本地模式

      setConvos(prev => {
        /* 本地只有一个空壳会话、而服务端有数据 → 直接以服务端为准，不推空会话 */
        const pristine = prev.list.length === 1
          && !(prev.list[0].messages || []).length
          && (prev.list[0].title === '新会话' || !prev.list[0].title);

        const map = new Map<string, Convo>();
        remote.forEach(c => map.set(c.id, c));

        const toPush: Convo[] = [];
        if (!pristine) {
          prev.list.forEach(c => {
            const r = map.get(c.id);
            if (!r || (c.updatedAt || 0) > (r.updatedAt || 0)) {
              map.set(c.id, c);
              toPush.push(c);
            }
          });
        }

        const list = [...map.values()]
          .sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0))
          .slice(0, 30);
        if (!list.length) return prev;

        const activeId = list.some(c => c.id === prev.activeId) ? prev.activeId : list[0].id;
        const nd = { activeId, list };
        persistConvos(nd);
        toPush.forEach(c => queueSaveConvo(c));
        return nd;
      });
      setSynced(true);
    })();
    return () => { alive = false; };
  }, []);

  /* 合并完成后：若服务端版本消息更多（例如上次关页面丢了尾巴），补回当前会话 */
  useEffect(() => {
    if (!synced) return;
    const conv = convos.list.find(c => c.id === convos.activeId);
    if (!conv) return;
    const remoteMsgs = conv.messages || [];
    if (remoteMsgs.length > messages.length) {
      setMessages(remoteMsgs.map(m => ({ id: m.id, role: m.role, content: m.content })));
      if (conv.profile) setProfile({ ...newProfile(), ...conv.profile });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [synced]);

  /* 页面隐藏/关闭前把待写入的变更立即落库 */
  useEffect(() => {
    const onHide = () => flushNow();
    const onVis = () => { if (document.visibilityState === 'hidden') flushNow(); };
    window.addEventListener('pagehide', onHide);
    document.addEventListener('visibilitychange', onVis);
    return () => {
      window.removeEventListener('pagehide', onHide);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, []);

  /* 健康检查 */
  useEffect(() => {
    let alive = true;
    const run = async () => { const h = await checkHealth(); if (alive) setHealth(h); };
    run();
    const t = window.setInterval(run, 15000);
    return () => { alive = false; window.clearInterval(t); };
  }, []);

  /* ---------- 会话持久化 ---------- */
  /** CopilotKit 的消息 content 可能为 undefined，落盘前统一成 ChatMessage */
  const asChatMessages = (list: typeof messages): ChatMessage[] =>
    list.map(m => ({ id: m.id, role: m.role as ChatMessage['role'], content: String(m.content ?? '') }));

  const saveCurrent = useCallback(() => {
    const snapshot = asChatMessages(messages);
    setConvos(prev => {
      const list = prev.list.map(c =>
        c.id === prev.activeId
          ? { ...c, messages: snapshot.slice(-40), updatedAt: Date.now(),
              title: c.title && c.title !== '新会话' ? c.title : titleFromMessages(snapshot),
              profile: snapshotProfile(profile) }
          : c
      );
      list.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
      const nd = { activeId: prev.activeId, list: list.slice(0, 30) };
      persistConvos(nd);
      /* 同步到服务端 SQLite（去抖 1.2s，流式输出不会刷爆请求） */
      const active = nd.list.find(c => c.id === nd.activeId);
      if (active) queueSaveConvo(active);
      return nd;
    });
  }, [messages, profile]);

  /* 消息变化即落盘（流式过程中也会持续写，刷新不丢） */
  useEffect(() => {
    if (!restoredRef.current) return;
    saveCurrent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  /* ---------- 发送 ---------- */
  const syncModelConfig = useCallback(async (rid: string) => {
    try {
      await fetch('/api/model-config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base: cfg.base, key: cfg.key, model: cfg.model, profile, runId: rid }),
      });
    } catch (_) {}
  }, [cfg, profile]);

  const send = useCallback(async (text: string) => {
    const t = (text || '').trim();
    if (!t || isLoading) return;
    if (!cfg.base || !cfg.model) {
      toast('❌ 请先点击右上角齿轮图标完成「模型配置」');
      setCfgOpen(true);
      return;
    }
    // 每轮生成一个 runId：后端各智能体的起止阶段事件按它归类，前端订阅后渲染流水线
    const rid = 'run_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    setRunId(rid);
    await syncModelConfig(rid);
    try {
      await sendMessage({ id: genId(), role: 'user', content: t });
    } catch (err: any) {
      const msg: string = err?.message || String(err);
      const errMsg = {
        id: genId(), role: 'assistant' as const,
        content: '❌ ' + msg + '\n\n排查顺序：\n1. 本地服务是否已启动（双击 start.bat）\n2. API 地址与模型名是否正确\n3. API Key 是否有效、额度是否充足\n4. 本地模型（如 Ollama）是否已启动',
      };
      setMessages(((prev: any[]) => [...prev, errMsg]) as any);
    }
  }, [cfg, isLoading, sendMessage, setMessages, syncModelConfig, toast]);

  /* ---------- 会话操作 ---------- */
  const newConvo = useCallback((silent = false) => {
    if (isLoading) return;
    saveCurrent();
    const id = genId();
    const conv: Convo = { id, title: '新会话', createdAt: Date.now(), updatedAt: Date.now(), messages: [], profile: snapshotProfile(profile) };
    setConvos(prev => {
      const nd = { activeId: id, list: [conv, ...prev.list].slice(0, 30) };
      persistConvos(nd);
      return nd;
    });
    reset();
    setRunId('');
    if (!silent) toast('已创建新会话');
  }, [isLoading, profile, reset, saveCurrent, toast]);

  const switchConvo = useCallback((id: string) => {
    if (isLoading || id === convoId) return;
    saveCurrent();
    flushNow(); // 切走前把上一场立即落库
    const conv = convos.list.find(c => c.id === id);
    if (!conv) return;
    setConvos(prev => {
      const nd = { ...prev, activeId: id };
      persistConvos(nd);
      return nd;
    });
    reset();
    setRunId('');
    setMessages((conv.messages || []).slice(-40).map(m => ({ id: m.id, role: m.role, content: m.content })));
    if (conv.profile) setProfile({ ...newProfile(), ...conv.profile });
    toast('已切换会话：' + (conv.title || '新会话'));
  }, [convoId, convos.list, isLoading, reset, saveCurrent, setMessages, toast]);

  const deleteConvo = useCallback((id: string) => {
    const d = loadConvos();
    d.list = d.list.filter(c => c.id !== id);
    if (d.activeId === id) d.activeId = d.list.length ? d.list[0].id : '';
    if (!d.list.length) {
      const nid = genId();
      d.list = [{ id: nid, title: '新会话', createdAt: Date.now(), updatedAt: Date.now(), messages: [], profile: null }];
      d.activeId = nid;
    }
    persistConvos(d);
    setConvos(d);
    void deleteRemoteConvo(id); // 服务端同步删除，避免刷新后“复活”
    if (id === convoId) {
      const next = d.list.find(c => c.id === d.activeId);
      reset();
      setRunId('');
      setMessages((next?.messages || []).map(m => ({ id: m.id, role: m.role, content: m.content })));
      if (next?.profile) setProfile({ ...newProfile(), ...next.profile });
    }
    toast('已删除会话');
  }, [convoId, reset, setMessages, toast]);

  /* ---------- 智能体卡片 ---------- */
  const activateAgent = useCallback(() => {
    if (isLoading) return;
    if (messages.length) newConvo(true);   // 当前会话已有内容才新建，避免堆积空会话
    setAgentOpen(true);
    toast('已切换到「面试出题官」，请填写面试配置');
  }, [isLoading, messages.length, newConvo, toast]);

  /* ---------- 面试配置确认 ---------- */
  const onAgentConfirm = useCallback(() => {
    if (!cfg.base || !cfg.model) {
      setGenErr('请先点击右上角齿轮图标完成「模型配置」');
      setCfgOpen(true);
      return;
    }
    setGenErr('');
    setAgentOpen(false);
    send(buildGeneratePrompt(profile));
  }, [cfg.base, cfg.model, profile, send]);

  /* ---------- 派生数据 ---------- */
  const activeAgent = useMemo(() => AGENTS.find(a => a.id === 'interviewer') || AGENTS[0], []);
  const chatTitle = `${profile.track}${profile.sub && profile.sub !== '随机' ? ' / ' + profile.sub : ''} · ${profile.level} · ${profile.exp}`;
  const indText = profile.industry === '不限' ? '行业不限' : profile.industry;
  const chatMeta = profile.resumeName
    ? `已加载简历：${profile.resumeName} · ${indText}`
    : `${profile.sub === '随机' ? '细分随机' : '细分：' + profile.sub} · ${indText} · 未上传简历`;

  const onSaveCfg = (c: ModelCfg) => {
    setCfg(c);
    saveCfg(c);
    toast('✅ 配置已保存');
  };

  return (
    <>
      <header>
        <div className="logo">🎯 IT<span>面试出题官</span></div>
        <div className="sub">AI Interviewer · React + CopilotKit + AgentScope 三智能体</div>
        <div className="spacer"></div>
        <span id="connText">{health.proxyOk ? (health.agentOk ? '本地服务就绪' : '智能体服务未连接') : '未检测到本地服务'}</span>
        <span className={'conn-dot ' + (health.proxyOk ? (health.agentOk ? 'ok' : 'err') : '')}></span>
        <button className="cfg-btn" title="模型配置" onClick={() => setCfgOpen(v => !v)}>
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3.2" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.08a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.08a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.08a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
          <span className={'conn-dot ' + (health.proxyOk ? (health.agentOk ? 'ok' : 'err') : '')}></span>
        </button>
      </header>

      <main>
        <ConvoSidebar
          convos={convos}
          activeId={convoId}
          onSwitch={switchConvo}
          onDelete={deleteConvo}
          onNew={() => newConvo()}
          banner={
            !health.proxyOk ? (
              <div className="banner show">
                ⚠️ 未检测到本地服务，模型请求会失败。<br />
                请在项目目录双击 <code>start.bat</code>，然后访问 <code>http://localhost:8787</code>
              </div>
            ) : !health.agentOk ? (
              <div className="banner show">
                ⚠️ 本地页面服务正常，但 <b>AgentScope 智能体服务</b>未连接，出题会失败。<br />
                请双击项目里的 <code>start.bat</code> 启动全部服务。
              </div>
            ) : null
          }
        />

        <div className="chat-wrap">
          <div className="chat-head">
            <div>
              <div className="title">{chatTitle}</div>
              <div className="meta">{chatMeta}</div>
            </div>
            <div className="spacer"></div>
            <button className="btn btn-ghost btn-sm" onClick={() => { reset(); setRunId(''); saveCurrent(); }}>清空对话</button>
          </div>

          <AgentTrace trace={trace} active={isLoading} />

          <ChatPanel
            messages={asChatMessages(messages)}
            isLoading={isLoading}
            onSend={send}
            onStop={stopGeneration}
          />

          <AgentBar agent={activeAgent} onClickCard={activateAgent} onOpenCfg={() => setAgentOpen(true)} />
        </div>
      </main>

      <ConfigModal open={cfgOpen} onClose={() => setCfgOpen(false)} cfg={cfg} onSave={onSaveCfg} toast={toast} />
      <AgentModal
        open={agentOpen}
        onClose={() => setAgentOpen(false)}
        profile={profile}
        onProfileChange={setProfile}
        onConfirm={onAgentConfirm}
        genErr={genErr}
        cfg={cfg}
        onNeedCfg={() => setCfgOpen(true)}
        toast={toast}
      />

      <div id="toast" className={toastMsg ? 'show' : ''}>{toastMsg}</div>
    </>
  );
}
