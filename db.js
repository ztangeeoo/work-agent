'use strict';
/**
 * 持久层：单文件 SQLite（Node 22 内置 node:sqlite，零 npm 依赖）
 *
 * 设计要点
 *  - 数据落在 data/interview.db，备份=复制文件，迁移=拷走
 *  - WAL 模式：读写并发友好，崩溃后可恢复
 *  - FTS5 + trigram 分词：中文全文检索（"缓存穿透"这种 3 字词可命中）
 *  - 所有写操作走事务，会话保存采用「整会话覆盖」策略（消息量小，行为可预测）
 *  - 若运行环境不支持 node:sqlite，则 available=false，上层降级到纯 localStorage
 */

const fs = require('fs');
const path = require('path');

/* node:sqlite 在 Node 22 仍标记为实验特性，会刷一行警告；只过滤这一条 */
const _emitWarning = process.emitWarning;
process.emitWarning = function (warning, ...rest) {
  const text = typeof warning === 'string' ? warning : (warning && warning.message) || '';
  if (text.includes('SQLite is an experimental feature')) return;
  return _emitWarning.call(process, warning, ...rest);
};

let DatabaseSync = null;
let loadError = '';
try {
  ({ DatabaseSync } = require('node:sqlite'));
} catch (e) {
  loadError = e.message;
}

const DB_PATH = process.env.DB_PATH || path.join(__dirname, 'data', 'interview.db');
const DEFAULT_USER = 'local';
const MAX_MSGS = 400;
const MAX_CONVOS = 200;

let _db = null;

const SCHEMA = `
CREATE TABLE IF NOT EXISTS convo (
  id           TEXT PRIMARY KEY,
  user_key     TEXT NOT NULL DEFAULT 'local',
  title        TEXT NOT NULL DEFAULT '新会话',
  profile_json TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_convo_user ON convo(user_key, updated_at DESC);

CREATE TABLE IF NOT EXISTS msg (
  id         TEXT NOT NULL,
  convo_id   TEXT NOT NULL REFERENCES convo(id) ON DELETE CASCADE,
  seq        INTEGER NOT NULL,
  role       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_convo ON msg(convo_id, seq);
CREATE UNIQUE INDEX IF NOT EXISTS idx_msg_id ON msg(id);

CREATE VIRTUAL TABLE IF NOT EXISTS msg_fts USING fts5(content, tokenize = 'trigram');

CREATE TRIGGER IF NOT EXISTS msg_ai AFTER INSERT ON msg BEGIN
  INSERT INTO msg_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS msg_ad AFTER DELETE ON msg BEGIN
  DELETE FROM msg_fts WHERE rowid = old.rowid;
END;

CREATE TABLE IF NOT EXISTS profile_memory (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_key     TEXT NOT NULL DEFAULT 'local',
  kind         TEXT NOT NULL,
  topic        TEXT NOT NULL,
  content      TEXT NOT NULL,
  confidence   REAL NOT NULL DEFAULT 0.6,
  source_convo TEXT,
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mem_user ON profile_memory(user_key, kind, topic);
`;

function init() {
  if (_db) return _db;
  if (!DatabaseSync) {
    throw new Error('当前 Node 不支持 node:sqlite（需 >= 22.5）：' + loadError);
  }
  fs.mkdirSync(path.dirname(DB_PATH), { recursive: true });
  const db = new DatabaseSync(DB_PATH);
  db.exec('PRAGMA journal_mode = WAL');
  db.exec('PRAGMA synchronous = NORMAL');
  db.exec('PRAGMA foreign_keys = ON');
  db.exec('PRAGMA busy_timeout = 5000');
  db.exec(SCHEMA);
  _db = db;
  return db;
}

function mapConvo(r) {
  let profile = null;
  if (r.profile_json) {
    try { profile = JSON.parse(r.profile_json); } catch (_) { profile = null; }
  }
  return {
    id: r.id,
    title: r.title,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
    profile,
  };
}

/** 会话列表（默认带消息，前端切换会话无需二次请求） */
function listConvos({ user = DEFAULT_USER, limit = MAX_CONVOS, withMessages = true } = {}) {
  const db = init();
  const rows = db.prepare(
    `SELECT id, title, profile_json, created_at, updated_at
       FROM convo WHERE user_key = ?
      ORDER BY updated_at DESC LIMIT ?`
  ).all(user, limit);

  if (!withMessages) return rows.map(mapConvo);

  const msgStmt = db.prepare(
    'SELECT id, role, content FROM msg WHERE convo_id = ? ORDER BY seq ASC'
  );
  return rows.map(r => ({
    ...mapConvo(r),
    messages: msgStmt.all(r.id).map(m => ({ id: m.id, role: m.role, content: m.content })),
  }));
}

function getConvo(id, user = DEFAULT_USER) {
  const db = init();
  const r = db.prepare(
    `SELECT id, title, profile_json, created_at, updated_at
       FROM convo WHERE id = ? AND user_key = ?`
  ).get(id, user);
  if (!r) return null;
  const msgs = db.prepare(
    'SELECT id, role, content FROM msg WHERE convo_id = ? ORDER BY seq ASC'
  ).all(id);
  return { ...mapConvo(r), messages: msgs.map(m => ({ id: m.id, role: m.role, content: m.content })) };
}

/** 保存整场会话（覆盖式，事务内完成） */
function saveConvo(input, user = DEFAULT_USER) {
  const id = String(input && input.id || '').trim();
  if (!id) throw new Error('缺少会话 id');
  const db = init();
  const now = Date.now();
  const msgs = (Array.isArray(input.messages) ? input.messages : []).slice(-MAX_MSGS);
  const title = String(input.title || '新会话').slice(0, 200);
  const profileJson = input.profile ? JSON.stringify(input.profile) : null;
  const createdAt = Number(input.createdAt) || now;
  const updatedAt = Number(input.updatedAt) || now;

  db.exec('BEGIN IMMEDIATE');
  try {
    db.prepare(
      `INSERT INTO convo (id, user_key, title, profile_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(id) DO UPDATE SET
            title        = excluded.title,
            profile_json = excluded.profile_json,
            updated_at   = excluded.updated_at`
    ).run(id, user, title, profileJson, createdAt, updatedAt);

    db.prepare('DELETE FROM msg WHERE convo_id = ?').run(id);

    const ins = db.prepare(
      'INSERT INTO msg (id, convo_id, seq, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)'
    );
    msgs.forEach((m, i) => {
      ins.run(
        String(m.id || `${id}_${i}`),
        id,
        i,
        m.role === 'user' ? 'user' : 'assistant',
        String(m.content == null ? '' : m.content),
        createdAt + i
      );
    });

    /* 上限保护：只保留最近 MAX_CONVOS 场，避免库无限增长 */
    db.prepare(
      `DELETE FROM convo WHERE user_key = ? AND id NOT IN (
         SELECT id FROM convo WHERE user_key = ? ORDER BY updated_at DESC LIMIT ?
       )`
    ).run(user, user, MAX_CONVOS);

    db.exec('COMMIT');
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
  return { id, count: msgs.length };
}

function deleteConvo(id, user = DEFAULT_USER) {
  const db = init();
  db.exec('BEGIN IMMEDIATE');
  try {
    db.prepare('DELETE FROM msg WHERE convo_id = ?').run(id);
    const r = db.prepare('DELETE FROM convo WHERE id = ? AND user_key = ?').run(id, user);
    db.exec('COMMIT');
    return r.changes > 0;
  } catch (e) {
    db.exec('ROLLBACK');
    throw e;
  }
}

/**
 * 跨会话全文检索。
 * trigram 分词要求 >= 3 字符：单串或全为长词时走 FTS5，
 * 含短词（如「雪崩」「击穿」）时逐词 LIKE AND，兼顾中文短词召回。
 */
function searchMessages(q, { user = DEFAULT_USER, limit = 30 } = {}) {
  const db = init();
  const query = String(q || '').trim();
  if (!query) return [];

  const terms = query.split(/\s+/).filter(Boolean);
  const likeTerms = terms.length ? terms : [query];
  let rows = [];

  /* 主路径：FTS5（所有词都 >= 3 字符） */
  if (terms.length && terms.every(t => t.length >= 3)) {
    const expr = terms.map(t => '"' + t.replace(/"/g, '""') + '"').join(' AND ');
    try {
      rows = db.prepare(
        `SELECT m.id, m.convo_id, m.role, m.content, m.created_at, c.title,
                snippet(msg_fts, 0, '\u0002', '\u0003', '…', 30) AS snip
           FROM msg_fts
           JOIN msg   m ON m.rowid = msg_fts.rowid
           JOIN convo c ON c.id = m.convo_id
          WHERE msg_fts MATCH ? AND c.user_key = ?
          ORDER BY m.created_at DESC LIMIT ?`
      ).all(expr, user, limit);
    } catch (_) {
      rows = [];
    }
  }

  /* 回退：逐词 LIKE AND（短词、或 FTS 无命中） */
  if (!rows.length) {
    const conds = likeTerms.map(() => "m.content LIKE ? ESCAPE '\\'").join(' AND ');
    const params = likeTerms.map(
      t => '%' + t.replace(/[\\%_]/g, c => '\\' + c) + '%'
    );
    rows = db.prepare(
      `SELECT m.id, m.convo_id, m.role, m.content, m.created_at, c.title,
              substr(m.content, 1, 140) AS snip
         FROM msg m JOIN convo c ON c.id = m.convo_id
        WHERE c.user_key = ? AND ${conds}
        ORDER BY m.created_at DESC LIMIT ?`
    ).all(user, ...params, limit);
  }

  return rows.map(r => ({
    id: r.id,
    convoId: r.convo_id,
    convoTitle: r.title,
    role: r.role,
    /* snippet 里的 \u0002/\u0003 是命中词边界标记，前端按需渲染高亮 */
    snippet: String(r.snip || '').replace(/[\u0002\u0003]/g, ''),
    createdAt: r.created_at,
  }));
}

function stats() {
  const db = init();
  const c = db.prepare('SELECT COUNT(*) AS n FROM convo').get();
  const m = db.prepare('SELECT COUNT(*) AS n FROM msg').get();
  let sizeKB = 0;
  try { sizeKB = Math.round(fs.statSync(DB_PATH).size / 1024); } catch (_) {}
  return { convos: c.n, messages: m.n, path: DB_PATH, sizeKB };
}

/** 关闭连接（测试与进程退出时用） */
function close() {
  if (!_db) return;
  try { _db.close(); } catch (_) {}
  _db = null;
}

module.exports = {
  available: !!DatabaseSync,
  unavailableReason: loadError,
  DB_PATH,
  init,
  close,
  listConvos,
  getConvo,
  saveConvo,
  deleteConvo,
  searchMessages,
  stats,
};
