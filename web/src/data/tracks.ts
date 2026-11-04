import type { ExpertDef } from '../types';

export const TRACK_SUBS: Record<string, string[]> = {
  '前端开发': ['JavaScript/TS', 'React', 'Vue', '浏览器与网络', 'CSS/布局', '工程化构建', '性能优化'],
  '后端开发': ['Java/JVM', 'Go', 'Python', 'Node.js', '数据库与SQL', '缓存与消息队列', '微服务与分布式', '高并发设计', 'API 设计'],
  '全栈': ['前后端综合', '系统设计', '数据库设计', '部署与运维'],
  '算法': ['数据结构', '动态规划', '图论', '字符串', '排序与查找', '复杂度分析'],
  '大数据': ['Hadoop/Spark', 'Flink 实时计算', 'Hive/数仓建模', 'Kafka', '数据治理'],
  '测试': ['自动化测试', '接口测试', '性能测试', '测试框架', '质量体系'],
  '运维/DevOps': ['Linux', 'Docker/K8s', 'CI/CD', '监控告警', '云原生', 'Shell 脚本'],
  '移动端': ['Android', 'iOS', 'Flutter', 'React Native', '移动性能优化'],
  'AI/机器学习': ['深度学习', 'NLP', 'CV', 'LLM/RAG', '模型部署', '特征工程'],
  '安全': ['Web 安全', '渗透测试', '密码学', '应急响应', '代码审计'],
  '架构师': ['分布式架构', '高可用设计', '领域建模', '技术选型', '容量与性能']
};
export const TRACKS = Object.keys(TRACK_SUBS);
export const LEVELS = ['初级', '中级', '高级', '专家'];
export const EXPS = ['应届/实习', '1-3年', '3-5年', '5-10年', '10年以上'];
export const INDUSTRIES = ['不限', '互联网/电商', '金融/银行', '证券/基金', '游戏', '在线教育', '医疗健康', '智能制造', '汽车/自动驾驶', '云计算/SaaS', '社交/内容', '物流/供应链', '政务/国企', '通信/运营商'];

/** 专家清单（内置兜底）：正式数据在服务端 SQLite 的 expert / agent 两张表里，
 *  启动时会用 /api/experts 的返回覆盖这里；库不可用 / 接口失败时用这份常量。
 *  关系：专家 → 多个智能体（is_primary=1 的是会话默认绑定的主智能体）。
 *  icon 只作头像用的单字标记 —— 界面主题色单一，不再塞彩色 emoji */
export const AGENTS: ExpertDef[] = [
  {
    id: 'interviewer', name: '面试出题官', icon: '面', mode: 'interview',
    desc: '按技术方向 / 级别 / 经验 / 行业背景 / 简历智能出题，支持追问与结构化评分复盘。',
    role: 'AI 技术面试官', tags: ['智能出题', '简历深挖', '评分复盘'], cta: '开始出题',
    agents: [
      { id: 'interviewer.planner', expertId: 'interviewer', stage: 'planner', name: '策划官', icon: '谋', role: '首次出题前规划考察大纲', primary: false, sort: 1 },
      { id: 'interviewer.interviewer', expertId: 'interviewer', stage: 'interviewer', name: '面试官', icon: '面', role: '出题 / 追问 / 讲解（主智能体）', primary: true, sort: 2 },
      { id: 'interviewer.grader', expertId: 'interviewer', stage: 'grader', name: '评分官', icon: '评', role: '对候选人回答做结构化评分与复盘', primary: false, sort: 3 },
    ],
  },
  {
    id: 'reviewer', name: '代码审核官', icon: '审', mode: 'review',
    desc: '轮询仓库开放 PR，按正确性 / 安全 / 并发性能 / 可维护性 / 测试五维审核，给出风险等级与合入结论。',
    role: 'AI Code Reviewer', tags: ['PR 审核', '风险定级', '修复建议'], cta: '开始审核',
    agents: [
      { id: 'reviewer.scout', expertId: 'reviewer', stage: 'scout', name: '侦察官', icon: '侦', role: '拉取 PR、解析 diff 与上下文', primary: false, sort: 1 },
      { id: 'reviewer.reviewer', expertId: 'reviewer', stage: 'reviewer', name: '审核官', icon: '审', role: '五维审核、定风险等级与合入结论（主智能体）', primary: true, sort: 2 },
      { id: 'reviewer.judge', expertId: 'reviewer', stage: 'judge', name: '裁决官', icon: '裁', role: '汇总各维度结论并给出最终裁决', primary: false, sort: 3 },
      { id: 'reviewer.advisor', expertId: 'reviewer', stage: 'advisor', name: '审核答疑', icon: '答', role: '基于审核报告回答追问', primary: false, sort: 4 },
    ],
  },
];

export const DEFAULT_PROFILE = {
  track: '前端开发', sub: '随机', level: '中级', exp: '1-3年', industry: '不限',
  count: 5, lang: '中文', note: '', resumeText: '', resumeName: '',
};
