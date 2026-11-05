import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useCopilotChatHeadless_c } from '@copilotkit/react-core';
import { AGENTS } from './data/tracks';
import { buildGeneratePrompt } from './lib/prompt';
import { esc } from './lib/markdown';
import {
  LS_CONVOS, blankProfile, fmtTime, genId, loadConvos,
  persistConvos, snapshotProfile, titleFromMessages,
} from './lib/storage';
import { checkHealth } from './lib/health';
import { fetchSkills } from './lib/skills';
import { applyTheme, loadTheme, nextTheme, saveTheme, type Theme } from './lib/theme';
import { clearRemoteMessages, deleteRemoteConvo, fetchExperts, fetchRemoteConvos, flushNow, queueSaveConvo } from './lib/remote';
import { useAgentStages } from './lib/stages';
import { newProfile } from './lib/storage';
import {
  fetchPRs, fetchPollSnapshot, fetchRepos, fetchReviews, pollRepo, postReviewToPR,
  reviewPR as requestReview, setRepoPolling, sortPRs, useReviewEvents,
} from './lib/review';
import type {
  AgentMode, ChatMessage, Convo, ExpertDef, ModelCfg, PRItem, PollScheduler, Profile, Repo, ReviewItem,
  SkillsPayload,
} from './types';
import ConvoSidebar from './components/ConvoSidebar';
import ExpertPicker from './components/ExpertPicker';
import ChatPanel from './components/ChatPanel';
import AgentTrace from './components/AgentTrace';
import ConfigModal from './components/ConfigModal';
import ConfirmModal from './components/ConfirmModal';
import InterviewSetup from './components/InterviewSetup';
import RepoSetup from './components/RepoSetup';
import ReviewStatus from './components/ReviewStatus';
import ReviewWorkbench from './components/ReviewWorkbench';

/**
 * 会话与专家的关系不是直接一条边，而是两级：
 *   convo.agentId → 智能体（agent） → 专家（expert，mode = interview / review）
 * 服务端把这三层都落在 SQLite 里；前端只用 agentId 表达绑定，mode 由所属专家派生。
 */
export default function ChatApp() {
  /* ---------- 模型配置（全局） ----------
   * base / key / model 是全局唯一一份，存在服务端（setting 表 + Node 内存）。
   * 页面本身不保存、也不回传配置：只有「模型配置」弹框保存时才写一次，
   * 否则某个旧标签页打开就会把全局配置改坏（401 的来源）。 */
  const [cfg, setCfg] = useState<ModelCfg>({ base: '', key: '', model: '' });
  const [cfgOpen, setCfgOpen] = useState(false);

  /* ---------- 主题（浅色 / 深色）---------- */
  // 初值直接读 localStorage：main.tsx 已经把类挂上了，这里只是让按钮图标对齐
  const [theme, setTheme] = useState<Theme>(loadTheme);
  useEffect(() => {
    applyTheme(theme);
    saveTheme(theme);
  }, [theme]);
  const toggleTheme = useCallback(() => setTheme(t => nextTheme(t)), []);

  /* ---------- 全局模型配置：启动时从服务端读一次 ----------
   * 只读不写：页面只是「显示器」，配置的唯一写入口是齿轮弹框的保存。
   * 换浏览器 / 清了缓存都能拿回服务端那份，不需要在本地另存一份。 */
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch('/api/model-config');
        const j = await res.json().catch(() => ({}));
        const c = j?.cfg || {};
        if (!alive || !c.base || !c.model) return;
        setCfg({ base: String(c.base), key: String(c.key || ''), model: String(c.model) });
      } catch (_) { /* 本地服务未启动：用户手动配一次即可 */ }
    })();
    return () => { alive = false; };
  }, []);

  /* ---------- 面试配置 ---------- */
  const [profile, setProfile] = useState<Profile>(newProfile);
  /* 面试配置走弹框：点输入框上方的「⚙️ 面试配置」打开，生成后自动关闭 */
  const [setupOpen, setSetupOpen] = useState(false);
  /**
   * 新建任务时用的空白草稿（从专家中心进入）。
   * 与 profile（当前会话已生效的画像）分开存：填到一半取消不会污染当前会话，
   * 确认后才写回 profile 并带进新任务。
   */
  const [setupDraft, setSetupDraft] = useState<Profile | null>(null);
  const [genErr, setGenErr] = useState('');

  /* ---------- 专家与智能体（以服务端 SQLite 为准，内置常量兜底） ---------- */
  const [experts, setExperts] = useState<ExpertDef[]>(AGENTS);
  /**
   * 当前会话绑定的智能体 id（落库在 convo.agent_id）。
   * null = 该会话还没关联专家/智能体 —— 新建会话的默认状态。
   */
  const [agentId, setAgentId] = useState<string | null>(null);

  /* ---------- 智能体模式：面试出题 / 代码审核 ----------
   * 由绑定的智能体所属专家派生（agent → expert → mode），只用于界面布局与后端路由。
   * 不再从 localStorage 继承上一次的专家，避免新会话「自动带上」某个专家。 */
  const [mode, setMode] = useState<AgentMode | null>(null);
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
  /** 后台轮询调度的存活状态（进程起来 ≠ 调度循环在跑） */
  const [scheduler, setScheduler] = useState<PollScheduler>({ running: false, tickSec: 0 });
  const [pollToggleBusy, setPollToggleBusy] = useState(false);
  /** 「清空对话」的二次确认弹框：清空不可撤销，多一步总比误删强 */
  const [clearAsk, setClearAsk] = useState(false);

  /* ---------- 视图：对话 / 专家选择 ---------- */
  const [view, setView] = useState<'chat' | 'experts'>('chat');
  /** 技能目录与「专家 → 技能」绑定关系（只读，来自 backend/skills/） */
  const [skills, setSkills] = useState<SkillsPayload | null>(null);
  /** 从专家入口选中、等待填配置的专家；确认后新建任务会话并永久绑定 */
  const [pendingExpert, setPendingExpert] = useState<AgentMode | null>(null);
  /** 新会话建好后要发送的首轮指令（等 reset 落地再发） */
  const pendingPromptRef = useRef('');

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

  /* 专家 / 智能体查表：会话绑定用 agentId，界面布局与后端路由用 mode（= 专家所属能力包） */
  const expertOfMode = useCallback(
    (m: AgentMode | null) => (m ? experts.find(e => e.mode === m) || null : null),
    [experts],
  );

  const primaryAgentOf = useCallback((m: AgentMode | null) => {
    const list = expertOfMode(m)?.agents || [];
    return list.find(a => a.primary) || list[0] || null;
  }, [expertOfMode]);

  const modeOfAgent = useCallback((id?: string | null): AgentMode | null => {
    if (!id) return null;
    const e = experts.find(x => (x.agents || []).some(a => a.id === id));
    return e ? e.mode : null;
  }, [experts]);

  /**
   * 会话绑定的专家模式：agentId → 所属专家 → mode。
   * 老数据没有 agentId 时退回冗余的 mode；再没有就按「有消息=面试会话」兜底。
   */
  const boundModeOf = useCallback((c?: Convo | null): AgentMode | null => {
    if (!c) return null;
    const byAgent = modeOfAgent(c.agentId);
    if (byAgent) return byAgent;
    if (c.mode) return c.mode;
    return (c.messages || []).length ? 'interview' : null;
  }, [modeOfAgent]);

  /** 会话绑定的智能体 id：优先库里的 agentId，老数据按主智能体补一个 */
  const boundAgentIdOf = useCallback((c?: Convo | null): string | null => {
    if (!c) return null;
    if (c.agentId) return c.agentId;
    return primaryAgentOf(boundModeOf(c))?.id || null;
  }, [boundModeOf, primaryAgentOf]);

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
  const trace = useAgentStages(runId, isLoading, mode ?? 'interview');

  /* 新建会话后的短暂窗口里，CopilotKit 的 reset() 可能把上一场消息又塞回来。
     这里记住「新建之前就存在的消息 id」，在出现真正的新消息之前一律不落到新会话。 */
  const staleMsgIdsRef = useRef<Set<string>>(new Set());
  const freshConvoRef = useRef(false);
  /**
   * 已点「清空对话」作废掉的消息 id。
   * CopilotKit 的 messages 来自 agent 内部状态，某些时序下（agent 重连、thread 恢复）
   * 会把刚清掉的历史回填回来，导致「点了清空、屏幕没变」。
   * 这里在清空时记下当时的 id，渲染与落盘都过滤掉它们 —— 不依赖 agent 是否配合。
   */
  const clearedMsgIdsRef = useRef<Set<string>>(new Set());

  /* 初始恢复：直接恢复当前会话（左边列表保持不变，不按智能体拆会话） */
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;

    const conv = convos.list.find(c => c.id === convoId);
    if (conv) {
      if (conv.messages && conv.messages.length) {
        setMessages(conv.messages.map(m => ({ id: m.id, role: m.role, content: m.content })));
        if (conv.profile) setProfile({ ...newProfile(), ...conv.profile });
      }
      /* 会话绑定的智能体 → 专家（空会话未关联时 mode 为 null，界面显示「未关联专家」） */
      const bound = boundModeOf(conv);
      setMode(bound);
      setAgentId(boundAgentIdOf(conv));
      if (bound === 'review' && conv.repoId) setRepoId(conv.repoId);
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
            } else if (r && !r.mode && c.mode) {
              /* 服务端是加字段前的旧记录：保留本地已绑定的智能体/专家与配置 */
              const merged: Convo = { ...r, agentId: c.agentId, agentName: c.agentName, mode: c.mode, repoId: c.repoId };
              map.set(c.id, merged);
              toPush.push(merged);
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
        /* 空壳会话不上推；但已绑定专家的任务会话（哪怕还没有消息）要推，否则绑定信息只在本地 */
        toPush
          .filter(c => (c.messages || []).length || (c.mode && c.title && c.title !== '新会话'))
          .forEach(c => queueSaveConvo(c));
        return nd;
      });
      setSynced(true);
    })();
    return () => { alive = false; };
  }, []);

  /* 合并完成后（以及 reset/切会话后 messages 短暂为空时）：服务端版本消息更多就补回 */
  const syncedModeRef = useRef(false);
  useEffect(() => {
    if (!synced) return;
    const conv = convos.list.find(c => c.id === convos.activeId);
    if (!conv) return;
    /* 合并后当前会话可能换成了服务端的某一场：把它的专家绑定一并恢复，避免界面与数据不一致 */
    if (!syncedModeRef.current) {
      syncedModeRef.current = true;
      const bound = boundModeOf(conv);
      setMode(bound);
      setAgentId(boundAgentIdOf(conv));
      if (bound === 'review' && conv.repoId) setRepoId(conv.repoId);
    }
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

  /* ---------- 技能绑定（专家卡片上展示「绑了哪些技能」） ---------- */
  useEffect(() => {
    let alive = true;
    void fetchSkills().then(d => { if (alive) setSkills(d); });
    return () => { alive = false; };
  }, []);

  /* 专家 / 智能体清单：库里有就以库为准（含各自的智能体），拿不到就继续用内置常量 */
  useEffect(() => {
    let alive = true;
    (async () => {
      const list = await fetchExperts();
      if (alive && list) setExperts(list);
    })();
    return () => { alive = false; };
  }, []);

  /* ---------- 代码审核：仓库 / PR / 审核报告 ---------- */
  const loadRepoList = useCallback(async () => {
    try {
      const snap = await fetchPollSnapshot();
      setScheduler(snap.scheduler);
      const list = snap.repos;
      setRepos(list);
      setRepoId(prev => (prev && list.some(r => r.id === prev) ? prev : (list[0]?.id || '')));
    } catch (e: any) {
      setRepoErr(e.message || String(e));
    }
  }, []);

  /** 只刷新「轮询状态」：左侧列表的徽标与头部的倒计时都靠它保活 */
  const syncPoll = useCallback(async () => {
    try {
      const snap = await fetchPollSnapshot();
      setScheduler(prev => (
        prev.running === snap.scheduler.running && prev.tickSec === snap.scheduler.tickSec ? prev : snap.scheduler
      ));
      /* 只有轮询状态真的变了才替换数组：4s 一次的空刷新不该让整个对话树重渲染 */
      setRepos(prev => {
        const same = prev.length === snap.repos.length && snap.repos.every((r, i) => {
          const p = prev[i];
          return !!p && p.id === r.id && p.enabled === r.enabled && p.polling === r.polling
            && p.phase === r.phase && p.reviewingPr === r.reviewingPr
            && p.lastPollAt === r.lastPollAt && p.lastError === r.lastError
            && p.openPrs === r.openPrs && p.nextPollAt === r.nextPollAt && p.intervalSec === r.intervalSec;
        });
        return same ? prev : snap.repos;
      });
    } catch (_) { /* 服务不可用时保持上一次状态，不要清空，否则徽标会闪 */ }
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

  /* 轮询状态保活：只要还有审核会话（或正停在审核界面），就每 4s 拉一次快照。
   * 后端的调度是每 20s 巡检一次到期仓库，4s 的轮询足以让「轮询中 / 下一轮」跟得上，
   * 也不会给本地服务增加负担。 */
  const reviewConvoCount = useMemo(
    () => convos.list.filter(c => (c.mode ?? ((c.messages || []).length ? 'interview' : null)) === 'review').length,
    [convos.list],
  );

  useEffect(() => {
    if (mode !== 'review' && reviewConvoCount === 0) return;
    let alive = true;
    const tick = () => { if (alive) void syncPoll(); };
    tick();
    const id = window.setInterval(tick, 4000);
    return () => { alive = false; window.clearInterval(id); };
  }, [mode, reviewConvoCount, syncPoll]);

  /* 轮询 / 审核进度：后端推送事件，前端即时更新 */
  useReviewEvents(true, (ev) => {
    /* 先刷新状态快照：事件是「某一刻开始/结束」，下一轮倒计时与指示灯都要跟着变 */
    if (ev.type !== 'review-posted') void syncPoll();
    /* 别的仓库的事件只用来更新左侧列表徽标，不打断当前界面的推进 */
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
    /* 后端整轮审核上限 600s（REVIEW_TIMEOUT_SEC），这里多留 30s 兜底：
     * 中间层把连接吞掉时，按钮也不能永远停在「审核中」。 */
    const ac = new AbortController();
    const timer = window.setTimeout(() => ac.abort(), 630000);
    try {
      const r = await requestReview(repoId, n, ac.signal);
      setReviews(prev => [r, ...prev.filter(x => x.id !== r.id)]);
      await loadPRData(repoId);
      toast('✅ PR #' + n + ' 审核完成');
    } catch (e: any) {
      if (e?.name === 'AbortError') {
        toast('⏱ 审核超时，已停止等待（先在齿轮里点「测试连接」确认模型能用）');
      } else {
        toast('❌ 审核失败：' + (e.message || e));
      }
      /* review-start 把全局指示也点亮了；失败时这里兜底关掉，
       * 不依赖 review-error 事件是否送达（断线重连、切换仓库都可能漏） */
      setPolling(false);
    } finally {
      window.clearTimeout(timer);
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
    let snapshot = toSavable(messages)
      .filter(m => !clearedMsgIdsRef.current.has(String(m.id)));   // 已清空的不再写回
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
              profile: snapshotProfile(profile),
              /* 会话 → 智能体：未关联时保持 undefined，不要让会话凭空「继承」专家 */
              agentId: agentId ?? undefined,
              mode: mode ?? undefined,
              /* 审核会话的专家配置 = 当前绑定的仓库 */
              repoId: mode === 'review' ? repoId : c.repoId }
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
  }, [agentId, messages, mode, profile, repoId]);

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

  /* 每一轮只同步「这一轮对话的上下文」，不碰全局模型配置：
   * 画像、当前 PR、仓库 id、runId 都是一次性的，会话不该把自己的东西
   * 写进全局配置（否则切个会话 / 开个旧标签页就会串味，甚至改坏 key）。 */
  const syncRunContext = useCallback(async (rid: string) => {
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
      await fetch('/api/run-context', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          profile, runId: rid,
          agentMode: mode || 'interview', review: reviewCtx,
          /* 当前仓库：审核官靠它绑定只读工具（列 MR / 拉 diff / 读文件） */
          repoId: repoId || '',
        }),
      });
    } catch (_) {}
  }, [mode, profile, selectedReview, repoId]);

  const send = useCallback(async (text: string) => {
    const t = (text || '').trim();
    if (!t || isLoading) return;
    /* 未绑定专家的会话不能发消息：先让用户选专家（会话是空的，选中后会直接绑定当前会话） */
    if (!mode) {
      toast('请先在专家中心选择一位专家');
      setView('experts');
      return;
    }
    if (!cfg.base || !cfg.model) {
      toast('❌ 请先点击右上角齿轮图标完成「模型配置」');
      setCfgOpen(true);
      return;
    }
    // 每轮生成一个 runId：后端各智能体的起止阶段事件按它归类，前端订阅后渲染流水线
    const rid = 'run_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    setRunId(rid);
    await syncRunContext(rid);
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
  }, [cfg, isLoading, mode, sendMessage, setMessages, syncRunContext, toast]);

  /* ---------- 会话操作 ---------- */
  const newConvo = useCallback((silent = false, opts: { mode?: AgentMode; agentId?: string; agentName?: string; title?: string; repoId?: string; profile?: Profile; persist?: boolean } = {}) => {
    if (isLoading) return;
    saveCurrent();
    /* 默认不继承当前会话的专家：新建出来的会话是「未关联专家」的空白会话 */
    const nextMode = opts.mode ?? null;
    const nextAgentId = opts.agentId ?? null;
    setMode(nextMode);
    setAgentId(nextAgentId);
    /* 记录旧消息 id：新会话在收到真正的新消息前，不接受这些旧消息 */
    staleMsgIdsRef.current = new Set(asChatMessages(messages).map(m => String(m.id)));
    freshConvoRef.current = true;
    const id = genId();
    const boundRepoId = opts.repoId ?? repoId;
    const conv: Convo = {
      id, title: opts.title || '新会话', createdAt: Date.now(), updatedAt: Date.now(),
      messages: [], profile: snapshotProfile(opts.profile ?? profile),
      agentId: nextAgentId ?? undefined,
      agentName: opts.agentName,
      mode: nextMode ?? undefined,
      repoId: nextMode === 'review' ? boundRepoId : undefined,
    };
    setConvos(prev => {
      const nd = { activeId: id, list: [conv, ...prev.list].slice(0, 30) };
      persistConvos(nd);
      return nd;
    });
    /* 任务会话没有消息也要落库，否则刷新后服务端合并会把绑定信息弄丢 */
    if (opts.persist) queueSaveConvo(conv);
    reset();
    setRunId('');
    setView('chat');
    if (!silent) toast('已创建新会话');
  }, [isLoading, messages, mode, profile, repoId, reset, saveCurrent, toast]);

  /**
   * 新建一个任务会话（专家确认后调用），会话建好后即与「专家 + 其主智能体」永久绑定。
   * 当前会话还没有内容时直接复用它（改名 + 绑定），否则另开一个，绝不改动已有会话的绑定。
   */
  const startTask = useCallback((m: AgentMode, title: string, firstPrompt?: string, opts: { repoId?: string; profile?: Profile } = {}) => {
    if (isLoading) { toast('当前任务进行中，请稍候'); return; }
    setView('chat');
    pendingPromptRef.current = firstPrompt || '';
    /* 仓库刚保存就建任务时 state 里的 repoId 还没刷新，用调用方传入的 id 兜底 */
    const boundRepoId = opts.repoId ?? repoId;
    /* 会话绑定的是智能体，不是专家：取该专家下的主智能体（库里 is_primary=1 那条） */
    const boundAgent = primaryAgentOf(m);
    const boundAgentId = boundAgent?.id;

    const hasMsgs = asChatMessages(messages).some(x => String(x.content || '').trim());
    if (!hasMsgs) {
      setMode(m);
      setAgentId(boundAgentId || null);
      setConvos(prev => {
        const list = prev.list.map(c =>
          c.id === prev.activeId
            ? { ...c, title, mode: m, agentId: boundAgentId, agentName: boundAgent?.name,
                updatedAt: Date.now(),
                /* 新建任务时表单刚填好的画像要跟着会话落下去（state 里的 profile 还没刷新） */
                profile: opts.profile ? snapshotProfile(opts.profile) : c.profile,
                repoId: m === 'review' ? boundRepoId : undefined }
            : c);
        const nd = { activeId: prev.activeId, list };
        persistConvos(nd);
        const active = list.find(c => c.id === nd.activeId);
        if (active) queueSaveConvo(active);   // 空会话直接绑定时也要落库
        return nd;
      });
      return;
    }
    newConvo(true, {
      mode: m, agentId: boundAgentId, agentName: boundAgent?.name,
      title, repoId: boundRepoId, profile: opts.profile, persist: true,
    });
  }, [isLoading, messages, newConvo, primaryAgentOf, repoId, toast]);

  /* 新会话 / 切换专家落地后再发首轮指令：此时 send 的 mode 已是新专家，避免用旧专家发请求 */
  useEffect(() => {
    const p = pendingPromptRef.current;
    if (!p || isLoading) return;
    pendingPromptRef.current = '';
    void send(p);
  }, [messages, isLoading, send, mode]);

  /** 切换会话：换会话的同时把消息、专家绑定、仓库一起切过去 */
  const switchConvo = useCallback((id: string) => {
    if (id === convoId) return;
    /**
     * 正在生成时也允许切换，但必须先停掉当前这一轮。
     * 原因：agent 是单例，正在跑的流会把后续 token 继续追加到 agent.messages 上，
     * 而这些消息已经被我们换成了新会话的 —— 不中断就会把上一场的输出灌进新会话。
     * 代价是上一轮结果丢了，所以在 toast 里说清楚。
     */
    const wasRunning = isLoading;
    if (wasRunning) stopGeneration();
    saveCurrent();
    flushNow(); // 切走前把上一场立即落库
    /* 同理：切换后 CopilotKit 可能把上一场的消息又回流一次，
       记下这些 id，在出现本场真正的新消息前不落盘，避免消息串场 */
    staleMsgIdsRef.current = new Set(asChatMessages(messages).map(m => String(m.id)));
    freshConvoRef.current = true;
    const conv = convos.list.find(c => c.id === id);
    if (!conv) return;
    setConvos(prev => {
      const nd = { ...prev, activeId: id };
      persistConvos(nd);
      return nd;
    });
    reset();
    setRunId('');
    /* 用与落盘格式一致的消息渲染：不能只塞 conv.messages（可能是空数组），
       否则 reset() 之后界面会空一拍，等回流到达才补上 */
    const restored = (conv.messages || []).slice(-40).map(m => ({ id: m.id, role: m.role, content: m.content }));
    setMessages(restored);
    if (conv.profile) setProfile({ ...newProfile(), ...conv.profile });
    /* 会话没关联专家（新建后未选专家）→ 回到「未关联专家」状态 */
    const bound = boundModeOf(conv);
    setMode(bound);
    setAgentId(boundAgentIdOf(conv));
    /* 审核会话跟着它绑定的仓库走；面试会话不动仓库（仓库只在审核界面出现） */
    if (bound === 'review' && conv.repoId) setRepoId(conv.repoId);
    setView('chat');
    toast((wasRunning ? '已停止上一轮生成，' : '') + '已切换到：' + (conv.title || '新会话'));
  }, [boundAgentIdOf, boundModeOf, convoId, convos.list, isLoading, reset, saveCurrent, setMessages, stopGeneration, toast]);

  /** 显式清空当前对话
   *
   * 三条腿一起走，任何一条都不能省：
   *  1. 界面：reset() + setMessages([])，并把这批消息 id 记进 clearedMsgIdsRef ——
   *     agent 若把旧历史回填，渲染层会过滤掉，屏幕不会再「复活」；
   *  2. 本地：会话 messages 置空并 persistConvos；
   *  3. 服务端：DELETE /api/convos/{id}/messages（幂等），不走「覆盖保存 + 1.2s 去抖」
   *     —— 那条路上只要 agent 回填一次，空写就会被非空覆盖，正是「点了没生效」的原因。
   */
  const clearCurrent = useCallback(() => {
    if (isLoading) stopGeneration();          // 正在流式输出时先停，否则会继续写回
    freshConvoRef.current = false;            // 清空是显式操作，允许落盘
    asChatMessages(messages).forEach(m => clearedMsgIdsRef.current.add(String(m.id)));
    reset();
    setMessages([]);
    setRunId('');
    let cleared: Convo | null = null;
    setConvos(prev => {
      const list = prev.list.map(c =>
        c.id === prev.activeId ? { ...c, messages: [], updatedAt: Date.now() } : c);
      const nd = { activeId: prev.activeId, list };
      persistConvos(nd);
      cleared = list.find(c => c.id === nd.activeId) || null;
      return nd;
    });
    void (async () => {
      const removed = await clearRemoteMessages(convoId);
      /* 接口不可用（服务没起来 / 旧版本）→ 退回覆盖式保存，至少别让刷新后复活 */
      if (removed === null && cleared) queueSaveConvo(cleared);
    })();
    toast('已清空当前对话');
  }, [convoId, isLoading, messages, reset, setMessages, stopGeneration, toast]);

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
      const bound = boundModeOf(next);
      setMode(bound);
      setAgentId(boundAgentIdOf(next));
      if (bound === 'review' && next?.repoId) setRepoId(next.repoId);
    }
    toast('已删除会话');
  }, [boundAgentIdOf, boundModeOf, convoId, reset, setMessages, toast]);

  /* ---------- 专家选择 ----------
   * 左侧「专家」入口 → 右侧专家卡片 → 选中后弹出该专家的任务配置 → 确认新建任务。
   */
  const pickExpert = useCallback(async (m: AgentMode) => {
    if (isLoading) { toast('当前任务进行中，请稍候'); return; }
    /* 只记录「待用专家」，不动当前会话的绑定；确认后统一走 startTask 新建任务 */
    setPendingExpert(m);
    setView('chat');
    if (m === 'review') {
      /* 当前可能还停在面试模式，仓库列表没加载过：先取一次（弹框里的「已有仓库」快捷选择要用） */
      let list = repos;
      if (!list.length) {
        try {
          list = await fetchRepos();
          setRepos(list);
        } catch (_) { list = []; }
      }
      /* 新建任务：配置从空白开始，不回填上次/当前的仓库，让用户自己填 */
      setRepoEditing(null);
      setRepoOpen(true);
    } else {
      /* 新建任务：画像也从空白开始，不能继承当前会话或上一次的画像 */
      setSetupDraft(blankProfile());
      setSetupOpen(true);
    }
  }, [isLoading, repos, toast]);

  /* 对话头部：打开当前专家的配置弹框（不新建任务） */
  const openAgentConfig = useCallback(() => {
    setPendingExpert(null);
    /* 编辑已绑定任务的配置：这里才回填当前值 */
    setSetupDraft(null);
    if (mode === 'review') {
      setRepoEditing(repos.find(r => r.id === repoId) || null);
      setRepoOpen(true);
    } else {
      setSetupOpen(true);
    }
  }, [mode, repos, repoId]);

  /* ---------- 面试配置确认：确认即新建一个面试任务（会话与专家永久绑定） ---------- */
  const onAgentConfirm = useCallback(() => {
    if (!cfg.base || !cfg.model) {
      setGenErr('请先点击右上角齿轮图标完成「模型配置」');
      setCfgOpen(true);
      return;
    }
    /* 从专家中心进来的是空白表单：必填项没填完不能生成 */
    const p = setupDraft ?? profile;
    if (!p.track) { setGenErr('请先选择技术方向'); return; }
    if (!p.level) { setGenErr('请选择候选人级别'); return; }
    if (!p.exp) { setGenErr('请选择工作经验'); return; }
    if (!(p.count > 0)) { setGenErr('请填写题目数量（1-50 道）'); return; }
    setGenErr('');
    setSetupOpen(false);   // 生成后关掉弹框，把屏幕还给对话
    setSetupDraft(null);
    const m: AgentMode | null = pendingExpert || mode;
    setPendingExpert(null);
    if (!m) {              // 没有关联专家的会话不能直接生成任务
      setView('experts');
      return;
    }
    /* 新任务沿用这份画像：当前会话（或新建的任务会话）都以它为准 */
    setProfile(p);
    startTask(m, `面试任务 · ${p.track} ${p.level}`, buildGeneratePrompt(p), { profile: p });
  }, [cfg.base, cfg.model, pendingExpert, mode, profile, setupDraft, startTask]);

  /* ---------- 派生数据 ---------- */
  /** 当前会话绑定的专家（null = 还没关联） */
  const activeExpert = useMemo(() => expertOfMode(mode), [expertOfMode, mode]);
  /** 当前会话绑定的智能体（convo → agent → expert 中间那一层） */
  const activeAgent = useMemo(() => {
    if (!agentId) return null;
    for (const e of experts) {
      const hit = (e.agents || []).find(a => a.id === agentId);
      if (hit) return hit;
    }
    return null;
  }, [agentId, experts]);
  const currentRepo = useMemo(() => repos.find(r => r.id === repoId) || null, [repos, repoId]);
  /** 正在轮询的仓库数（顶部状态条用） */
  const pollingRepoCount = useMemo(() => repos.filter(r => r.polling).length, [repos]);

  /** 暂停 / 恢复当前仓库的自动轮询 */
  const handleTogglePolling = useCallback(async () => {
    if (!currentRepo || pollToggleBusy) return;
    setPollToggleBusy(true);
    try {
      const repo = await setRepoPolling(currentRepo.id, !currentRepo.enabled);
      setRepos(prev => prev.map(r => (r.id === repo.id ? { ...r, ...repo } : r)));
      toast(repo.enabled ? '✅ 已恢复自动轮询' : '已暂停自动轮询');
    } catch (e: any) {
      toast('❌ ' + (e.message || e));
    } finally {
      setPollToggleBusy(false);
    }
  }, [currentRepo, pollToggleBusy, toast]);
  /* 左侧会话列表保持完整：不按智能体过滤，切换时也不新建会话 */
  const visibleConvos = convos;

  /* 真正显示在屏幕上的消息（已清空的 id 会被过滤掉）。
     确认弹框里的「将删除 N 条」也用它，跟用户看到的条数对齐。 */
  const shownMessages = asChatMessages(messages)
    .filter(m => !clearedMsgIdsRef.current.has(String(m.id)));

  const indText = profile.industry === '不限' ? '行业不限' : profile.industry;
  const chatTitle = mode == null
    ? '未关联专家'
    : mode === 'review'
    ? (currentRepo ? `${currentRepo.owner}/${currentRepo.name}` : '代码审核官')
    : `${profile.track}${profile.sub && profile.sub !== '随机' ? ' / ' + profile.sub : ''} · ${profile.level} · ${profile.exp}`;
  const chatMeta = mode == null
    ? '这个会话还没有关联专家 · 从专家中心选一位即可开始'
    : mode === 'review'
    ? (currentRepo
        ? `${prs.length} 个开放 PR · 每 ${Math.round(currentRepo.intervalSec / 60) || 1} 分钟轮询 · ${currentRepo.hasToken ? '已配 Token' : '未配 Token'}`
        : '先填代码地址，再轮询 PR 自动审核')
    : (profile.resumeName
        ? `已加载简历：${profile.resumeName} · ${indText}`
        : `${profile.sub === '随机' ? '细分随机' : '细分：' + profile.sub} · ${indText} · 未上传简历`);

  /* 配置的唯一写入口：保存即写服务端全局配置（Node 内存 + Python setting 表），
   * 本地不再留副本，避免「浏览器旧配置覆盖服务端」这种事故。 */
  const onSaveCfg = (c: ModelCfg) => {
    setCfg(c);
    void (async () => {
      try {
        const res = await fetch('/api/model-config', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ base: c.base, key: c.key, model: c.model }),
        });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        toast('✅ 配置已保存（全局生效）');
      } catch (err: any) {
        toast('❌ 配置未能同步到服务端：' + (err?.message || err));
      }
    })();
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
        {/* 全局轮询指示灯：切到别的会话也知道后台还在跑审核 */}
        {pollingRepoCount > 0 && (
          <span className="top-poll" title={`${pollingRepoCount} 个仓库正在轮询 / 审核`}>
            <i className="poll-dot" aria-hidden="true" />
            {pollingRepoCount} 个仓库轮询中
          </span>
        )}
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
          onOpenExperts={() => setView(v => (v === 'experts' ? 'chat' : 'experts'))}
          expertsOpen={view === 'experts'}
          expertCount={experts.length}
          repos={repos}
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

        {view === 'experts' ? (
          <ExpertPicker
            experts={experts}
            mode={mode}
            agentId={agentId}
            skills={skills}
            onPick={pickExpert}
            onBack={() => setView('chat')}
          />
        ) : (
        <div className="chat-wrap">
          <div className="chat-head">
            <button
              type="button"
              className={'expert-chip' + (activeExpert ? '' : ' is-empty')}
              onClick={() => setView('experts')}
              title={activeExpert ? '更换专家 / 新建任务' : '选择一位专家开始任务'}
            >
              <span className="expert-chip-ico" aria-hidden="true">{activeExpert ? activeExpert.icon : '专'}</span>
              <span className="expert-chip-name">{activeExpert ? activeExpert.name : '未关联专家'}</span>
              {/* 会话 → 智能体：主智能体挂在专家名后面，两级关系一眼可见 */}
              {activeAgent && <span className="expert-chip-agent">· {activeAgent.name}</span>}
              <span className="expert-chip-arrow" aria-hidden="true">›</span>
            </button>
            <div className="chat-head-main">
              {mode === 'review' ? (
                <ReviewStatus
                  repos={repos}
                  repoId={repoId}
                  onPick={setRepoId}
                  polling={polling}
                  scheduler={scheduler}
                  onTogglePolling={handleTogglePolling}
                  toggleBusy={pollToggleBusy}
                  prCount={prs.length}
                  lastError={repoErr || currentRepo?.lastError || ''}
                />
              ) : (
                <>
                  <div className="title">{chatTitle}</div>
                  <div className="meta">{chatMeta}</div>
                </>
              )}
            </div>
            <div className="spacer"></div>
            {mode === 'review' && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={handlePoll}
                disabled={!currentRepo || polling}
              >
                {polling ? '轮询中…' : '立即轮询'}
              </button>
            )}
            {/* 未关联专家时没有「专家配置」可填，直接不显示 */}
            {mode && (
              <button className="btn btn-ghost btn-sm" onClick={openAgentConfig}>
                {mode === 'review' ? '仓库配置' : '面试配置'}
              </button>
            )}
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => setClearAsk(true)}
              disabled={!shownMessages.length && !isLoading}
              title={shownMessages.length || isLoading ? '清空当前会话的消息' : '当前没有可清空的消息'}
            >
              清空对话
            </button>
          </div>

          <AgentTrace trace={trace} active={isLoading} />

          <ChatPanel
            messages={shownMessages}
            isLoading={isLoading}
            onSend={send}
            onStop={stopGeneration}
            disabled={mode == null}
            threadKey={convoId}
            avatar={activeAgent?.icon || activeExpert?.icon || '专'}
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
            empty={mode == null ? (
              <div className="empty-state">
                <div className="big">专</div>
                <p>这个会话还没有关联专家</p>
                <p>会话与专家绑定后才会开始工作：选好专家、填完任务信息，直接沿用当前这个空会话</p>
                <button type="button" className="btn empty-cta" onClick={() => setView('experts')}>
                  去专家中心选择
                </button>
              </div>
            ) : mode === 'review' ? null : undefined}
            placeholder={mode == null
              ? '先选择一位专家…（点上面的「未关联专家」或左侧「专家中心」）'
              : mode === 'review'
              ? '针对这份审核报告追问，例如「第 2 个问题怎么改？」'
              : '输入消息…（Enter 发送，Shift+Enter 换行）'}
          />
        </div>
        )}
      </main>

      {/* 清空确认：说明里写清「删什么、留什么、能不能撤回」，避免只想清屏的人误删记录 */}
      <ConfirmModal
        open={clearAsk}
        danger
        title="清空当前对话？"
        confirmText="确认清空"
        cancelText="取消"
        onCancel={() => setClearAsk(false)}
        onConfirm={() => { setClearAsk(false); clearCurrent(); }}
      >
        <p>
          将删除当前会话的 <b>{shownMessages.length}</b> 条消息：界面、本地缓存、服务端记录一起清空，
          且<b>无法撤销</b>。
        </p>
        {isLoading && <p className="sub">当前正在生成回复，清空会先停止生成。</p>}
        <p className="sub">会话本身会保留，专家绑定与任务配置不受影响；想彻底删掉这条会话，请用左侧的删除。</p>
      </ConfirmModal>

      {/* 三个弹框：面试配置 / 仓库配置 / 模型配置（模型配置叠在最上层） */}
      <InterviewSetup
        open={setupOpen}
        onClose={() => { setSetupOpen(false); setSetupDraft(null); setPendingExpert(null); setGenErr(''); }}
        /* 新建任务用空白草稿；编辑已绑定任务时才是当前会话的画像 */
        profile={setupDraft ?? profile}
        onProfileChange={(p) => (setupDraft ? setSetupDraft(p) : setProfile(p))}
        onConfirm={onAgentConfirm}
        genErr={genErr}
        cfg={cfg}
        onNeedCfg={() => setCfgOpen(true)}
        toast={toast}
      />

      <RepoSetup
        open={repoOpen}
        onClose={() => { setRepoOpen(false); setPendingExpert(null); }}
        repo={repoEditing}
        repos={repos}
        /* 新建任务时可跳过填写，直接挑一个已有仓库开始 */
        onUseRepo={(r) => {
          setRepoOpen(false);
          setRepoId(r.id);
          if (pendingExpert === 'review') {
            setPendingExpert(null);
            startTask('review', `代码审核 · ${r.owner}/${r.name}`, undefined, { repoId: r.id });
            window.setTimeout(() => { void pollRepo(r.id).catch(() => {}); }, 0);
          }
        }}
        onSaved={(repo, isNew) => {
          setRepos(prev => (isNew ? [...prev, repo] : prev.map(r => (r.id === repo.id ? repo : r))));
          setRepoId(repo.id);
          setRepoEditing(repo);
          /* 从专家入口选了审核官时，保存仓库 = 确认任务：新建审核会话并立刻拉一次 PR */
          if (pendingExpert === 'review') {
            setPendingExpert(null);
            startTask('review', `代码审核 · ${repo.owner}/${repo.name}`, undefined, { repoId: repo.id });
            window.setTimeout(() => { void pollRepo(repo.id).catch(() => {}); }, 0);
          }
        }}
        onDeleted={(id) => {
          setRepos(prev => prev.filter(r => r.id !== id));
          setRepoId(prev => (prev === id ? '' : prev));
        }}
        toast={toast}
      />

      {/* 只在打开时挂载：全局配置是异步从服务端读回来的，常驻挂载会让弹框
          停在挂载那一刻的旧值（初始为空），看起来像「配置丢了」 */}
      {cfgOpen && (
        <ConfigModal open onClose={() => setCfgOpen(false)} cfg={cfg} onSave={onSaveCfg} toast={toast} />
      )}

      <div id="toast" className={toastMsg ? 'show' : ''}>{toastMsg}</div>
    </>
  );
}
