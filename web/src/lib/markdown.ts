/* Markdown 富渲染 + 题目卡片（自单文件版迁移，纯函数无 DOM 依赖） */

export function esc(s: string): string {
  return s.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c] as string));
}

/* 完整 markdown：代码块 / 行内代码 / 标题 / 加粗斜体 / 表格 / 引用 / 列表 */
export function mdRich(s: string | null | undefined): string {
  if (s == null) return '';
  const blocks: { lang: string; code: string }[] = [];
  let h = String(s).replace(/```(\w*)\n?([\s\S]*?)(?:```|$)/g, (_m, lang: string, code: string) => {
    blocks.push({ lang: lang || '', code: code.replace(/\n$/, '') });
    return '\u0000CB' + (blocks.length - 1) + '\u0000';
  });
  h = esc(h);
  h = h.replace(/`([^`\n]+?)`/g, '<code>$1</code>');
  h = h.replace(/^\s*####\s*(.+)$/gm, '<h5>$1</h5>');
  h = h.replace(/^\s*###\s*(.+)$/gm, '<h4>$1</h4>');
  h = h.replace(/^\s*##\s*(.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/^\s*#\s*(.+)$/gm, '<h3>$1</h3>');
  h = h.replace(/\*\*([^*]+?)\*\*/g, '<b>$1</b>');
  h = h.replace(/(^|[^*\w])\*([^*\n]+?)\*(?!\*)/g, '$1<i>$2</i>');
  h = h.replace(/((?:^\s*\|.*\|\s*$\n?)+)/gm, (m) => {
    const rows = m.trim().split('\n').map(r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim()));
    let out = '<table>', head = true;
    rows.forEach(cells => {
      if (cells.every(c => /^:?-{2,}:?$/.test(c))) return;
      const tag = head ? 'th' : 'td'; head = false;
      out += '<tr>' + cells.map(c => '<' + tag + '>' + c + '</' + tag + '>').join('') + '</tr>';
    });
    return out + '</table>';
  });
  h = h.replace(/^\s*&gt;\s?(.+)$/gm, '<blockquote>$1</blockquote>');
  h = h.replace(/((?:^[ \t]*(?:[-*]|\d+\.)[ \t]+.*$\n?)+)/gm, (m) => {
    const ordered = /^[ \t]*\d+\./.test(m);
    const items = m.trim().split('\n').map(r => r.replace(/^[ \t]*(?:[-*]|\d+\.)[ \t]+/, ''));
    return '<' + (ordered ? 'ol' : 'ul') + '>' + items.map(i => '<li>' + i + '</li>').join('') + '</' + (ordered ? 'ol' : 'ul') + '>';
  });
  h = h.replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>');
  h = '<p>' + h + '</p>';
  h = h.replace(/\u0000CB(\d+)\u0000/g, (_m, i: string) => {
    const b = blocks[+i];
    return '<div class="code-block"><div class="code-head"><span class="code-lang">' + esc(b.lang || 'code') +
      '</span><button type="button" class="code-copy">复制</button></div><pre><code>' + esc(b.code) + '</code></pre></div>';
  });
  return h;
}

/* 流式传输专用：未闭合的代码块按「正在输入」状态渲染 */
export function mdRichStreaming(s: string): string {
  const fences = (s.match(/```/g) || []).length;
  if (fences % 2 === 1) {
    const idx = s.lastIndexOf('```');
    return mdRich(s.slice(0, idx)) +
      '<div class="code-block"><div class="code-head"><span class="code-lang">…</span></div><pre><code>' +
      esc(s.slice(idx + 3)) + '</code></pre></div>';
  }
  return mdRich(s);
}

/* 把一段 AI 回复按「题目」边界拆开：支持 第N题 / 题N / 问题N / QN 等写法 */
export function splitQuestions(text: string): { no: number; head: string; body: string }[] {
  const lines = String(text == null ? '' : text).split('\n');
  const qRe = /^\s*(?:\*\*|__)?\s*(?:第\s*(\d+)\s*题|题\s*(\d+)|问题\s*(\d+)|Q\s*(\d+))\s*(?:\*\*|__)?\s*[:：.、]?\s*(.*)$/i;
  const segs: { no: number; head: string; lines: string[] }[] = [];
  let cur: { no: number; head: string; lines: string[] } | null = null;
  const push = () => { if (cur) { segs.push(cur); cur = null; } };
  for (const ln of lines) {
    const m = ln.match(qRe);
    if (m) {
      push();
      cur = { no: +(m[1] || m[2] || m[3] || m[4] || 0), head: (m[5] || '').trim(), lines: [] };
      continue;
    }
    if (!cur) cur = { no: 0, head: '', lines: [] };
    cur.lines.push(ln);
  }
  push();
  return segs.map(s => ({ no: s.no, head: s.head.replace(/^\*\*|\*\*$/g, '').trim(), body: s.lines.join('\n').trim() }))
    .filter(s => s.no || s.body);
}

function qTagClass(t: string): string {
  if (/基础|简单|初阶|概念|难度·(简单|基础|初阶)/.test(t)) return 't-blue';
  if (/场景|设计|架构/.test(t)) return 't-teal';
  if (/深挖|进阶|高阶|原理/.test(t)) return 't-purple';
  if (/算法|编码|代码|手写/.test(t)) return 't-orange';
  if (/排查|故障|线上|应急/.test(t)) return 't-red';
  return 't-gray';
}
function headInline(h: string): string {
  return esc(h).replace(/\*\*([^*]+?)\*\*/g, '<b>$1</b>');
}
function qCardHtml(s: { no: number; head: string; body: string }): string {
  let head = s.head || '';
  const tags: string[] = [];
  let m: RegExpMatchArray | null;
  while ((m = head.match(/【([^】]{1,14})】|\[([^\]]{1,14})\]/))) {
    tags.push(m[1] || m[2]);
    head = head.replace(m[0], ' ').trim();
  }
  let body = s.body;
  /* body 里的【考察点】行提取为卡片标签（该行不再重复展示） */
  body = body.replace(/^[ \t]*【([^】]{1,14})】[ \t]*[:：]?[ \t]*(.*)$/gm, (_mm, tag: string, rest: string) => {
    tags.push(tag);
    return (rest || '').trim();
  });
  const dm = body.match(/难度\s*[:：]?\s*([一-龥A-Za-z]{1,6})/);
  if (dm && /简单|中等|困难|进阶|高阶|基础|初级|中级|高级|easy|medium|hard/i.test(dm[1])) tags.push('难度·' + dm[1]);
  /* 追问块用哨兵包住，mdRich 之后再换成高亮 div */
  body = body.replace(/(^|\n)([ \t]*(?:[-*]\s*)?(?:\*\*)?追问(?:\*\*)?\s*[:：][\s\S]*?)(?=\n[ \t]*\n|$)/g,
    (_mm, br: string, p1: string) => br + '\u0001' + p1.trim() + '\u0001');
  if (!head) {
    const first = body.split('\n').find(l => l.trim() && !/^\s*[#>\-*|]/.test(l));
    head = (first || '').replace(/[#*`\s]/g, '').slice(0, 50) || '面试题';
  }
  const bodyHtml = mdRich(body).replace(/\u0001([\s\S]*?)\u0001/g, (_mm, inner: string) =>
    '<div class="q-followup"><b>💬 追问</b>' +
    mdRich(inner.replace(/^[ \t]*(?:[-*]\s*)?(?:\*\*)?追问(?:\*\*)?\s*[:：]\s*/, '')) + '</div>');
  const tagHtml = tags.map(t => '<span class="q-tag ' + qTagClass(t) + '">' + esc(t) + '</span>').join('');
  return '<div class="q-card"><div class="q-card-head"><span class="q-no">Q' + s.no +
    '</span><span class="q-title">' + headInline(head) + '</span>' + tagHtml +
    '</div><div class="q-card-body">' + bodyHtml + '</div></div>';
}

/* AI 消息统一入口：含题目 → 卡片化；否则整段富文本 */
export function renderAiHtml(text: string): string {
  const segs = splitQuestions(text);
  const qCount = segs.filter(s => s.no).length;
  if (qCount >= 1) {
    let html = '';
    segs.forEach(s => {
      if (s.no) html += qCardHtml(s);
      else if (s.body) html += '<div class="ai-plain">' + mdRich(s.body) + '</div>';
    });
    return html;
  }
  return mdRich(text);
}

/* 流式中的 AI 消息：题目边界不稳定，先整段富文本渲染，结束后再由 renderAiHtml 正式拆卡 */
export function renderAiHtmlStreaming(text: string): string {
  if (!text) return '';
  return mdRichStreaming(text);
}
