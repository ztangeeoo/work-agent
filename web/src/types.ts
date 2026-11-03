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
  /** 这场会话属于哪个智能体；老数据没有该字段时按面试模式处理 */
  mode?: AgentMode;
  /** 审核会话绑定的仓库 id（专家配置的一部分，切回该会话时恢复） */
  repoId?: string;
}

/** 智能体工作模式：面试出题 / 代码审核 */
export type AgentMode = 'interview' | 'review';

export interface AgentDef {
  id: string;
  name: string;
  icon: string;
  desc: string;
  mode: AgentMode;
  /** 专家卡片上的角色副标题 */
  role?: string;
  /** 专家卡片上的能力标签 */
  tags?: string[];
  /** 专家卡片主按钮文案 */
  cta?: string;
}

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
