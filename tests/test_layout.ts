/**
 * 布局回归断言：防止聊天区滚动条再次失效。
 *
 * 背景：`.chat-list` 是 `.chat-wrap`（column flex）里的 `flex:1` 子项。
 * 若缺少 `min-height: 0`，flex 子项的最小高度会按内容撑开（min-height: auto），
 * 内容一长就把自己撑出父容器，再被 `main { overflow:hidden }` 裁掉，
 * 表现就是「滚动条能看到但拖不动」。
 *
 * 运行：node --experimental-strip-types tests/test_layout.ts
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const CSS = fs.readFileSync(path.join(ROOT, 'web/src/styles.css'), 'utf8');
const PANEL = fs.readFileSync(path.join(ROOT, 'web/src/components/ChatPanel.tsx'), 'utf8');

let pass = 0;
let fail = 0;
function ok(cond: boolean, name: string) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name); }
}

/** 取出某个选择器的规则体（只取第一条匹配，够用） */
function ruleBody(selector: string): string {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const m = CSS.match(new RegExp(esc + '\\s*\\{([^}]*)\\}'));
  return m ? m[1] : '';
}

console.log('\n[1] 滚动容器关键约束');
const list = ruleBody('.chat-list');
ok(/min-height\s*:\s*0/.test(list), '.chat-list 有 min-height: 0（不被内容撑开）');
ok(/overflow-y\s*:\s*auto/.test(list), '.chat-list 有 overflow-y: auto');
ok(/flex\s*:\s*1/.test(list), '.chat-list 有 flex: 1');

const wrap = CSS.match(/\.chat-wrap\s*\{([^}]*)\}/);
ok(!!wrap && /min-height\s*:\s*0/.test(wrap[1]), '.chat-wrap 有 min-height: 0');
ok(!!wrap && /overflow\s*:\s*hidden/.test(wrap[1]), '.chat-wrap 有 overflow: hidden');

const body = CSS.match(/(^|\n)\s*body\s*\{([^}]*)\}/);
ok(!!body && /overflow\s*:\s*hidden/.test(body[2]), 'body 有 overflow: hidden（整页不参与滚动）');

console.log('\n[2] 同级元素不挤压聊天区');
ok(/\.msg[^{]*\{[^}]*flex-shrink\s*:\s*0/.test(CSS), '.msg 有 flex-shrink: 0');
ok(/\.chat-input\s*\{[^}]*flex-shrink\s*:\s*0/.test(CSS), '.chat-input 有 flex-shrink: 0');
ok(/\.agent-trace\s*\{[^}]*flex-shrink\s*:\s*0/.test(CSS), '.agent-trace 有 flex-shrink: 0');
ok(/\.agent-trace\.is-open \.trace-body\s*\{[^}]*max-height/.test(CSS),
   '流水线展开时内部限高滚动（不压扁聊天区）');

console.log('\n[3] 滚动条视觉（Windows 上默认太窄）');
ok(/\.chat-list::-webkit-scrollbar[\s\S]{0,160}width\s*:\s*10px/.test(CSS), '滚动条宽度 10px');
ok(/::-webkit-scrollbar-thumb[\s\S]{0,80}border-radius/.test(CSS), '滚动条滑块有圆角样式');

console.log('\n[4] 自动滚底不打断用户拖动');
ok(/stickRef/.test(PANEL), 'ChatPanel 有 stickRef（贴底吸附状态）');
ok(/addEventListener\('scroll'/.test(PANEL), '监听用户滚动以更新吸附状态');
ok(/if \(!el \|\| !stickRef\.current \|\| !messages\.length\) return;/.test(PANEL),
   '不贴底时不强制滚到底（用户往上翻不会被拽回）');
ok(/!messages\.length\) return;/.test(PANEL),
   '空消息列表不滚到底（审核工作台不会被顶出视野）');
ok(/stickRef\.current = true/.test(PANEL), '发送消息时重新吸附到底部');
ok(!/useEffect\(\(\) => \{\s*const el = listRef\.current;\s*if \(el\) el\.scrollTop = el\.scrollHeight;\s*\}, \[messages, isLoading\]\)/.test(PANEL),
   '旧的「每次都强制滚底」逻辑已移除');

/* ---------------- 专家选择：左侧入口；输入框上方不再有切换条 ---------------- */
const APP = fs.readFileSync(path.join(ROOT, 'web/src/ChatApp.tsx'), 'utf8');
const PICKER = fs.readFileSync(path.join(ROOT, 'web/src/components/ExpertPicker.tsx'), 'utf8');

console.log('\n[5] 专家选择在左侧入口');
ok(/inputTop\?:\s*ReactNode/.test(PANEL), 'ChatPanel 提供 inputTop 插槽');
ok(/\{inputTop\}[\s\S]{0,200}className="chat-input"/.test(PANEL),
   'inputTop 渲染在 chat-list 之后、chat-input 之前');
ok(!/<AgentSwitcher/.test(APP), 'ChatApp 不再渲染已移除的 AgentSwitcher');
ok(/view === 'experts'/.test(APP) && /<ExpertPicker/.test(APP), '专家选择视图由左侧入口切换');
ok(!/\.agent-bar\s*\{/.test(CSS), '旧切换条样式已移除');

console.log('\n[5.1] 专家卡片展示已绑定的技能');
ok(/skills=\{skills\}/.test(APP), 'ChatApp 把技能绑定关系传给专家页');
ok(/expert-skills/.test(PICKER), '卡片上有技能区');
ok(/assignments\.experts\.find/.test(PICKER), '按专家 id 取该专家绑定的技能');
ok(/is-global/.test(PICKER), '全局技能与专属技能在标记上区分');
ok(/\.expert-skill\.is-global\s*\{[^}]*border-style\s*:\s*dashed/.test(CSS),
   '全局技能用虚线芯片（不靠颜色单独表意）');
ok(/assignments\.unassigned\.length/.test(PICKER), '未生效的技能在页面上有提示');

console.log('\n[6] 配置走弹框（不再内联在对话流里）');
const SETUP = fs.readFileSync(path.join(ROOT, 'web/src/components/InterviewSetup.tsx'), 'utf8');
const REPO = fs.readFileSync(path.join(ROOT, 'web/src/components/RepoSetup.tsx'), 'utf8');
ok(!/footer\?:\s*ReactNode/.test(PANEL), 'ChatPanel 不再提供 footer 内联插槽');
ok(!/\{footer\}/.test(PANEL), '消息列表末尾不再渲染内联配置卡');
ok(!/setup-card/.test(CSS), '旧的内联卡样式 .setup-card 已移除');
ok(/<InterviewSetup/.test(APP) && /<RepoSetup/.test(APP), '面试/仓库配置以组件形式挂在页面上');
ok(/modal-mask show/.test(SETUP) && /modal-mask show/.test(REPO), '两个配置组件都渲染可见的弹框遮罩');
ok(/if \(e\.target === e\.currentTarget\) onClose\(\)/.test(SETUP)
  && /if \(e\.target === e\.currentTarget\) onClose\(\)/.test(REPO), '点遮罩关闭弹框');
ok(/e\.key === 'Escape'/.test(SETUP) && /e\.key === 'Escape'/.test(REPO), '支持 Esc 关闭弹框');
ok(/modal-tall/.test(SETUP) && /\.modal-tall\s*\{[^}]*max-height/.test(CSS),
   '面试弹框限高（长表单内部滚动，不撑破屏幕）');
ok(/modal-wide/.test(REPO), '仓库配置弹框用宽版');
ok(/\.modal-foot\s*\{[^}]*flex-shrink\s*:\s*0/.test(CSS), '操作区固定在弹框底部（不随表单滚动）');
ok(/modal-foot/.test(SETUP) && /modal-foot/.test(REPO), '两个配置弹框都用了固定操作区');
ok(/#cfgModal\s*\{[^}]*z-index/.test(CSS) && /id="cfgModal"/.test(
  fs.readFileSync(path.join(ROOT, 'web/src/components/ConfigModal.tsx'), 'utf8')),
   '模型配置弹框叠在配置弹框之上');
ok(/const \[setupOpen, setSetupOpen\] = useState\(false\)/.test(APP), '面试配置弹框默认关闭（不占屏幕）');
ok(/const \[repoOpen, setRepoOpen\] = useState\(false\)/.test(APP), '仓库配置弹框默认关闭');
ok(!/if \(mode === 'interview'\) setSetupOpen\(true\)/.test(APP), '新建会话不再自动弹出配置');
ok(/if \(m === 'review'\)[\s\S]{0,400}?setRepoOpen\(true\)/.test(APP),
   '选中审核官即打开仓库配置（没配过时是空表单引导）');
ok(/openAgentConfig[\s\S]{0,300}?setSetupOpen\(true\)/.test(APP), '对话头部配置入口打开弹框');
ok(/setSetupOpen\(false\);   \/\/ 生成后关掉弹框/.test(APP), '生成面试题后自动关闭弹框');

console.log('\n[7] 切换智能体不影响左侧会话列表');
ok(/const visibleConvos = convos;/.test(APP), '侧栏使用完整会话列表（不按智能体过滤）');
ok(!/list:\s*convos\.list\.filter\(c => modeOf/.test(APP), '不再按模式过滤侧栏');
ok(/const pickExpert[\s\S]{0,700}?不动当前会话的绑定/.test(APP),
   'pickExpert 只记录待用专家，不动当前会话绑定');
const switchBody = (APP.match(/const switchMode = useCallback\(\(m: AgentMode\) => \{([\s\S]*?)\n  \}, \[/) || ['', ''])[1];
ok(!/newConvo\(|activeId:/.test(switchBody), '切换智能体时不新建/不切换会话');

console.log('\n[8] 新建会话不带上一条消息（reset 留下空占位）');
ok(/const toSavable = [\s\S]{0,220}filter\(m => m\.content\.trim\(\)\.length > 0\)/.test(APP),
   '落盘前过滤空内容消息（toSavable）');
ok(/(?:const|let) snapshot = toSavable\(messages\)/.test(APP), 'saveCurrent 使用过滤后的快照');
ok(/const shown = messages\.filter\(m => m\.role === 'user' \|\| String\(m\.content \?\? ''\)\.trim\(\)\.length > 0\)/.test(PANEL),
   '渲染时跳过空的 AI 占位消息（不出现空气泡）');
ok(/\{shown\.map\(/.test(PANEL) && !/\{messages\.map\(/.test(PANEL), '消息列表改用 shown 渲染');
ok(/\{shown\.length === 0 &&/.test(PANEL), '空状态与打字指示改用 shown 判断');
ok(/if \(active && \(active\.messages \|\| \[\]\)\.length\) queueSaveConvo\(active\)/.test(APP),
   '空会话不推服务端（避免攒一堆「新会话 · 0 条消息」）');
ok(/\.filter\(c => \(c\.messages \|\| \[\]\)\.length \|\| \(c\.mode && c\.title && c\.title !== '新会话'\)\)/.test(APP),
   '启动合并时不上推空壳会话，但已绑定专家的任务会话要推');
ok(!/if \(mode === 'interview'\) setSetupOpen\(true\)/.test(APP), '新建会话不弹配置框（不打断对话）');

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
process.exit(fail ? 1 : 0);
