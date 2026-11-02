/* tests/test_web.ts — React 版 markdown 渲染 / 题目卡片逻辑回归（Node 22 直接跑 TS） */
import { mdRich, mdRichStreaming, renderAiHtml, renderAiHtmlStreaming, splitQuestions } from '../web/src/lib/markdown.ts';

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
ok((renderAiHtml(q3).match(/class="q-card"/g) || []).length === 3, '3 张题目卡片');
ok(renderAiHtml(q3).includes('Q1') && renderAiHtml(q3).includes('Q3'), '题号徽标 Q1/Q3');
ok(renderAiHtml(q3).includes('ai-plain'), '开场白作为普通段保留');
const withTags = '**第 1 题：缓存穿透**\n描述场景。\n【考察点】缓存\n追问：如果缓存击穿呢？';
ok(renderAiHtml(withTags).includes('q-tag') && renderAiHtml(withTags).includes('考察点'), '【考察点】标签');
ok(renderAiHtml(withTags).includes('q-followup'), '追问高亮');
ok(!renderAiHtml('好的，我们直接开始。').includes('q-card'), '普通消息不卡片化');
ok(renderAiHtml('**第 1 题：手撕代码**\n```js\nconst a = 1;\n```\n请解释。').includes('q-card'), '卡片内含代码块');
ok(renderAiHtml('**第 1 题：难度自适应**\n内容\n难度：中等').includes('难度·中等'), '难度标签');
ok(!renderAiHtmlStreaming('**第 1 题：进行中的题').includes('q-card'), '流式中不拆卡');

console.log('\n结果：' + passed + ' 通过 / ' + failed + ' 失败');
process.exit(failed ? 1 : 0);
