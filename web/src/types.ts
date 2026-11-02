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
}

export interface AgentDef {
  id: string;
  name: string;
  icon: string;
  desc: string;
}
