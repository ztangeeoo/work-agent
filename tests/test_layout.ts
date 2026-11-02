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

/* ---------------- 新增：智能体切换位置 / 内联配置 / 左侧不变 ---------------- */
const APP = fs.readFileSync(path.join(ROOT, 'web/src/ChatApp.tsx'), 'utf8');
const SWITCHER = fs.readFileSync(path.join(ROOT, 'web/src/components/AgentSwitcher.tsx'), 'utf8');

console.log('\n[5] 两个智能体放在输入框上方');
ok(/inputTop\?:\s*ReactNode/.test(PANEL), 'ChatPanel 提供 inputTop 插槽');
ok(/\{inputTop\}[\s\S]{0,200}className="chat-input"/.test(PANEL),
   'inputTop 渲染在 chat-list 之后、chat-input 之前');
ok(/inputTop=\{/.test(APP) && /<AgentSwitcher/.test(APP), 'ChatApp 通过 inputTop 传入 AgentSwitcher');
ok(!/<ChatPanel[\s\S]*?\/>\s*<AgentSwitcher/.test(APP), '不再把智能体选择器放在聊天框下方');
ok(/\.agent-bar\s*\{[^}]*border-top/.test(CSS), '切换条与聊天区之间有分隔线');
ok(/\.agent-tab\.active/.test(CSS), '切换条有选中态样式');

console.log('\n[6] 配置在聊天框内联出现（不是弹框）');
ok(/footer\?:\s*ReactNode/.test(PANEL), 'ChatPanel 提供 footer 插槽（内联配置卡）');
ok(/\{footer\}/.test(PANEL), 'footer 渲染在消息列表末尾（紧贴输入框上方）');
ok(/<InterviewSetup/.test(APP), '面试配置用内联组件 InterviewSetup');
ok(/<RepoSetup/.test(APP), '仓库配置用内联组件 RepoSetup');
ok(!/<AgentModal/.test(APP) && !/<RepoModal/.test(APP), '不再使用配置弹框');
ok(!/modal-mask/.test(fs.readFileSync(path.join(ROOT, 'web/src/components/InterviewSetup.tsx'), 'utf8')),
   '面试配置卡里没有弹框遮罩');
ok(!/modal-mask/.test(fs.readFileSync(path.join(ROOT, 'web/src/components/RepoSetup.tsx'), 'utf8')),
   '仓库配置卡里没有弹框遮罩');
ok(/\.setup-card\s*\{[^}]*border-left/.test(CSS), '配置卡有左侧色条（在对话流中可辨识）');
ok(/setup-actions/.test(CSS), '配置卡有独立操作区');

console.log('\n[7] 切换智能体不影响左侧会话列表');
ok(/const visibleConvos = convos;/.test(APP), '侧栏使用完整会话列表（不按智能体过滤）');
ok(!/list:\s*convos\.list\.filter\(c => modeOf/.test(APP), '不再按模式过滤侧栏');
ok(/const switchMode[\s\S]{0,900}?只切换|不新建、不切换、不过滤左侧会话/.test(APP),
   'switchMode 注释明确不改会话');
const switchBody = (APP.match(/const switchMode = useCallback\(\(m: AgentMode\) => \{([\s\S]*?)\n  \}, \[/) || ['', ''])[1];
ok(!/newConvo\(|activeId:/.test(switchBody), '切换智能体时不新建/不切换会话');

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
process.exit(fail ? 1 : 0);
