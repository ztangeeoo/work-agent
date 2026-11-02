/**
 * IT 面试出题官 · 本地服务（前后端分离版）
 * 作用：
 *  1. 托管 React 前端构建产物（web/dist），未构建时回退旧版 index.html
 *  2. /api/models 代理转发到任意 OpenAI 兼容接口（拉取模型列表）
 *  3. /api/chat 转发给 AgentScope 智能体服务（agent_server.py，默认 8790），
 *     由多 Agent（策划官/面试官/评分官）完成出题与对话
 *  4. /api/model-config 保存前端最新模型配置（内存），供 CopilotKit 适配器读取
 *  5. /api/copilotkit CopilotKit 自托管 runtime（适配器 → AgentScope 三智能体）
 *  6. /api/convos、/api/search SQLite 会话持久化与跨会话检索（零依赖 node:sqlite）
 *
 * 启动：start.bat（会同时拉起 Python 智能体服务、构建前端并启动本服务）
 *       AGENTSCOPE=0 node server.js  → 回退到「直接转发模型 API」的老模式
 *
 * 打开 http://localhost:8787
 */
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const os = require('os');
const db = require('./db');

const PORT = Number(process.env.PORT) || 8787;
const ROOT = __dirname;

/* ---------- AgentScope 智能体服务配置 ---------- */
const AGENTSCOPE_URL = (process.env.AGENTSCOPE_URL || 'http://127.0.0.1:8790').replace(/\/+$/, '');
const USE_AGENTSCOPE = process.env.AGENTSCOPE !== '0'; // 默认走智能体

/* ---------- 前端最新模型配置（内存，由 /api/model-config 更新） ---------- */
const modelCfg = { base: '', key: '', model: '', profile: null, runId: '' };

/* ---------- 智能体阶段中心 ----------
 * 后端每个智能体环节的起止都会以 `agent_stage` 事件透出，
 * 适配器捕获后写入这里，前端用 /api/stages?runId=xxx 订阅（SSE）。
 */
const stageHub = new Map(); // runId -> { events: [], clients: Set<res>, updatedAt }
const STAGE_TTL = 10 * 60 * 1000;   // 10 分钟后清理
const STAGE_MAX_EVENTS = 60;

function stageBucket(runId, create) {
  if (!runId) return null;
  let b = stageHub.get(runId);
  if (!b && create) {
    b = { events: [], clients: new Set(), updatedAt: Date.now() };
    stageHub.set(runId, b);
  }
  return b || null;
}

function pushStage(stage, cfg) {
  const runId = (cfg && cfg.runId) || modelCfg.runId;
  const b = stageBucket(runId, true);
  if (!b) return;
  b.updatedAt = Date.now();
  b.events.push(stage);
  if (b.events.length > STAGE_MAX_EVENTS) b.events.splice(0, b.events.length - STAGE_MAX_EVENTS);
  const line = 'data: ' + JSON.stringify(stage) + '\n\n';
  for (const res of b.clients) {
    try { res.write(line); } catch (_) { b.clients.delete(res); }
  }
}

/* 定期清理过期阶段数据，避免长跑泄漏 */
setInterval(() => {
  const now = Date.now();
  for (const [k, b] of stageHub) {
    if (now - b.updatedAt > STAGE_TTL && b.clients.size === 0) stageHub.delete(k);
  }
}, 60 * 1000).unref();

/* ---------- 静态文件 ---------- */
const DIST = path.join(ROOT, 'web', 'dist');

function serveIndex(res) {
  const distIndex = path.join(DIST, 'index.html');
  const target = fs.existsSync(distIndex) ? distIndex : path.join(ROOT, 'index.html');
  fs.readFile(target, (err, buf) => {
    if (err) {
      res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
      res.end('读取页面失败：' + err.message);
      return;
    }
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-cache' });
    res.end(buf);
  });
}

/* web/dist 下的静态资源（Vite 构建产物） */
function serveDistAsset(urlPath, res) {
  const rel = decodeURIComponent(urlPath.replace(/^\/+/, ''));
  const file = path.normalize(path.join(DIST, rel));
  if (!file.startsWith(DIST)) {
    res.writeHead(403); return res.end('Forbidden');
  }
  fs.readFile(file, (err, buf) => {
    if (err) {
      res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
      return res.end('Not Found');
    }
    const ext = path.extname(file).toLowerCase();
    const mime = {
      '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css',
      '.html': 'text/html', '.json': 'application/json', '.svg': 'image/svg+xml',
      '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
      '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff': 'font/woff',
      '.woff2': 'font/woff2', '.map': 'application/json',
    }[ext] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime + '; charset=utf-8', 'Cache-Control': 'public, max-age=3600' });
    res.end(buf);
  });
}

/* ---------- CopilotKit 自托管 runtime ---------- */
let copilotHandler = null;
(async () => {
  try {
    // 自托管部署：关闭 CopilotKit 匿名遥测（不会往外部服务器发任何数据）
    if (!process.env.COPILOTKIT_TELEMETRY_DISABLED) process.env.COPILOTKIT_TELEMETRY_DISABLED = '1';
    const [{ CopilotRuntime, copilotRuntimeNodeHttpEndpoint }, { createAgentScopeAdapter }] = await Promise.all([
      import('@copilotkit/runtime'),
      Promise.resolve(require('./agent_adapter.js')),
    ]);
    const adapter = createAgentScopeAdapter({
      agentBase: AGENTSCOPE_URL,
      getCfg: () => modelCfg,
      onStage: pushStage,
    });
    const runtime = new CopilotRuntime();
    const handler = copilotRuntimeNodeHttpEndpoint({
      runtime,
      serviceAdapter: adapter,
      endpoint: '/api/copilotkit',
      cors: { origin: '*' },
    });
    copilotHandler = handler;
    console.log('  ➜  CopilotKit runtime: /api/copilotkit（适配器 → ' + AGENTSCOPE_URL + '）');
  } catch (e) {
    console.error('  ⚠️ CopilotKit runtime 加载失败（/api/copilotkit 不可用）：' + e.message);
    console.error('     请先在项目根目录执行 npm install');
  }
})();

/* ---------- 组装上游地址（支持自动补 /v1 重试） ---------- */
function buildCandidates(base) {
  let b = String(base || '').trim().replace(/\/+$/, '');
  if (!b) throw new Error('缺少 API 地址');
  if (!/^https?:\/\//i.test(b)) b = 'https://' + b;
  if (/\/chat\/completions$/i.test(b)) return [b];

  const list = [b + '/chat/completions'];
  // 常见错误：地址只写到域名，缺 /v1
  if (!/\/v\d+$/i.test(b)) list.push(b + '/v1/chat/completions');
  return [...new Set(list)];
}

/* 模型列表候选地址 */
function buildModelCandidates(base) {
  let b = String(base || '').trim().replace(/\/+$/, '');
  if (!b) throw new Error('缺少 API 地址');
  if (!/^https?:\/\//i.test(b)) b = 'https://' + b;
  b = b.replace(/\/chat\/completions$/i, '');
  const list = [b + '/models'];
  if (!/\/v\d+$/i.test(b)) list.push(b + '/v1/models');
  return [...new Set(list)];
}

function readBody(req, limit = 4 * 1024 * 1024) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > limit) {
        reject(new Error('请求体过大'));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

function json(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(body);
}

/* ---------- AgentScope 智能体服务探测（带 5 秒缓存） ---------- */
let agentHealthCache = { ok: false, ts: 0, detail: '' };
function checkAgentHealth() {
  return new Promise((resolve) => {
    if (Date.now() - agentHealthCache.ts < 5000) {
      return resolve(agentHealthCache);
    }
    let u;
    try {
      u = new URL(AGENTSCOPE_URL + '/health');
    } catch (_) {
      return resolve({ ok: false, detail: 'AGENTSCOPE_URL 无效' });
    }
    const lib = u.protocol === 'https:' ? https : http;
    const req = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname,
        method: 'GET',
        timeout: 2500,
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          let detail = '';
          try {
            const j = JSON.parse(Buffer.concat(chunks).toString('utf8'));
            detail = `AgentScope ${j.agentscope || ''} · ${(j.agents || []).join('/')}`.trim();
          } catch (_) {}
          agentHealthCache = { ok: res.statusCode === 200, detail, ts: Date.now() };
          resolve(agentHealthCache);
        });
      }
    );
    req.on('timeout', () => {
      req.destroy();
      agentHealthCache = { ok: false, detail: '探测超时', ts: Date.now() };
      resolve(agentHealthCache);
    });
    req.on('error', (err) => {
      agentHealthCache = { ok: false, detail: err.code || err.message, ts: Date.now() };
      resolve(agentHealthCache);
    });
    req.end();
  });
}

/* ---------- 转发给 AgentScope 智能体服务 ---------- */
function forwardToAgent(rawBody, res) {
  return new Promise((resolve) => {
    let u;
    try {
      u = new URL(AGENTSCOPE_URL + '/v1/chat/completions');
    } catch (e) {
      return resolve({ error: new Error('AGENTSCOPE_URL 无效') });
    }
    const lib = u.protocol === 'https:' ? https : http;
    const upstream = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname,
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(rawBody),
        },
        timeout: 300000,
      },
      (up) => {
        const code = up.statusCode || 502;
        if (code >= 200 && code < 300) {
          res.writeHead(200, {
            'Content-Type': up.headers['content-type'] || 'text/event-stream; charset=utf-8',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
            'X-Accel-Buffering': 'no',
          });
          up.pipe(res);
          return resolve({ done: true });
        }
        const chunks = [];
        up.on('data', (c) => chunks.push(c));
        up.on('end', () => resolve({ code, raw: Buffer.concat(chunks).toString('utf8') }));
      }
    );
    upstream.on('timeout', () =>
      upstream.destroy(Object.assign(new Error('智能体服务响应超时（300s）'), { code: 'ETIMEDOUT' }))
    );
    upstream.on('error', (err) => resolve({ error: err }));
    upstream.end(rawBody);
  });
}

const AGENT_DOWN_HINT =
  'AgentScope 智能体服务（agent_server.py）没有运行。\n' +
  '请双击项目里的 start.bat 启动全部服务；或手动执行：\n' +
  '  .venv\\Scripts\\python.exe agent_server.py\n' +
  '（首次运行需先 .venv\\Scripts\\python.exe -m pip install -r requirements.txt）';

/* ---------- /api/chat 代理 ---------- */
async function handleChat(req, res) {
  let raw;
  try {
    // 图片简历会带 base64，放宽到 16MB
    raw = await readBody(req, 16 * 1024 * 1024);
  } catch (e) {
    return json(res, 400, { error: { message: '请求体解析失败：' + e.message } });
  }

  // ---- 模式一：AgentScope 多智能体（默认） ----
  if (USE_AGENTSCOPE) {
    const r = await forwardToAgent(raw, res);
    if (r.done) return;
    if (r.error) {
      let hint = r.error.message;
      if (r.error.code === 'ECONNREFUSED') hint = AGENT_DOWN_HINT;
      else if (r.error.code === 'ENOTFOUND') hint += '（域名解析失败：请检查 AGENTSCOPE_URL）';
      return json(res, 502, { error: { message: '无法连接智能体服务：' + hint } });
    }
    let msg = (r.raw || '').slice(0, 800);
    try {
      const j = JSON.parse(r.raw);
      msg = j.error?.message || j.message || msg;
    } catch (_) {}
    return json(res, r.code || 502, { error: { message: msg || ('智能体服务返回 HTTP ' + r.code) } });
  }

  // ---- 模式二：直接转发模型 API（AGENTSCOPE=0 时的回退） ----
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (e) {
    return json(res, 400, { error: { message: '请求体解析失败：' + e.message } });
  }

  const { base, key, model, messages, temperature, stream, max_tokens } = payload;
  if (!model) return json(res, 400, { error: { message: '缺少模型名称' } });
  if (!Array.isArray(messages) || !messages.length) {
    return json(res, 400, { error: { message: '缺少 messages' } });
  }

  let candidates;
  try {
    candidates = buildCandidates(base);
  } catch (e) {
    return json(res, 400, { error: { message: 'API 地址无效：' + e.message } });
  }

  const body = JSON.stringify({
    model,
    messages,
    temperature: typeof temperature === 'number' ? temperature : 0.7,
    stream: !!stream,
    ...(max_tokens ? { max_tokens } : {}),
  });

  const tried = [];

  // 依次尝试候选路径：地址缺 /v1 时会自动重试一次
  for (let i = 0; i < candidates.length; i++) {
    const target = candidates[i];
    const r = await forwardOnce(target, body, key, res);

    if (r.done) return; // 已流式输出
    tried.push(target + (r.code ? ' -> ' + r.code : ''));

    // 404 / 405 且还有候选路径：换下一种写法重试
    if ((r.code === 404 || r.code === 405) && i < candidates.length - 1) continue;

    if (r.error) {
      let hint = r.error.message;
      if (r.error.code === 'ECONNREFUSED') hint += '（目标地址拒绝连接：本地模型是否已启动？端口是否正确？）';
      if (r.error.code === 'ENOTFOUND') hint += '（域名解析失败：请检查 API 地址拼写）';
      if (r.error.code === 'ETIMEDOUT') hint += '（连接超时：网络或代理是否可用？）';
      return json(res, 502, {
        error: { message: '连接模型接口失败：' + hint + '\n已尝试：' + tried.join('，') },
      });
    }

    let msg = (r.raw || '').slice(0, 500) || ('上游返回 HTTP ' + r.code);
    try {
      const j = JSON.parse(r.raw);
      msg = j.error?.message || j.message || msg;
    } catch (_) {}

    let extra = '';
    if (r.code === 404) {
      extra =
        '\n提示：404 一般是接口路径不对。多数服务地址要以 /v1 结尾（如 https://api.deepseek.com/v1）；也可能是该服务没有这个模型名。';
    }
    return json(res, r.code || 502, {
      error: { message: msg + extra + '\n已尝试：' + tried.join('，') },
    });
  }
}

/* 单次转发：2xx 直接流式透传，非 2xx 缓冲后交给调用方判断 */
function forwardOnce(target, body, key, res) {
  return new Promise((resolve) => {
    let u;
    try {
      u = new URL(target);
    } catch (e) {
      return resolve({ error: new Error('地址无效：' + target) });
    }
    const lib = u.protocol === 'https:' ? https : http;
    const headers = {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body),
    };
    if (key) headers['Authorization'] = 'Bearer ' + key;

    const upstream = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname + u.search,
        method: 'POST',
        headers,
        timeout: 180000,
      },
      (up) => {
        const code = up.statusCode || 502;

        if (code >= 200 && code < 300) {
          res.writeHead(200, {
            'Content-Type': up.headers['content-type'] || 'application/json; charset=utf-8',
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
          });
          up.pipe(res);
          return resolve({ done: true });
        }

        const chunks = [];
        up.on('data', (c) => chunks.push(c));
        up.on('end', () => resolve({ code, raw: Buffer.concat(chunks).toString('utf8') }));
      }
    );

    upstream.on('timeout', () =>
      upstream.destroy(Object.assign(new Error('上游响应超时（180s）'), { code: 'ETIMEDOUT' }))
    );
    upstream.on('error', (err) => resolve({ error: err }));
    upstream.end(body);
  });
}

/* ---------- /api/models 拉取模型列表 ---------- */
async function handleModels(req, res) {
  let payload;
  try {
    payload = JSON.parse(await readBody(req));
  } catch (e) {
    return json(res, 400, { error: { message: '请求体解析失败' } });
  }

  const { base, key } = payload;
  let candidates;
  try {
    candidates = buildModelCandidates(base);
  } catch (e) {
    return json(res, 400, { error: { message: e.message } });
  }

  for (let i = 0; i < candidates.length; i++) {
    const r = await getOnce(candidates[i], key);

    if (r.error) {
      let hint = r.error.message;
      if (r.error.code === 'ECONNREFUSED') hint += '（目标地址拒绝连接）';
      if (r.error.code === 'ENOTFOUND') hint += '（域名解析失败）';
      return json(res, 502, { error: { message: '获取模型列表失败：' + hint } });
    }

    if (r.code >= 200 && r.code < 300) {
      let ids = [];
      try {
        const j = JSON.parse(r.raw);
        ids = (j.data || j.models || [])
          .map((m) => (typeof m === 'string' ? m : m.id || m.name))
          .filter(Boolean);
      } catch (_) {}
      return json(res, 200, { models: ids });
    }

    if (r.code === 401 || r.code === 403) {
      let msg = 'API Key 无效或没有权限（HTTP ' + r.code + '）';
      try {
        msg = JSON.parse(r.raw).error?.message || msg;
      } catch (_) {}
      return json(res, r.code, { error: { message: msg } });
    }

    if ((r.code === 404 || r.code === 405) && i < candidates.length - 1) continue;

    return json(res, r.code || 502, {
      error: { message: (r.raw || '').slice(0, 300) || '获取模型列表失败' },
    });
  }
}

function getOnce(target, key) {
  return new Promise((resolve) => {
    let u;
    try {
      u = new URL(target);
    } catch (e) {
      return resolve({ error: new Error('地址无效') });
    }
    const lib = u.protocol === 'https:' ? https : http;
    const headers = { Accept: 'application/json' };
    if (key) headers['Authorization'] = 'Bearer ' + key;

    const upstream = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname + u.search,
        method: 'GET',
        headers,
        timeout: 30000,
      },
      (up) => {
        const chunks = [];
        up.on('data', (c) => chunks.push(c));
        up.on('end', () =>
          resolve({
            code: up.statusCode || 502,
            raw: Buffer.concat(chunks).toString('utf8'),
          })
        );
      }
    );

    upstream.on('timeout', () =>
      upstream.destroy(Object.assign(new Error('请求超时'), { code: 'ETIMEDOUT' }))
    );
    upstream.on('error', (err) => resolve({ error: err }));
    upstream.end();
  });
}

/* ---------- 启动 ---------- */
const server = http.createServer((req, res) => {
  // 允许从任意页面来源（如静态预览、file:// ）调用本地接口
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,DELETE,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, X-User-Key');
  res.setHeader('Access-Control-Max-Age', '86400');
  if (req.method === 'OPTIONS') {
    res.writeHead(204);
    return res.end();
  }

  const url = new URL(req.url, 'http://' + (req.headers.host || 'localhost'));

  if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
    return serveIndex(res);
  }
  /* Vite 构建产物静态资源 */
  if (req.method === 'GET' && fs.existsSync(DIST) && !url.pathname.startsWith('/api/')) {
    return serveDistAsset(url.pathname, res);
  }
  /* CopilotKit 自托管 runtime（React 前端经此与三智能体对话） */
  if (url.pathname === '/api/copilotkit') {
    if (!copilotHandler) {
      return json(res, 503, { error: { message: 'CopilotKit runtime 未就绪：请先在项目根目录执行 npm install 并重启服务' } });
    }
    return copilotHandler(req, res);
  }
  /* ---------- 会话持久化（SQLite 单文件 data/interview.db） ---------- */
  if (url.pathname === '/api/convos' || url.pathname.startsWith('/api/convos/')) {
    return (async () => {
      if (!db.available) {
        return json(res, 503, {
          dbAvailable: false,
          error: { message: '当前 Node 不支持 node:sqlite（需 >= 22.5），已回退到浏览器本地存储：' + db.unavailableReason },
        });
      }
      const userKey = String(req.headers['x-user-key'] || 'local').slice(0, 64);
      const idPart = url.pathname.slice('/api/convos'.length).replace(/^\//, '');
      let id = '';
      try { id = idPart ? decodeURIComponent(idPart) : ''; } catch (_) { id = idPart; }

      try {
        /* 会话集合 */
        if (!id) {
          if (req.method === 'GET') {
            const limit = Math.min(Number(url.searchParams.get('limit')) || 200, 200);
            const withMessages = url.searchParams.get('messages') !== '0';
            const list = db.listConvos({ user: userKey, limit, withMessages });
            return json(res, 200, { ok: true, dbAvailable: true, list });
          }
          if (req.method === 'POST') {
            const body = JSON.parse(await readBody(req, 16 * 1024 * 1024));
            const saved = db.saveConvo(body, userKey);
            return json(res, 200, { ok: true, ...saved });
          }
          return json(res, 405, { error: { message: '不支持的方法：' + req.method } });
        }

        /* 单个会话 */
        if (req.method === 'GET') {
          const conv = db.getConvo(id, userKey);
          return conv
            ? json(res, 200, { ok: true, convo: conv })
            : json(res, 404, { error: { message: '会话不存在：' + id } });
        }
        if (req.method === 'POST') {
          const body = JSON.parse(await readBody(req, 16 * 1024 * 1024));
          const saved = db.saveConvo({ ...body, id }, userKey);
          return json(res, 200, { ok: true, ...saved });
        }
        if (req.method === 'DELETE') {
          const removed = db.deleteConvo(id, userKey);
          return json(res, removed ? 200 : 404,
            removed ? { ok: true } : { error: { message: '会话不存在：' + id } });
        }
        return json(res, 405, { error: { message: '不支持的方法：' + req.method } });
      } catch (e) {
        return json(res, 500, { error: { message: '数据库操作失败：' + e.message } });
      }
    })();
  }

  /* 跨会话全文检索：GET /api/search?q=缓存穿透 */
  if (req.method === 'GET' && url.pathname === '/api/search') {
    if (!db.available) {
      return json(res, 503, { dbAvailable: false, error: { message: '未启用数据库（node:sqlite 不可用）' } });
    }
    const q = url.searchParams.get('q') || '';
    const limit = Math.min(Number(url.searchParams.get('limit')) || 30, 100);
    if (!q.trim()) return json(res, 200, { ok: true, query: q, results: [] });
    try {
      const userKey = String(req.headers['x-user-key'] || 'local').slice(0, 64);
      return json(res, 200, { ok: true, query: q, results: db.searchMessages(q, { user: userKey, limit }) });
    } catch (e) {
      return json(res, 500, { error: { message: '检索失败：' + e.message } });
    }
  }

  /* 数据库状态 */
  if (req.method === 'GET' && url.pathname === '/api/db/stats') {
    if (!db.available) {
      return json(res, 200, { dbAvailable: false, reason: db.unavailableReason });
    }
    try {
      return json(res, 200, { dbAvailable: true, ...db.stats() });
    } catch (e) {
      return json(res, 500, { error: { message: e.message } });
    }
  }

  /* 保存前端最新模型配置（CopilotKit 适配器每次请求前读取） */
  if (req.method === 'POST' && url.pathname === '/api/model-config') {
    return (async () => {
      try {
        const p = JSON.parse(await readBody(req, 1 * 1024 * 1024));
        modelCfg.base = String(p.base || '').trim();
        modelCfg.key = String(p.key || '').trim();
        modelCfg.model = String(p.model || '').trim();
        modelCfg.profile = p.profile && typeof p.profile === 'object' ? p.profile : null;
        if (typeof p.runId === 'string' && p.runId) {
          modelCfg.runId = p.runId;
          stageBucket(p.runId, true); // 新一轮，保证桶存在
        }
        json(res, 200, { ok: true });
      } catch (e) {
        json(res, 400, { error: { message: '请求体解析失败：' + e.message } });
      }
    })();
  }
  /* 智能体阶段事件订阅（SSE）：前端流水线面板用 */
  if (req.method === 'GET' && url.pathname === '/api/stages') {
    const runId = url.searchParams.get('runId') || '';
    if (!runId) return json(res, 400, { error: { message: '缺少 runId' } });
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
      'Access-Control-Allow-Origin': '*',
    });
    const b = stageBucket(runId, true);
    // 补发历史事件（前端可能在阶段开始后才连上来）
    for (const ev of b.events) res.write('data: ' + JSON.stringify(ev) + '\n\n');
    b.clients.add(res);
    const hb = setInterval(() => { try { res.write(': ping\n\n'); } catch (_) {} }, 15000);
    req.on('close', () => { clearInterval(hb); b.clients.delete(res); });
    return;
  }
  if (req.method === 'GET' && url.pathname === '/api/health') {
    return (async () => {
      const agent = await checkAgentHealth();
      let dbInfo = { available: db.available };
      if (db.available) {
        try { Object.assign(dbInfo, db.stats()); } catch (e) { dbInfo.error = e.message; }
      }
      json(res, 200, {
        ok: true,
        service: 'interview-agent',
        port: PORT,
        mode: USE_AGENTSCOPE ? 'agentscope' : 'direct',
        agentOk: USE_AGENTSCOPE ? agent.ok : null,
        agentDetail: agent.detail || '',
        db: dbInfo,
      });
    })();
  }
  if (req.method === 'POST' && url.pathname === '/api/chat') {
    return handleChat(req, res);
  }
  if (req.method === 'POST' && url.pathname === '/api/models') {
    return handleModels(req, res);
  }
  json(res, 404, { error: { message: 'Not Found' } });
});

/* 列出本机 IPv4 地址，便于局域网访问 */
function lanAddresses() {
  const out = [];
  const nets = os.networkInterfaces();
  for (const name of Object.keys(nets)) {
    for (const ni of nets[name] || []) {
      if (ni.family === 'IPv4' && !ni.internal) out.push({ name, address: ni.address });
    }
  }
  return out;
}

server.listen(PORT, async () => {
  console.log('');
  console.log('  🎯 IT 面试出题官已启动');
  console.log('  ➜  本机访问:   http://localhost:' + PORT);
  if (USE_AGENTSCOPE) {
    const agent = await checkAgentHealth();
    if (agent.ok) {
      console.log('  ➜  智能体:     AgentScope 已连接（' + (agent.detail || AGENTSCOPE_URL) + '）');
    } else {
      console.log('  ➜  智能体:     ⚠️ 未连接（' + AGENTSCOPE_URL + '），出题会失败');
      console.log('                请先启动智能体服务：.venv\\Scripts\\python.exe agent_server.py');
    }
  } else {
    console.log('  ➜  智能体:     已关闭（AGENTSCOPE=0，直接转发模型 API）');
  }
  /* 初始化数据库并打印状态 */
  if (db.available) {
    try {
      const s = db.stats();
      console.log('  ➜  历史库:     SQLite 就绪 · ' + s.convos + ' 场会话 / ' + s.messages + ' 条消息');
      console.log('                ' + s.path + '（' + s.sizeKB + ' KB，备份=复制此文件）');
    } catch (e) {
      console.log('  ➜  历史库:     ⚠️ 初始化失败（' + e.message + '），回退到浏览器本地存储');
    }
  } else {
    console.log('  ➜  历史库:     ⚠️ node:sqlite 不可用（需 Node >= 22.5），回退到浏览器本地存储');
  }
  const lan = lanAddresses();
  if (lan.length) {
    console.log('  ➜  局域网访问:');
    lan.forEach((n) => console.log('       http://' + n.address + ':' + PORT + '   （' + n.name + '）'));
    console.log('     手机/同事电脑与你在同一 WiFi 下即可打开上述地址');
    console.log('     若打不开，多半是 Windows 防火墙拦截（见下方命令，需管理员）');
    console.log('       netsh advfirewall firewall add rule name="IT面试出题官" dir=in action=allow protocol=TCP localport=' + PORT);
  } else {
    console.log('  ➜  未检测到局域网网卡（仅本机可访问）');
  }
  console.log('  ➜  停止服务: Ctrl + C');
  console.log('');
});
