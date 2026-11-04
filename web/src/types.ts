/* 全局类型定义 */

export type Role = 'user' | 'assistant';

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
}

export interface ModelCfg {
  base: string;
  key: string;
  model: string;
}

/** 面试画像：传给 AgentScope 三智能体的结构化配置 */
export interface Profile {
  track: string;
  sub: string;
  level: string;
  exp: string;
  industry: string;
  count: number;
  lang: string;
  note: string;
  resumeText: string;
  resumeName: string;
}

export interface Convo {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messages: ChatMessage[];
  profile: Profile | null;
  /** 会话绑定的智能体 id（会话 → 智能体 → 专家，服务端落库）；空 = 未关联 */
  agentId?: string;
  /** 以下三项由服务端 JOIN 返回，仅供展示，不参与路由 */
  agentName?: string;
  agentStage?: string;
  expertId?: string;
  expertName?: string;
  expertIcon?: string;
  /** 专家所属能力包的路由键；老数据没有该字段时按未绑定处理 */
  mode?: AgentMode;
  /** 审核会话绑定的仓库 id（专家配置的一部分，切回该会话时恢复） */
  repoId?: string;
}

/** 智能体工作模式：面试出题 / 代码审核 */
export type AgentMode = 'interview' | 'review';

/** 智能体：专家下挂的流水线角色（策划官 / 面试官 / 评分官 / 侦察官 / 审核官…） */
export interface AgentInfo {
  id: string;
  expertId: string;
  /** 流水线环节键：planner / interviewer / grader / scout / reviewer / judge / advisor */
  stage: string;
  name: string;
  icon: string;
  role: string;
  /** 是否该专家的主智能体（新会话默认绑定它） */
  primary: boolean;
  sort?: number;
}

/** 专家：对外的能力包，一个专家下挂多个智能体 */
export interface ExpertDef {
  id: string;
  mode: AgentMode;
  name: string;
  icon: string;
  /** 卡片上的角色副标题 */
  role?: string;
  desc: string;
  /** 能力标签 */
  tags?: string[];
  /** 卡片主按钮文案 */
  cta?: string;
  agents: AgentInfo[];
}

/** 兼容旧命名：专家卡片以前叫 AgentDef */
export type AgentDef = ExpertDef;

/* ---------- 代码审核 ---------- */

export interface Repo {
  id: string;
  url: string;
  provider: 'github' | 'gitlab' | 'gitee';
  host: string;
  owner: string;
  name: string;
  intervalSec: number;
  autoPost: boolean;
  enabled: boolean;
  lastPollAt: number;
  lastError: string;
  hasToken: boolean;
  openPrs?: number;
  reviewCount?: number;
}

export interface PRItem {
  repoId: string;
  number: number;
  title: string;
  author: string;
  url: string;
  baseBranch: string;
  headBranch: string;
  headSha: string;
  state: string;
  draft: boolean;
  updatedAt: number;
  review: { id: string; risk: string; decision: string; issueCount: number; createdAt: number } | null;
}

export interface ReviewIssue {
  severity: string;
  file: string;
  line: number;
  title: string;
  detail: string;
  suggestion: string;
}

export interface ReviewStage {
  stage: string;
  label: string;
  icon: string;
  ms?: number;
  status?: string;
  detail?: string;
}

export interface ReviewItem {
  id: string;
  repoId: string;
  repoUrl: string;
  owner: string;
  name: string;
  prNumber: number;
  headSha: string;
  title: string;
  author: string;
  prUrl: string;
  risk: string;
  decision: string;
  summary: string;
  reportMd: string;
  issues: ReviewIssue[];
  issueCount: number;
  stages: ReviewStage[];
  model: string;
  posted: boolean;
  postError: string;
  createdAt: number;
}

/** 审核事件（SSE 推送） */
export interface ReviewEvent {
  type: 'poll-start' | 'poll-done' | 'poll-error' | 'review-start'
      | 'review-done' | 'review-error' | 'review-posted';
  repoId: string;
  prNumber?: number;
  title?: string;
  count?: number;
  error?: string;
  review?: ReviewItem;
  ts: number;
}
