import type { AgentDef } from '../types';

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

/** 专家列表：从左侧「专家」入口进入后，在右侧选择
 *  icon 只作头像用的单字标记 —— 界面主题色单一，不再塞彩色 emoji */
export const AGENTS: AgentDef[] = [
  {
    id: 'interviewer', name: '面试出题官', icon: '面', mode: 'interview',
    desc: '按技术方向 / 级别 / 经验 / 行业背景 / 简历智能出题，支持追问与结构化评分复盘。',
    role: 'AI 技术面试官', tags: ['智能出题', '简历深挖', '评分复盘'], cta: '开始出题',
  },
  {
    id: 'reviewer', name: '代码审核官', icon: '审', mode: 'review',
    desc: '轮询仓库开放 PR，按正确性 / 安全 / 并发性能 / 可维护性 / 测试五维审核，给出风险等级与合入结论。',
    role: 'AI Code Reviewer', tags: ['PR 审核', '风险定级', '修复建议'], cta: '开始审核',
  },
];

export const DEFAULT_PROFILE = {
  track: '前端开发', sub: '随机', level: '中级', exp: '1-3年', industry: '不限',
  count: 5, lang: '中文', note: '', resumeText: '', resumeName: '',
};
