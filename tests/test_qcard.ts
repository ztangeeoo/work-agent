/**
 * 题目卡片布局 / 历史恢复守卫 回归断言
 *
 * 背景：
 * 1. 卡片模式必须让气泡退化成透明容器，否则卡片被包在一个大白气泡里，视觉上不像「一题一卡」；
 * 2. 流式时必须按块渲染并保持 key 稳定，否则每帧重建整段 innerHTML，已完成卡片会闪烁、选中丢失；
 * 3. ChatApp.saveCurrent 必须挡住「空快照」，否则挂载/reset() 期间 messages 短暂为 [] 会把历史覆盖成空。
 *
 * 运行：node --experimental-strip-types tests/test_qcard.ts
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const CSS = fs.readFileSync(path.join(ROOT, 'web/src/styles.css'), 'utf8');
const PANEL = fs.readFileSync(path.join(ROOT, 'web/src/components/ChatPanel.tsx'), 'utf8');
const APP = fs.readFileSync(path.join(ROOT, 'web/src/ChatApp.tsx'), 'utf8');

let pass = 0;
let fail = 0;
function ok(cond: boolean, name: string) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name); }
}

console.log('\n[1] 卡片模式：气泡退化成透明容器');
ok(/\.msg\.ai \.bubble\.bubble-qcards\s*\{[^}]*background\s*:\s*none/.test(CSS), '卡片模式去掉气泡背景');
ok(/\.msg\.ai \.bubble\.bubble-qcards\s*\{[^}]*border\s*:\s*none/.test(CSS), '卡片模式去掉气泡边框');
ok(/\.msg\.ai \.bubble\.bubble-qcards\s*\{[^}]*white-space\s*:\s*normal/.test(CSS),
  '卡片模式覆盖 pre-wrap（否则 HTML 换行被当文本保留，排版炸开）');
ok(/\.msg\.ai \.bubble\.bubble-qcards\s*\{[^}]*padding\s*:\s*0/.test(CSS), '卡片模式去掉内边距');

console.log('\n[2] 一题一卡的视觉分层');
ok(/\.q-card\s*\{[^}]*border-left\s*:\s*3px solid var\(--qa/.test(CSS), '卡片有按题型着色的左边条');
['t-blue', 't-teal', 't-purple', 't-orange', 't-red', 't-gray'].forEach(t => {
  ok(new RegExp('\\.q-card\\.' + t + '\\s*\\{\\s*--qa\\s*:').test(CSS), '题型色条：' + t);
});
ok(/\.q-no\s*\{[^}]*background\s*:\s*var\(--qa/.test(CSS), '题号徽标跟随题型色');
ok(/\.q-card\.is-live/.test(CSS), '进行中卡片有独立样式');
ok(/\.q-live-dot[\s\S]{0,120}animation/.test(CSS), '进行中卡片有脉冲点');
ok(/\.bubble\.is-streaming \.q-card\s*\{\s*animation\s*:\s*none/.test(CSS), '流式中不重播入场动画（防闪烁）');
ok(/\.ai-plain \+ \.ai-block\s*\{\s*margin-top/.test(CSS), '大纲段落与卡片之间有间距');
ok(/\.q-focus\s*\{/.test(CSS), '考察点说明条样式存在');

console.log('\n[3] 流式渲染：按块输出，key 稳定');
ok(/import \{[^}]*aiBlocks[^}]*\} from '\.\.\/lib\/markdown'/.test(PANEL), 'ChatPanel 使用 aiBlocks 按块渲染');
ok(/key=\{b\.key\}/.test(PANEL), '每个渲染块用稳定 key（已完成卡片不重建）');
ok(!/dangerouslySetInnerHTML=\{\{ __html: html \}\}/.test(PANEL), '不再整段 innerHTML 一次性替换');
ok(/b\.live/.test(PANEL), '进行中块被标记');
ok(/closest\('\.code-copy'\)/.test(PANEL), '代码块复制按钮的事件委托仍在');

console.log('\n[4] 历史不被空快照覆盖');
ok(/if \(!snapshot\.length && cur && \(cur\.messages \|\| \[\]\)\.length\) return prev;/.test(APP),
  'saveCurrent 挡住空快照（防挂载/reset 竞争清空历史）');
ok(/\[\s*synced\s*,\s*convos\.activeId\s*,\s*convos\.list\s*,\s*messages\.length\s*\]/.test(APP),
  '历史补回 effect 依赖 messages.length（reset 后可自愈）');
ok(/const clearCurrent = useCallback/.test(APP), '有显式 clearCurrent（否则守卫会挡住「清空对话」）');
ok(/onClick=\{clearCurrent\}/.test(APP), '「清空对话」按钮走 clearCurrent');
ok(/messages: \[\], updatedAt: Date\.now\(\)/.test(APP), 'clearCurrent 先把会话历史置空再清消息');

console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
process.exit(fail ? 1 : 0);
