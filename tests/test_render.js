/* tests/test_render.js — 用 Node vm 加载 index.html 内嵌脚本，mock DOM，验证「题目卡片化」渲染 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const m = html.match(/<script>([\s\S]*?)<\/script>/);
if(!m){ console.error('未找到内嵌 script'); process.exit(1); }

let passed = 0, failed = 0;
function ok(cond, name){
  if(cond){ passed++; console.log('  \u2713 ' + name); }
  else { failed++; console.log('  \u2717 ' + name); }
}

/* ---- DOM mock ---- */
function makeEl(){
  const el = {
    innerHTML: '', textContent: '', value: '', style: {}, dataset: {},
    children: [], files: [], _qs: {},
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    appendChild(c){ this.children.push(c); return c; },
    removeChild(c){ const i = this.children.indexOf(c); if(i>=0) this.children.splice(i,1); return c; },
    remove(){},
    querySelector(sel){ return this._qs[sel] || (this._qs[sel] = makeEl()); },
    querySelectorAll(){ return []; },
    addEventListener(){}, removeEventListener(){},
    setAttribute(){}, getAttribute(){ return null; },
    insertAdjacentHTML(){}, focus(){}, click(){}, scrollIntoView(){},
    scrollTop: 0, scrollHeight: 0, disabled: false, checked: false,
    getContext(){ return null; }
  };
  return el;
}
const els = {};
const sandbox = {
  console,
  setTimeout, clearTimeout,
  setInterval: ()=>0, clearInterval: ()=>{},
  document: {
    getElementById(id){ return els[id] || (els[id] = makeEl()); },
    createElement(){ return makeEl(); },
    querySelector(){ return makeEl(); },
    querySelectorAll(){ return []; },
    addEventListener(){},
    body: makeEl()
  },
  navigator: { clipboard: null, userAgent: 'node' },
  location: { origin: 'http://localhost:8787', hostname: 'localhost', href: 'http://localhost:8787/' },
  fetch: async () => ({ ok: true, status: 200, json: async () => ({ ok: true, agentOk: true, models: [] }) }),
  URL: { createObjectURL: ()=>'blob:x', revokeObjectURL(){} },
  alert(){}
};
sandbox.window = sandbox;
const lsStore = {};
sandbox.localStorage = {
  getItem: k => (k in lsStore ? lsStore[k] : null),
  setItem: (k, v) => { lsStore[k] = String(v); },
  removeItem: k => { delete lsStore[k]; }
};
vm.createContext(sandbox);
vm.runInContext(m[1], sandbox);

const { mdRich, mdRichStreaming, splitQuestions, renderAiInto, renderMsg } = sandbox;
for(const [n, f] of Object.entries({ mdRich, mdRichStreaming, splitQuestions, renderAiInto, renderMsg })){
  if(typeof f !== 'function'){ console.error('缺少函数: ' + n); process.exit(1); }
}

console.log('\u2014 mdRich \u2014');
ok(mdRich('**加粗**').includes('<b>加粗</b>'), '加粗转换');
ok(mdRich('使用 `List` 类型').includes('<code>List</code>'), '行内代码');
ok(mdRich('- 甲\n- 乙').includes('<ul><li>甲</li><li>乙</li></ul>'), '无序列表');
ok(mdRich('1. 甲\n2. 乙').includes('<ol><li>甲</li><li>乙</li></ol>'), '有序列表');
const tbl = '| 维度 | 得分 |\n| --- | --- |\n| 基础 | 8 |';
ok(mdRich(tbl).includes('<table>') && mdRich(tbl).includes('<th>维度</th>') && mdRich(tbl).includes('<td>8</td>'), '表格（含表头）');
ok(mdRich('```java\nint a = 1;\n```').includes('code-block') && mdRich('```java\nint a = 1;\n```').includes('>java<'), '代码块（含语言标签）');
ok(!mdRich('<script>alert(1)</script>').includes('<script>'), 'XSS 转义');
ok(mdRich('> 引用一句').includes('<blockquote>'), '引用块');
ok(mdRich('### 小节标题').includes('<h4>小节标题</h4>'), '标题');

console.log('\u2014 mdRichStreaming \u2014');
ok(mdRichStreaming('前文\n```py\nx=1').includes('code-block'), '未闭合代码块降级渲染');
ok(mdRichStreaming('普通内容').includes('普通内容'), '普通内容直出');

console.log('\u2014 splitQuestions \u2014');
const q3 = '开场白\n\n**第 1 题：进程与线程**\n请解释进程与线程的区别。\n\n**第 2 题：索引优化**\nMySQL 索引什么时候失效？\n\n**第 3 题：场景设计**\n设计一个秒杀系统。';
const segs = splitQuestions(q3);
ok(segs.length === 4, '拆出 4 段（开场 + 3 题），实际 ' + segs.length);
ok(segs.filter(s=>s.no).length === 3, '其中 3 个题目段');
ok(segs[1].no === 1 && segs[1].head === '进程与线程', '第 1 题标题提取: ' + segs[1].head);
ok(splitQuestions('好的，我们来开始面试。第一题请听好。').every(s=>!s.no), '无编号文本不拆分');
ok(splitQuestions('第 1 题\nQ2. 手写快排\n问题 3').filter(s=>s.no).length === 3, '兼容 QN/问题N 写法');

console.log('\u2014 renderAiInto \u2014');
const bubble = { innerHTML: '', querySelectorAll: ()=>[] };
renderAiInto(bubble, q3);
ok((bubble.innerHTML.match(/class="q-card"/g) || []).length === 3, '3 张题目卡片，实际 ' + (bubble.innerHTML.match(/class="q-card"/g) || []).length);
ok(bubble.innerHTML.includes('Q1') && bubble.innerHTML.includes('Q3'), '题号徽标 Q1/Q3');
ok(bubble.innerHTML.includes('ai-plain'), '开场白作为普通段保留');

const withTags = '**第 1 题：缓存穿透**\n描述场景。\n【考察点】缓存\n追问：如果缓存击穿呢？';
const b2 = { innerHTML:'', querySelectorAll: ()=>[] };
renderAiInto(b2, withTags);
ok(b2.innerHTML.includes('q-tag') && b2.innerHTML.includes('考察点'), '【考察点】标签');
ok(b2.innerHTML.includes('q-followup'), '追问高亮');

const b3 = { innerHTML:'', querySelectorAll: ()=>[] };
renderAiInto(b3, '好的，我们直接开始。');
ok(!b3.innerHTML.includes('q-card'), '普通消息不卡片化');

const b4 = { innerHTML:'', querySelectorAll: ()=>[] };
renderAiInto(b4, '**第 1 题：手撕代码**\n```js\nconst a = 1;\n```\n请解释。');
ok(b4.innerHTML.includes('q-card') && b4.innerHTML.includes('code-block'), '卡片内含代码块');

const b5 = { innerHTML:'', querySelectorAll: ()=>[] };
renderAiInto(b5, '**第 1 题：难度自适应**\n内容\n难度：中等');
ok(b5.innerHTML.includes('难度·中等'), '难度标签');

console.log('\u2014 renderMsg \u2014');
const bub = renderMsg('user', '你好 <b>');
ok(bub.innerHTML.includes('&lt;b&gt;'), '用户消息转义');
const bub2 = renderMsg('assistant', '**第 1 题：x**\n内容');
ok(bub2.innerHTML.includes('q-card'), 'AI 消息经 renderMsg 卡片化');

console.log('\n结果：' + passed + ' 通过 / ' + failed + ' 失败');
process.exit(failed ? 1 : 0);
