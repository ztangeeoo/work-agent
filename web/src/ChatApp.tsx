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
import { applyTheme, loadTheme, nextTheme, saveTheme, type Theme } from './lib/theme';
import { deleteRemoteConvo, fetchRemoteConvos, flushNow, queueSaveConvo } from './lib/remote';
import { useAgentStages } from './lib/stages';
import { newProfile } from './lib/storage';
import {
  fetchPRs, fetchRepos, fetchReviews, pollRepo, postReviewToPR,
  reviewPR as requestReview, sortPRs, useReviewEvents,
} from './lib/review';
import type { AgentMode, ChatMessage, Convo, ModelCfg, PRItem, Profile, Repo, ReviewItem } from './types';
import ConvoSidebar from './components/ConvoSidebar';
import AgentSwitcher from './components/AgentSwitcher';
import ChatPanel from './components/ChatPanel';
import AgentTrace from './components/AgentTrace';
import ConfigModal from './components/ConfigModal';
import InterviewSetup from './components/InterviewSetup';
import RepoSetup from './components/RepoSetup';
import ReviewBar from './components/ReviewBar';
import ReviewWorkbench from './components/ReviewWorkbench';

const LS_MODE = 'interviewer_mode_v1';

export default function ChatApp() {
  /* ---------- 模型配置 ---------- */
  const [cfg, setCfg] = useState<ModelCfg>(() => {
    const c = loadCfg();
    return { base: c.base || '', key: c.key || '', model: c.model || '' };
  });
  const [cfgOpen, setCfgOpen] = useState(false);

  /* ---------- 主题（浅色 / 深色）---------- */
  // 初值直接读 localStorage：main.tsx 已经把类挂上了，这里只是让按钮图标对齐
  const [theme, setTheme] = useState<Theme>(loadTheme);
  useEffect(() => {
    applyTheme(theme);
    saveTheme(theme);
  }, [theme]);
  const toggleTheme = useCallback(() => setTheme(t => nextTheme(t)), []);

  /* ---------- 面试配置 ---------- */
  const [profile, setProfile] = useState<Profile>(newProfile);
  /* 面试配置走弹框：点输入框上方的「⚙️ 面试配置」打开，生成后自动关闭 */
  const [setupOpen, setSetupOpen] = useState(false);
  const [genErr, setGenErr] = useState('');

  /* ---------- 智能体模式：面试出题 / 代码审核 ---------- */
  const [mode, setMode] = useState<AgentMode>(() =>
    localStorage.getItem(LS_MODE) === 'review' ? 'review' : 'interview',
  );
  const [repos, setRepos] = useState<Repo[]>([]);
  const [repoId, setRepoId] = useState('');
  const [prs, setPRs] = useState<PRItem[]>([]);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [selectedPr, setSelectedPr] = useState<number | null>(null);
  const [runningPr, setRunningPr] = useState<number | null>(null);
  const [polling, setPolling] = useState(false);
  /* 仓库配置同样走弹框：没有可用仓库时自动打开 */
  const [repoOpen, setRepoOpen] = useState(false);
  const [repoEditing, setRepoEditing] = useState<Repo | null>(null);
  const [repoErr, setRepoErr] = useState('');

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
  const trace = useAgentStages(runId, isLoading, mode);

  /* 新建会话后的短暂窗口里，CopilotKit 的 reset() 可能把上一场消息又塞回来。
     这里记住「新建之前就存在的消息 id」，在出现真正的新消息之前一律不落到新会话。 */
  const staleMsgIdsRef = useRef<Set<string>>(new Set());
  const freshConvoRef = useRef(false);

  /* 初始恢复：直接恢复当前会话（左边列表保持不变，不按智能体拆会话） */
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
        /* 空会话不上推（避免每次开新浏览器都往库里加一条空壳） */
        toPush.filter(c => (c.messages || []).length).forEach(c => queueSaveConvo(c));
        return nd;
      });
      setSynced(true);
    })();
    return () => { alive = false; };
  }, []);

  /* 合并完成后（以及 reset/切会话后 messages 短暂为空时）：服务端版本消息更多就补回 */
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
  }, [synced, convos.activeId, convos.list, messages.length]);

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

  /* ---------- 代码审核：仓库 / PR / 审核报告 ---------- */
  const loadRepoList = useCallback(async () => {
    try {
      const list = await fetchRepos();
      setRepos(list);
      setRepoId(prev => (prev && list.some(r => r.id === prev) ? prev : (list[0]?.id || '')));
    } catch (e: any) {
      setRepoErr(e.message || String(e));
    }
  }, []);

  const loadPRData = useCallback(async (id: string) => {
    if (!id) {
      setPRs([]);
      setReviews([]);
      setSelectedPr(null);
      return;
    }
    try {
      const [prList, reviewList] = await Promise.all([fetchPRs(id), fetchReviews(id, 20)]);
      const sorted = sortPRs(prList);
      setPRs(sorted);
      setReviews(reviewList);
      setRepoErr('');
      setSelectedPr(prev => {
        if (prev && sorted.some(p => p.number === prev)) return prev;
        const reviewed = reviewList.find(r => sorted.some(p => p.number === r.prNumber));
        if (reviewed) return reviewed.prNumber;
        return sorted.length ? sorted[0].number : null;
      });
    } catch (e: any) {
      setRepoErr(e.message || String(e));
    }
  }, []);

  useEffect(() => {
    if (mode !== 'review') return;
    loadRepoList();
  }, [mode, loadRepoList]);

  useEffect(() => {
    if (mode !== 'review') return;
    loadPRData(repoId);
  }, [mode, repoId, loadPRData]);

  /* 轮询 / 审核进度：后端推送事件，前端即时更新 */
  useReviewEvents(mode === 'review', (ev) => {
    if (repoId && ev.repoId && ev.repoId !== repoId) return;
    if (ev.type === 'poll-start') setPolling(true);
    else if (ev.type === 'poll-done') { setPolling(false); loadPRData(repoId); }
    else if (ev.type === 'poll-error') {
      setPolling(false);
      toast('⚠️ 轮询失败：' + (ev.error || '未知错误'));
    } else if (ev.type === 'review-start') {
      setRunningPr(ev.prNumber ?? null);
      setPolling(true);
    } else if (ev.type === 'review-done') {
      setRunningPr(null);
      setPolling(false);
      if (ev.review) {
        const r = ev.review;
        setReviews(prev => [r, ...prev.filter(x => x.id !== r.id)]);
      }
      loadPRData(repoId);
    } else if (ev.type === 'review-error') {
      setRunningPr(null);
      setPolling(false);
      if (ev.error) toast('⚠️ ' + ev.error);
    }
  });

  const handlePoll = useCallback(async () => {
    if (!repoId) { setRepoEditing(null); setRepoOpen(true); return; }
    setPolling(true);
    try {
      await pollRepo(repoId);
      toast('已触发轮询，结果会自动刷新');
    } catch (e: any) {
      setPolling(false);
      toast('❌ ' + (e.message || e));
    }
  }, [repoId, toast]);

  const handleReviewOne = useCallback(async (n: number) => {
    if (!repoId) return;
    setRunningPr(n);
    setSelectedPr(n);
    try {
      const r = await requestReview(repoId, n);
      setReviews(prev => [r, ...prev.filter(x => x.id !== r.id)]);
      await loadPRData(repoId);
      toast('✅ PR #' + n + ' 审核完成');
    } catch (e: any) {
      toast('❌ 审核失败：' + (e.message || e));
    } finally {
      setRunningPr(null);
    }
  }, [repoId, loadPRData, toast]);

  const handlePost = useCallback(async (review: ReviewItem) => {
    try {
      const out = await postReviewToPR(review.id);
      setReviews(prev => prev.map(x => (x.id === review.id ? out.review : x)));
      toast('✅ 已回帖到 PR');
    } catch (e: any) {
      toast('❌ 回帖失败：' + (e.message || e));
    }
  }, [toast]);

  /* ---------- 会话持久化 ---------- */
  /** CopilotKit 的消息 content 可能为 undefined，落盘前统一成 ChatMessage */
  const asChatMessages = (list: typeof messages): ChatMessage[] =>
    list.map(m => ({ id: m.id, role: m.role as ChatMessage['role'], content: String(m.content ?? '') }));

  /**
   * 落盘用快照：丢掉空内容消息。
   * 新建会话 / reset() 瞬间，CopilotKit 会留下一条 content 为空的占位消息，
   * 若照单全收，新会话就会凭空多出「1 条消息」（空气泡 + 侧栏计数 +1）。
   */
  const toSavable = (list: typeof messages): ChatMessage[] =>
    asChatMessages(list).filter(m => m.content.trim().length > 0);

  const saveCurrent = useCallback(() => {
    let snapshot = toSavable(messages);
    /* 新会话刚建好时，过滤掉「上一场残留下来的」旧消息 */
    if (freshConvoRef.current) {
      const stale = staleMsgIdsRef.current;
      const fresh = snapshot.filter(m => !stale.has(String(m.id)));
      if (!fresh.length) return;          // 还是旧消息回流：这次不落盘
      snapshot = fresh;
      freshConvoRef.current = false;      // 出现真正的新消息，恢复常规保存
    }
    setConvos(prev => {
      const cur = prev.list.find(c => c.id === prev.activeId);
      /* 初始化 / reset() 竞争会让 messages 短暂为 []，此时不能用它覆盖已有历史 */
      if (!snapshot.length && cur && (cur.messages || []).length) return prev;
      const list = prev.list.map(c =>
        c.id === prev.activeId
          ? { ...c, messages: snapshot.slice(-40), updatedAt: Date.now(),
              title: c.title && c.title !== '新会话' ? c.title : titleFromMessages(snapshot),
              profile: snapshotProfile(profile), mode }
          : c
      );
      list.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
      const nd = { activeId: prev.activeId, list: list.slice(0, 30) };
      persistConvos(nd);
      /* 同步到服务端 SQLite（去抖 1.2s，流式输出不会刷爆请求）
       * 空会话（刚点「新建」还没说话）不推服务端，否则库里会攒一堆「新会话 · 0 条消息」 */
      const active = nd.list.find(c => c.id === nd.activeId);
      if (active && (active.messages || []).length) queueSaveConvo(active);
      return nd;
    });
  }, [messages, mode, profile]);

  /* 消息变化即落盘（流式过程中也会持续写，刷新不丢） */
  useEffect(() => {
    if (!restoredRef.current) return;
    saveCurrent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages]);

  /* ---------- 发送 ---------- */
  /* 审核模式追问时，把当前 PR 的审核报告一起带给后端 */
  const selectedReview = useMemo(
    () => (selectedPr == null ? null : reviews.find(r => r.prNumber === selectedPr) || null),
    [reviews, selectedPr],
  );

  const syncModelConfig = useCallback(async (rid: string) => {
    const reviewCtx = mode === 'review' && selectedReview
      ? {
          repo: (selectedReview.owner ? selectedReview.owner + '/' : '') + selectedReview.name,
          number: selectedReview.prNumber,
          title: selectedReview.title,
          risk: selectedReview.risk,
          decision: selectedReview.decision,
          reportMd: String(selectedReview.reportMd || '').slice(0, 8000),
        }
      : null;
    try {
      await fetch('/api/model-config', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base: cfg.base, key: cfg.key, model: cfg.model, profile, runId: rid,
          agentMode: mode, review: reviewCtx,
        }),
      });
    } catch (_) {}
  }, [cfg, mode, profile, selectedReview]);

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
    /* 记录旧消息 id：新会话在收到真正的新消息前，不接受这些旧消息 */
    staleMsgIdsRef.current = new Set(asChatMessages(messages).map(m => String(m.id)));
    freshConvoRef.current = true;
    const id = genId();
    const conv: Convo = {
      id, title: '新会话', createdAt: Date.now(), updatedAt: Date.now(),
      messages: [], profile: snapshotProfile(profile), mode,
    };
    setConvos(prev => {
      const nd = { activeId: id, list: [conv, ...prev.list].slice(0, 30) };
      persistConvos(nd);
      return nd;
    });
    reset();
    setRunId('');
    if (!silent) toast('已创建新会话');
  }, [isLoading, messages, mode, profile, reset, saveCurrent, toast]);

  const switchConvo = useCallback((id: string) => {
    if (isLoading || id === convoId) return;
    saveCurrent();
    flushNow(); // 切走前把上一场立即落库
    freshConvoRef.current = false;   // 切会话：恢复常规保存
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

  /** 显式清空当前对话：先把会话历史置空（saveCurrent 的守卫才会放行），再清消息 */
  const clearCurrent = useCallback(() => {
    freshConvoRef.current = false;   // 清空是显式操作，允许落盘
    reset();
    setMessages([]);
    setRunId('');
    setConvos(prev => {
      const list = prev.list.map(c =>
        c.id === prev.activeId ? { ...c, messages: [], updatedAt: Date.now() } : c);
      const nd = { activeId: prev.activeId, list };
      persistConvos(nd);
      const active = list.find(c => c.id === nd.activeId);
      if (active) queueSaveConvo(active);
      return nd;
    });
    toast('已清空当前对话');
  }, [reset, setMessages, toast]);

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

  /* ---------- 智能体切换 ----------
   * 只切换「谁来回话」，不新建、不切换、不过滤左侧会话。
   * 再次点击当前智能体 = 打开它的配置弹框（面试配置 / 仓库配置）。
   */
  const switchMode = useCallback((m: AgentMode) => {
    if (m === mode) {
      if (m === 'review') {
        setRepoEditing(repos.find(r => r.id === repoId) || null);
        setRepoOpen(true);
      } else {
        setSetupOpen(true);
      }
      return;
    }
    if (isLoading) return;
    saveCurrent();
    flushNow();
    setMode(m);
    localStorage.setItem(LS_MODE, m);
    /* 切过去不主动弹框（弹框会打断视线）；只有审核官还没有仓库时才自动弹出来引导 */
    if (m === 'review' && !repos.length) {
      setRepoEditing(null);
      setRepoOpen(true);
    }
    toast(m === 'review' ? '已切换到「代码审核官」' : '已切换到「面试出题官」');
  }, [mode, isLoading, saveCurrent, repos, repoId, toast]);

  /* ---------- 面试配置确认 ---------- */
  const onAgentConfirm = useCallback(() => {
    if (!cfg.base || !cfg.model) {
      setGenErr('请先点击右上角齿轮图标完成「模型配置」');
      setCfgOpen(true);
      return;
    }
    setGenErr('');
    setSetupOpen(false);   // 生成后关掉弹框，把屏幕还给对话
    send(buildGeneratePrompt(profile));
  }, [cfg.base, cfg.model, profile, send]);

  /* ---------- 派生数据 ---------- */
  const activeAgent = useMemo(() => AGENTS.find(a => a.mode === mode) || AGENTS[0], [mode]);
  const currentRepo = useMemo(() => repos.find(r => r.id === repoId) || null, [repos, repoId]);
  /* 左侧会话列表保持完整：不按智能体过滤，切换时也不新建会话 */
  const visibleConvos = convos;

  const indText = profile.industry === '不限' ? '行业不限' : profile.industry;
  const chatTitle = mode === 'review'
    ? (currentRepo ? `${currentRepo.owner}/${currentRepo.name}` : '代码审核官')
    : `${profile.track}${profile.sub && profile.sub !== '随机' ? ' / ' + profile.sub : ''} · ${profile.level} · ${profile.exp}`;
  const chatMeta = mode === 'review'
    ? (currentRepo
        ? `${prs.length} 个开放 PR · 每 ${Math.round(currentRepo.intervalSec / 60) || 1} 分钟轮询 · ${currentRepo.hasToken ? '已配 Token' : '未配 Token'}`
        : '先填代码地址，再轮询 PR 自动审核')
    : (profile.resumeName
        ? `已加载简历：${profile.resumeName} · ${indText}`
        : `${profile.sub === '随机' ? '细分随机' : '细分：' + profile.sub} · ${indText} · 未上传简历`);

  const onSaveCfg = (c: ModelCfg) => {
    setCfg(c);
    saveCfg(c);
    toast('✅ 配置已保存');
  };

  return (
    <>
      <header>
        <div className="logo">
          <span className="logo-mark" aria-hidden="true"></span>
          工作智能体集合
        </div>
        <div className="sub">面试出题官 + 代码审核官 · 多智能体协作</div>
        <div className="spacer"></div>
        <span id="connText">{health.proxyOk ? (health.agentOk ? '本地服务就绪' : '智能体服务未连接') : '未检测到本地服务'}</span>
        <span className={'conn-dot ' + (health.proxyOk ? (health.agentOk ? 'ok' : 'err') : '')}></span>
        <button
          className="cfg-btn theme-btn"
          title={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          aria-label={theme === 'dark' ? '切换到浅色模式' : '切换到深色模式'}
          aria-pressed={theme === 'dark'}
          onClick={toggleTheme}
        >
          {theme === 'dark' ? (
            /* 太阳：表示「点一下回到浅色」 */
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="4.2" />
              <path d="M12 2.4v2.2M12 19.4v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.4 12h2.2M19.4 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" />
            </svg>
          ) : (
            /* 月亮：表示「点一下进入深色」 */
            <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          )}
        </button>
        <button className="cfg-btn" title="模型配置" onClick={() => setCfgOpen(v => !v)}>
          <svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3.2" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.08a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h.08a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.08a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>
          <span className={'conn-dot ' + (health.proxyOk ? (health.agentOk ? 'ok' : 'err') : '')}></span>
        </button>
      </header>

      <main>
        <ConvoSidebar
          convos={visibleConvos}
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
            {mode === 'review' && currentRepo && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => { setRepoEditing(currentRepo); setRepoOpen(true); }}
              >
                仓库设置
              </button>
            )}
            <button className="btn btn-ghost btn-sm" onClick={clearCurrent}>清空对话</button>
          </div>

          {mode === 'review' && (
            <ReviewBar
              repos={repos}
              repoId={repoId}
              onPick={setRepoId}
              onPoll={handlePoll}
              onAdd={() => { setRepoEditing(null); setRepoOpen(true); }}
              onSettings={() => { setRepoEditing(currentRepo); setRepoOpen(v => !v); }}
              polling={polling}
              prCount={prs.length}
              lastError={repoErr || currentRepo?.lastError || ''}
            />
          )}

          <AgentTrace trace={trace} active={isLoading} />

          <ChatPanel
            messages={asChatMessages(messages)}
            isLoading={isLoading}
            onSend={send}
            onStop={stopGeneration}
            avatar={activeAgent.icon}
            header={mode === 'review' ? (
              <ReviewWorkbench
                repo={currentRepo}
                prs={prs}
                reviews={reviews}
                selected={selectedPr}
                onSelect={setSelectedPr}
                onReview={handleReviewOne}
                onPost={handlePost}
                onAddRepo={() => { setRepoEditing(null); setRepoOpen(true); }}
                onPoll={handlePoll}
                polling={polling || isLoading}
                runningPr={runningPr}
                onAsk={send}
              />
            ) : undefined}
            empty={mode === 'review' ? null : undefined}
            placeholder={mode === 'review'
              ? '针对这份审核报告追问，例如「第 2 个问题怎么改？」'
              : '输入消息…（Enter 发送，Shift+Enter 换行）'}
            inputTop={
              <AgentSwitcher
                agents={AGENTS}
                mode={mode}
                onSwitch={switchMode}
                onOpenConfig={() => {
                  if (mode === 'review') {
                    setRepoEditing(currentRepo);
                    setRepoOpen(true);
                  } else {
                    setSetupOpen(true);
                  }
                }}
                configLabel={mode === 'review' ? '仓库配置' : '面试配置'}
                configOpen={mode === 'review' ? repoOpen : setupOpen}
              />
            }
          />
        </div>
      </main>

      {/* 三个弹框：面试配置 / 仓库配置 / 模型配置（模型配置叠在最上层） */}
      <InterviewSetup
        open={setupOpen}
        onClose={() => setSetupOpen(false)}
        profile={profile}
        onProfileChange={setProfile}
        onConfirm={onAgentConfirm}
        genErr={genErr}
        cfg={cfg}
        onNeedCfg={() => setCfgOpen(true)}
        toast={toast}
      />

      <RepoSetup
        open={repoOpen}
        onClose={() => setRepoOpen(false)}
        repo={repoEditing}
        onSaved={(repo, isNew) => {
          setRepos(prev => (isNew ? [...prev, repo] : prev.map(r => (r.id === repo.id ? repo : r))));
          setRepoId(repo.id);
          setRepoEditing(repo);
        }}
        onDeleted={(id) => {
          setRepos(prev => prev.filter(r => r.id !== id));
          setRepoId(prev => (prev === id ? '' : prev));
        }}
        toast={toast}
      />

      <ConfigModal open={cfgOpen} onClose={() => setCfgOpen(false)} cfg={cfg} onSave={onSaveCfg} toast={toast} />

      <div id="toast" className={toastMsg ? 'show' : ''}>{toastMsg}</div>
    </>
  );
}
