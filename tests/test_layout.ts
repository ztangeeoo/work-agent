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
ok(/if \(!el \|\| !stickRef\.current\) return;/.test(PANEL),
   '不贴底时不强制滚到底（用户往上翻不会被拽回）');
ok(/stickRef\.current = true/.test(PANEL), '发送消息时重新吸附到底部');
ok(!/useEffect\(\(\) => \{\s*const el = listRef\.current;\s*if \(el\) el\.scrollTop = el\.scrollHeight;\s*\}, \[messages, isLoading\]\)/.test(PANEL),
   '旧的「每次都强制滚底」逻辑已移除');

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
process.exit(fail ? 1 : 0);
