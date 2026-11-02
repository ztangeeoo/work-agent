/* tests/test_web.ts — React 版 markdown 渲染 / 题目卡片逻辑回归（Node 22 直接跑 TS） */
import { mdRich, mdRichStreaming, renderAiHtml, renderAiHtmlStreaming, splitQuestions, aiBlocks } from '../web/src/lib/markdown.ts';

let passed = 0, failed = 0;
function ok(cond: boolean, name: string) {
  if (cond) { passed++; console.log('  ✓ ' + name); }
  else { failed++; console.log('  ✗ ' + name); }
}

console.log('— mdRich —');
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

console.log('— mdRichStreaming —');
ok(mdRichStreaming('前文\n```py\nx=1').includes('code-block'), '未闭合代码块降级渲染');
ok(mdRichStreaming('普通内容').includes('普通内容'), '普通内容直出');

console.log('— splitQuestions —');
const q3 = '开场白\n\n**第 1 题：进程与线程**\n请解释进程与线程的区别。\n\n**第 2 题：索引优化**\nMySQL 索引什么时候失效？\n\n**第 3 题：场景设计**\n设计一个秒杀系统。';
const segs = splitQuestions(q3);
ok(segs.length === 4, '拆出 4 段（开场 + 3 题），实际 ' + segs.length);
ok(segs.filter(s => s.no).length === 3, '其中 3 个题目段');
ok(segs[1].no === 1 && segs[1].head === '进程与线程', '第 1 题标题提取: ' + segs[1].head);
ok(splitQuestions('好的，我们来开始面试。第一题请听好。').every(s => !s.no), '无编号文本不拆分');
ok(splitQuestions('第 1 题\nQ2. 手写快排\n问题 3').filter(s => s.no).length === 3, '兼容 QN/问题N 写法');

console.log('— renderAiHtml —');
const qCardRe = /class="q-card[ "]/g;
ok((renderAiHtml(q3).match(qCardRe) || []).length === 3, '3 张题目卡片');
ok(renderAiHtml(q3).includes('Q1') && renderAiHtml(q3).includes('Q3'), '题号徽标 Q1/Q3');
ok(renderAiHtml(q3).includes('ai-plain'), '开场白作为普通段保留');
const withTags = '**第 1 题：缓存穿透**\n描述场景。\n【考察点】缓存\n追问：如果缓存击穿呢？';
ok(/class="q-focus"[\s\S]*?缓存/.test(renderAiHtml(withTags)), '【考察点】抽成底部说明条');
ok(!/q-tag[^>]*>考察点</.test(renderAiHtml(withTags)), '考察点不再占用头部标签位');
ok(renderAiHtml('**第 1 题：标签测试**\n【场景设计】正文\n【考察点】会说清取舍').includes('q-tag t-teal'), '正文内非考察点标签仍当题型标签');
ok(renderAiHtml(withTags).includes('q-followup'), '追问高亮');
ok(!renderAiHtml('好的，我们直接开始。').includes('q-card'), '普通消息不卡片化');
ok(renderAiHtml('**第 1 题：手撕代码**\n```js\nconst a = 1;\n```\n请解释。').includes('q-card'), '卡片内含代码块');
ok(renderAiHtml('**第 1 题：难度自适应**\n内容\n难度：中等').includes('难度·中等'), '难度标签');

/* ---- 真实出题格式：agent_server.py 的 prompt 要求 `### 第 N 题【题型】` ---- */
console.log('— 真实后端格式（### 第 N 题【题型】）—');
const real = `🧭 已完成大纲规划

### 考察大纲
1. 并发基础（高）
2. 缓存设计（高）

---

### 第 1 题【基础概念】
某支付服务使用 \`ThreadPoolExecutor\`，核心 8 / 最大 16 / 队列 1000。

请回答：
1. 高并发下会怎样？
2. 改成无界队列呢？

【考察点】是否真正理解线程池参数。

---

### 第 2 题【场景设计】
设计一个抢购活动的库存扣减方案。

---

### 第 3 题【排查定位】
线上接口 P99 毛刺，你怎么查？

【考察点】排障方法论。`;
const realSegs = splitQuestions(real);
ok(realSegs.filter(s => s.no).length === 3, '### 标题行可被拆出（关键修复），实际 ' + realSegs.filter(s => s.no).length);
ok(realSegs.filter(s => s.no)[0].head === '【基础概念】', '题型标签进入 head: ' + realSegs.filter(s => s.no)[0].head);
const realHtml = renderAiHtml(real);
ok((realHtml.match(qCardRe) || []).length === 3, '真实格式渲染出 3 张卡片');
ok(realHtml.includes('q-tag t-blue') && realHtml.includes('基础概念'), '题型 → 标签 + 卡片左侧色条');
ok(realHtml.includes('场景设计') && realHtml.includes('q-card t-teal'), '场景题用青色系');
ok(!realHtml.includes('---'), '题目之间的 --- 分隔线被剥离');
ok(realHtml.includes('考察大纲'), '大纲作为普通段保留');
ok(!realHtml.includes('q-title">某支付服务使用'), '纯题型标题不把正文首句顶上来');
ok(!realHtml.includes('class="q-title"'), '无独立标题时不输出空标题占位（头部只有题号+标签）');
const longTag = renderAiHtml('### 第 1 题【深挖追问 · 分库分表 + 高并发】\n正文');
ok(longTag.includes('深挖追问 · 分库分表 + 高并发'), '超过 14 字的长题型标签也能识别');

/* ---- 流式：已完成题立即成卡，最后一题标记 is-live ---- */
console.log('— 流式增量拆卡 —');
const partial = '### 第 1 题【基础概念】\n线程池参数是什么？\n\n### 第 2 题【场景设计】\n设计一个秒杀系统，还没写完';
const liveHtml = renderAiHtmlStreaming(partial);
ok((liveHtml.match(qCardRe) || []).length === 2, '流式中已出现的题立即成卡');
ok(liveHtml.includes('q-card t-teal is-live'), '最后一题标记 is-live');
ok((liveHtml.match(/is-live/g) || []).length === 1, '只有最后一题是 is-live');
ok(liveHtml.includes('q-live-dot'), '进行中卡片有脉冲点');
ok(renderAiHtmlStreaming('### 第 1 题【x】\n```py\nx=1').includes('code-block'), '进行中卡片内未闭合代码块降级');
ok(!renderAiHtml(partial).includes('is-live'), '完成后不再有 is-live');

/* ---- aiBlocks：前端按块渲染，key 稳定 ---- */
console.log('— aiBlocks —');
const blks = aiBlocks(real, false);
ok(blks.filter(b => b.kind === 'q').length === 3, '拆出 3 个题目块');
ok(new Set(blks.map(b => b.key)).size === blks.length, '块 key 唯一');
ok(blks.filter(b => b.kind === 'q').map(b => b.key).join(',') === 'q1,q2,q3', '题目块 key 为题号');
const blksLive = aiBlocks(partial, true);
ok(blksLive.filter(b => b.live).length === 1 && blksLive[blksLive.length - 1].live, '流式仅最后一块 live');
ok(aiBlocks('', false).length === 0, '空内容返回空块');

console.log('\n结果：' + passed + ' 通过 / ' + failed + ' 失败');
process.exit(failed ? 1 : 0);
