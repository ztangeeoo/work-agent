/**
 * 持久层测试（node:sqlite）
 * 运行：node tests/test_db.js
 */
const fs = require('fs');
const path = require('path');
const os = require('os');

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'itv-db-'));
process.env.DB_PATH = path.join(tmpDir, 'test.db');

const db = require('../db');

let pass = 0, fail = 0;
function ok(cond, name, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
}

console.log('\n[0] 环境');
ok(db.available === true, 'node:sqlite 可用', db.unavailableReason);
ok(db.DB_PATH === process.env.DB_PATH, 'DB 路径尊重 DB_PATH 环境变量');

console.log('\n[1] 保存与读取');
const c1 = {
  id: 'c_1', title: '后端面试 · 中级', createdAt: 1000, updatedAt: 2000,
  profile: { track: '后端开发', level: '中级' },
  messages: [
    { id: 'm1', role: 'user', content: '请出 2 道后端面试题' },
    { id: 'm2', role: 'assistant', content: '第 1 题：请解释 Redis 缓存穿透和布隆过滤器的区别' },
    { id: 'm3', role: 'user', content: '缓存穿透是查询不存在的数据' },
  ],
};
db.saveConvo(c1);

const got = db.getConvo('c_1');
ok(!!got, '能取回会话');
ok(got.messages.length === 3, '消息条数 3，实际 ' + (got && got.messages.length));
ok(got.messages[0].role === 'user' && got.messages[1].role === 'assistant', '角色与顺序正确');
ok(got.profile && got.profile.track === '后端开发', 'profile JSON 往返正确');
ok(got.title === '后端面试 · 中级', '标题正确');

console.log('\n[2] 覆盖式保存');
/* 只保留第 1 条，m2（含 Redis / 布隆过滤器）应被真正删除 */
db.saveConvo({ ...c1, updatedAt: 3000, messages: c1.messages.slice(0, 1) });
const got2 = db.getConvo('c_1');
ok(got2.messages.length === 1, '覆盖后消息数 1，实际 ' + got2.messages.length);
ok(got2.updatedAt === 3000, 'updatedAt 已更新');

const ftsAfterRewrite = db.searchMessages('布隆过滤器');
ok(ftsAfterRewrite.length === 0, '被覆盖掉的消息已从索引移除，实际 ' + ftsAfterRewrite.length);

console.log('\n[3] 中文全文检索（trigram）');
db.saveConvo({
  id: 'c_2', title: '缓存专题', createdAt: 4000, updatedAt: 5000,
  messages: [{ id: 'm10', role: 'assistant', content: '请说明缓存雪崩、缓存击穿与缓存穿透的差异' }],
});
let r = db.searchMessages('缓存穿透');
ok(r.length === 1, '「缓存穿透」命中 1 条，实际 ' + r.length);
ok(r[0] && r[0].convoTitle === '缓存专题', '命中结果带会话标题');
ok(r[0] && r[0].snippet.length > 8 && r[0].snippet.includes('缓存'),
   'snippet 带命中上下文', r[0] && r[0].snippet);

r = db.searchMessages('Redis');
ok(r.length === 0, '已被覆盖删除的内容搜不到（Redis 在旧消息里）');

console.log('\n[4] 短词回退 LIKE');
r = db.searchMessages('雪崩');
ok(r.length === 1, '2 字词「雪崩」经 LIKE 命中 1 条，实际 ' + r.length);

console.log('\n[5] 多词 AND 检索');
r = db.searchMessages('缓存 击穿');
ok(r.length === 1, '「缓存 击穿」AND 命中 1 条，实际 ' + r.length);
r = db.searchMessages('缓存 不存在的词xyz');
ok(r.length === 0, '含不存在词的组合不命中');

console.log('\n[6] 列表与排序');
db.saveConvo({ id: 'c_3', title: '最新会话', createdAt: 9000, updatedAt: 9000, messages: [] });
const list = db.listConvos();
ok(list.length === 3, '会话总数 3，实际 ' + list.length);
ok(list[0].id === 'c_3', '按 updatedAt 倒序，最新在前');

const brief = db.listConvos({ withMessages: false });
ok(brief[0].messages === undefined, 'withMessages:false 不返回消息');

console.log('\n[7] 删除级联');
db.deleteConvo('c_2');
ok(db.getConvo('c_2') === null, '会话已删除');
const leftover = db.searchMessages('缓存雪崩');
ok(leftover.length === 0, '消息与全文索引一并清理，实际 ' + leftover.length);

console.log('\n[8] 用户隔离');
db.saveConvo({ id: 'u_a1', title: 'A 的会话', createdAt: 1, updatedAt: 1, messages: [] }, 'alice');
db.saveConvo({ id: 'u_b1', title: 'B 的会话', createdAt: 1, updatedAt: 1, messages: [] }, 'bob');
const la = db.listConvos({ user: 'alice' });
const lb = db.listConvos({ user: 'bob' });
ok(la.length === 1 && la[0].id === 'u_a1', 'alice 只能看到自己的会话');
ok(lb.length === 1 && lb[0].id === 'u_b1', 'bob 只能看到自己的会话');
ok(db.getConvo('u_b1', 'alice') === null, '跨用户取详情被拒绝');

console.log('\n[9] 上限与健壮性');
const many = [];
for (let i = 0; i < 600; i++) many.push({ id: 'x' + i, role: 'user', content: 'msg ' + i });
db.saveConvo({ id: 'c_big', title: '超长会话', createdAt: 1, updatedAt: 1, messages: many });
ok(db.getConvo('c_big').messages.length === 400, '单会话截断到 400 条');

let threw = false;
try { db.saveConvo({ id: '', messages: [] }); } catch (e) { threw = true; }
ok(threw, '缺少 id 时抛错而非静默写入');

const st = db.stats();
ok(st.convos >= 3 && st.messages >= 400, 'stats 统计正常：' + JSON.stringify(st));
ok(fs.existsSync(db.DB_PATH), '数据库文件已落盘');

db.close();
try { fs.rmSync(tmpDir, { recursive: true, force: true }); } catch (_) {}
console.log(`\n结果：${pass} 通过 / ${fail} 失败\n`);
process.exit(fail ? 1 : 0);
