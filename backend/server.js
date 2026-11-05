/**
 * IT 面试出题官 · 本地服务（前后端分离版）
 * 作用：
 *  1. 托管 React 前端构建产物（web/dist），未构建时回退旧版 index.html
 *  2. /api/models 代理转发到任意 OpenAI 兼容接口（拉取模型列表）
 *  3. /api/chat 转发给 AgentScope 智能体服务（app/，默认 8790），
 *     由多 Agent（策划官/面试官/评分官）完成出题与对话
 *  4. /api/model-config 保存前端最新模型配置（内存），供 CopilotKit 适配器读取
 *  5. /api/copilotkit CopilotKit 自托管 runtime（适配器 → AgentScope 三智能体）
 *  6. /api/convos、/api/experts、/api/agents、/api/search、/api/repos、/api/reviews、/api/review/events
 *     反向代理到 FastAPI（:8790）；持久化用 Python 标准库 sqlite3，零额外依赖
 *
 * 位置：本文件在 backend/ 下，运行时 ROOT 指回仓库根（web/dist、index.html 都在根）。
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
/* 持久化（SQLite）、代码托管适配、PR 轮询与全部 /api/* 业务接口
 * 都已归到 FastAPI 侧（app/store/ + app/services/ 与 app/api/routes/），
 * 本服务只做「静态托管 + CopilotKit runtime + 反向代理」。 */

const PORT = Number(process.env.PORT) || 8787;
/* 仓库根：本文件在 backend/ 下，静态资源与构建产物都在上一层 */
const ROOT = path.resolve(__dirname, '..');

/* ---------- AgentScope 智能体服务配置 ---------- */
const AGENTSCOPE_URL = (process.env.AGENTSCOPE_URL || 'http://127.0.0.1:8790').replace(/\/+$/, '');
const USE_AGENTSCOPE = process.env.AGENTSCOPE !== '0'; // 默认走智能体

/* ---------- 全局模型配置（全局唯一一份，不属于任何会话） ----------
 * base/key/model 只在「模型配置」弹框保存时写入（POST /api/model-config），
 * 启动时从 Python 侧的 setting 表读回，所以 Node 重启也不会丢。
 * 前端不持有、也不回传这三个值——避免某个旧标签页把全局配置改坏。
 */
const modelCfg = { base: '', key: '', model: '' };

/* ---------- 本轮上下文（跟着对话走，不是配置） ----------
 * profile / 当前 PR / repoId / runId / agentMode 每轮都不一样，属于「这一次对话」
 * 而不是「这个系统怎么连模型」。所以单独放这里，只影响紧接着的那次对话请求，
 * 绝不写进 modelCfg —— 会话（或浏览器）不应该去动全局 LLM 配置。
 * 由前端在每次发送前 POST /api/run-context 更新。
 */
const runCtx = {
  profile: null,
  runId: '',
  agentMode: 'interview',  // interview | review
  review: null,            // 审核模式下当前 PR 的上下文（追问用）
  repoId: '',              // 审核模式下当前仓库：审核官据此绑定只读工具
};

/** 从 Python 侧读回全局模型配置（Node 与 Python 各缓存一份，以 setting 表为准） */
async function hydrateModelCfg() {
  try {
    const r = await fetchAgentJson('/api/model-config');
    const c = (r && r.cfg) || {};
    if (c.base && c.model) {
      modelCfg.base = String(c.base || '');
      modelCfg.key = String(c.key || '');
      modelCfg.model = String(c.model || '');
      console.log('  ➜  模型配置已读取：' + modelCfg.model);
    }
  } catch (_) { /* Python 还没起来：等页面 GET 时再补 */ }
}

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
  const runId = (cfg && cfg.runId) || runCtx.runId;
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

/* ---------- 代码审核事件中心 ----------
 * 轮询状态、审核完成都会广播；页面晚打开时回放最近一批事件，避免错过结果。
 */
const reviewClients = new Set();
const reviewEventLog = [];
const REVIEW_EVENT_MAX = 120;

function broadcastReview(ev) {
  reviewEventLog.push(ev);
  if (reviewEventLog.length > REVIEW_EVENT_MAX) {
    reviewEventLog.splice(0, reviewEventLog.length - REVIEW_EVENT_MAX);
  }
  const line = 'data: ' + JSON.stringify(ev) + '\n\n';
  for (const res of reviewClients) {
    try { res.write(line); } catch (_) { reviewClients.delete(res); }
  }
}

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
      /* 全局配置 + 本轮上下文合并：base/key/model 永远来自 modelCfg，
         会话相关的 profile/review/repoId 来自 runCtx，两者互不覆盖 */
      getCfg: () => Object.assign({}, runCtx, modelCfg),
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

/* ---------- 反向代理：把 /api/* 业务接口透传给 FastAPI（8790） ----------
 * 持久化、仓库配置、PR 轮询、审核报告与事件流都在 Python 侧；
 * 本函数流式透传（SSE 也能用），上游不可用时给出明确提示。
 */
function proxyToAgent(req, res, targetPath) {
  return new Promise((resolve) => {
    let u;
    try {
      u = new URL(AGENTSCOPE_URL + targetPath);
    } catch (_) {
      return resolve({ done: false, error: new Error('AGENTSCOPE_URL 无效') });
    }
    const lib = u.protocol === 'https:' ? https : http;
    const headers = {
      Host: u.host,
      Accept: req.headers.accept || '*/*',
    };
    if (req.headers['content-type']) headers['Content-Type'] = req.headers['content-type'];
    if (req.headers['x-user-key']) headers['X-User-Key'] = String(req.headers['x-user-key']).slice(0, 64);
    if (req.headers['content-length']) headers['Content-Length'] = req.headers['content-length'];

    const preq = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname + u.search,
        method: req.method,
        headers,
        timeout: 300000,   // 审核流水线可能跑几分钟
      },
      (pres) => {
        const outHeaders = {
          'Content-Type': pres.headers['content-type'] || 'application/json; charset=utf-8',
        };
        if (pres.headers['cache-control']) outHeaders['Cache-Control'] = pres.headers['cache-control'];
        if (String(pres.headers['content-type'] || '').includes('text/event-stream')) {
          outHeaders['Cache-Control'] = outHeaders['Cache-Control'] || 'no-cache, no-transform';
          outHeaders.Connection = 'keep-alive';
          outHeaders['X-Accel-Buffering'] = 'no';
        }
        res.writeHead(pres.statusCode || 502, outHeaders);
        pres.pipe(res);
        pres.on('end', () => resolve({ done: true }));
        pres.on('error', () => resolve({ done: true }));
      }
    );
    preq.on('timeout', () => preq.destroy(Object.assign(new Error('智能体服务响应超时'), { code: 'ETIMEDOUT' })));
    preq.on('error', (e) => resolve({ done: false, error: e }));
    req.pipe(preq);
  });
}

/** 代理并保证响应一定被写回（上游挂掉时给出可读错误） */
async function proxyApi(req, res, targetPath, fallbackStatus = 502) {
  const r = await proxyToAgent(req, res, targetPath);
  if (r.done) return;
  if (res.headersSent) { try { res.end(); } catch (_) {} return; }
  let hint = (r.error && r.error.message) || '未知错误';
  if (r.error && r.error.code === 'ECONNREFUSED') hint = AGENT_DOWN_HINT;
  json(res, fallbackStatus, {
    error: { message: '无法连接后端数据服务（' + AGENTSCOPE_URL + '）：' + hint },
  });
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
        timeout: 4000,   // /health 里 import agentscope 偶尔要 2s+，给足余量避免误报未连接
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

/* ---------- 读取智能体服务的 JSON（启动时探测持久化状态用） ---------- */
function fetchAgentJson(pathname, timeout = 4000) {
  return new Promise((resolve, reject) => {
    let u;
    try {
      u = new URL(AGENTSCOPE_URL + pathname);
    } catch (e) {
      return reject(new Error('AGENTSCOPE_URL 无效'));
    }
    const lib = u.protocol === 'https:' ? https : http;
    const req = lib.request(
      {
        hostname: u.hostname,
        port: u.port || (u.protocol === 'https:' ? 443 : 80),
        path: u.pathname + u.search,
        method: 'GET',
        timeout,
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          try {
            resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}'));
          } catch (e) {
            reject(new Error('返回内容不是合法 JSON'));
          }
        });
      }
    );
    req.on('timeout', () => req.destroy(new Error('探测超时')));
    req.on('error', reject);
    req.end();
  });
}
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
  'AgentScope 智能体服务（backend/）没有运行。\n' +
  '请双击项目里的 start.bat 启动全部服务；或手动执行：\n' +
  '  .venv\\Scripts\\python.exe backend\\run.py\n' +
  '（首次运行需先 .venv\\Scripts\\python.exe -m pip install -r backend\\requirements.txt）';

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

  /* 模型三要素是全局配置：请求体里没带就用服务端那份（前端不必持有 key） */
  const { messages, temperature, stream, max_tokens } = payload;
  const base = payload.base || modelCfg.base;
  const key = payload.key !== undefined ? payload.key : modelCfg.key;
  const model = payload.model || modelCfg.model;
  if (!model) return json(res, 400, { error: { message: '缺少模型名称（服务端也还没有保存模型配置，请点右上角齿轮配置）' } });
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
  /* ================== 业务接口：反向代理到 FastAPI（8790） ==================
   * 持久化（会话/检索）、仓库配置、PR 轮询、审核报告与事件流都由 Python 侧提供，
   * 前端仍只访问本服务的 /api/*，因此接口路径与响应结构保持不变。
   */

  /* 会话持久化（SQLite：Python app/store/） */
  if (url.pathname === '/api/convos' || url.pathname.startsWith('/api/convos/')) {
    return void proxyApi(req, res, url.pathname + url.search);
  }

  /* 专家与智能体清单（会话 → 智能体 → 专家 的关系也由 Python 侧落库） */
  if (url.pathname === '/api/experts' || url.pathname.startsWith('/api/experts/')
      || url.pathname === '/api/agents') {
    return void proxyApi(req, res, url.pathname + url.search);
  }

  /* 跨会话全文检索 */
  if (req.method === 'GET' && url.pathname === '/api/search') {
    return void proxyApi(req, res, url.pathname + url.search);
  }

  /* 历史库状态 */
  if (req.method === 'GET' && url.pathname === '/api/db/stats') {
    return void proxyApi(req, res, url.pathname + url.search);
  }

  /* 全局模型配置：唯一写入口（只接受 base/key/model）
   * 会话上下文（profile / review / repoId / runId）不走这里 —— 见 /api/run-context。
   * 写内存的同时同步给 Python：自动轮询要读同一份配置。 */
  if (req.method === 'POST' && url.pathname === '/api/model-config') {
    return (async () => {
      let raw = '';
      let body = null;
      try {
        raw = await readBody(req, 1 * 1024 * 1024);
        const p = JSON.parse(raw);
        modelCfg.base = String(p.base || '').trim();
        modelCfg.key = String(p.key || '').trim();
        modelCfg.model = String(p.model || '').trim();
        /* 转发给 Python 的只保留三项：它是全局配置，不该混入会话上下文 */
        body = JSON.stringify({ base: modelCfg.base, key: modelCfg.key, model: modelCfg.model });
      } catch (e) {
        return json(res, 400, { error: { message: '请求体解析失败：' + e.message } });
      }
      let pyOk = false;
      try {
        const u = new URL(AGENTSCOPE_URL + '/api/model-config');
        const lib = u.protocol === 'https:' ? https : http;
        pyOk = await new Promise((resolve) => {
          const up = lib.request({
            hostname: u.hostname,
            port: u.port || (u.protocol === 'https:' ? 443 : 80),
            path: u.pathname,
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
            timeout: 3000,
          }, (r) => { r.resume(); r.on('end', () => resolve(r.statusCode >= 200 && r.statusCode < 300)); });
          up.on('timeout', () => { up.destroy(); resolve(false); });
          up.on('error', () => resolve(false));
          up.end(body);
        });
      } catch (_) { /* Python 未就绪：内存里已有配置，页面仍可用，自动审核等它起来 */ }
      json(res, 200, { ok: true, synced: pyOk });
    })();
  }

  /* 读全局模型配置：页面启动时用它回填表单（换浏览器也不用重填） */
  if (req.method === 'GET' && url.pathname === '/api/model-config') {
    return (async () => {
      try {
        const r = await fetchAgentJson('/api/model-config');
        const c = (r && r.cfg) || {};
        if (c.base && c.model) {
          modelCfg.base = String(c.base || '');
          modelCfg.key = String(c.key || '');
          modelCfg.model = String(c.model || '');
        }
        return json(res, 200, { ok: true, cfg: { base: modelCfg.base, key: modelCfg.key, model: modelCfg.model } });
      } catch (e) {
        /* Python 没起来：退回 Node 内存里那份（至少页面还能显示上次配置） */
        return json(res, 200, { ok: true, cfg: { base: modelCfg.base, key: modelCfg.key, model: modelCfg.model } });
      }
    })();
  }

  /* 本轮对话上下文：每次发送前覆盖，只影响紧接着的那一次请求。
   * 刻意不落库、不进 modelCfg —— 它属于「这一轮对话」，不是系统配置。 */
  if (req.method === 'POST' && url.pathname === '/api/run-context') {
    return (async () => {
      try {
        const p = JSON.parse(await readBody(req, 1 * 1024 * 1024));
        runCtx.profile = p.profile && typeof p.profile === 'object' ? p.profile : null;
        runCtx.agentMode = p.agentMode === 'review' ? 'review' : 'interview';
        runCtx.review = p.review && typeof p.review === 'object' ? p.review : null;
        runCtx.repoId = typeof p.repoId === 'string' ? p.repoId : '';
        if (typeof p.runId === 'string' && p.runId) {
          runCtx.runId = p.runId;
          stageBucket(p.runId, true); // 新一轮，保证阶段事件桶存在
        }
        return json(res, 200, { ok: true });
      } catch (e) {
        return json(res, 400, { error: { message: '请求体解析失败：' + e.message } });
      }
    })();
  }

  /* 智能体阶段事件订阅（SSE）：前端流水线面板用（留在本服务，与适配器同进程） */
  if (req.method === 'GET' && url.pathname === '/api/stages') {
    const runId = url.searchParams.get('runId') || '';
    if (!runId) return json(res, 400, { error: { message: '缺少 runId' } });
    const b = stageBucket(runId, true);
    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
      'Access-Control-Allow-Origin': '*',
    });
    for (const ev of b.events.slice(-40)) {
      res.write('data: ' + JSON.stringify(ev) + '\n\n');
    }
    b.clients.add(res);
    const hb = setInterval(() => { try { res.write(': ping\n\n'); } catch (_) {} }, 15000);
    req.on('close', () => { clearInterval(hb); b.clients.delete(res); });
    return;
  }

  /* 健康检查：智能体连通性 + 持久层状态（持久层在 Python 侧，一起汇总） */
  if (req.method === 'GET' && url.pathname === '/api/health') {
    return (async () => {
      const agent = await checkAgentHealth();
      let dbInfo = { available: false };
      try {
        const h = await fetchAgentJson('/health');
        dbInfo = (h && h.db) || { available: false, reason: 'Python 服务未返回数据库状态' };
      } catch (e) {
        dbInfo = { available: false, reason: e.message };
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

  /* 技能：专家的技能绑定关系（只读，来自 backend/skills/） */
  if (req.method === 'GET' && url.pathname === '/api/skills') {
    return void proxyApi(req, res, url.pathname + url.search);
  }

  /* 代码审核：仓库 / PR / 审核报告（Python 提供，含异步轮询与 SSE 事件流回放） */
  if (url.pathname === '/api/repos' || url.pathname.startsWith('/api/repos/')) {
    return void proxyApi(req, res, url.pathname + url.search);
  }
  if (url.pathname === '/api/reviews' || url.pathname.startsWith('/api/reviews/')) {
    return void proxyApi(req, res, url.pathname + url.search);
  }
  if (req.method === 'GET' && url.pathname === '/api/review/events') {
    return void proxyApi(req, res, url.pathname + url.search);
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
  /* 端口撞车保护：页面服务必须和智能体服务分开。
   * 一旦两者同端口（例如外部继承了 PORT=8790），Windows 会按地址族分裂：
   * localhost(IPv6) 打到 Node、127.0.0.1(IPv4) 打到 Python，
   * 于是「页面保存了配置」和「接口读到的配置」可能落在不同进程，极难排查。 */
  if (USE_AGENTSCOPE) {
    try {
      const ap = new URL(AGENTSCOPE_URL).port || '80';
      if (String(ap) === String(PORT)) {
        console.log('  ➜  ⚠️ 端口冲突: 页面服务与智能体服务都用了 ' + PORT + '，请改用 8787');
        console.log('      （常见原因：批处理里残留 PORT 变量，或手动 set PORT=' + PORT + '）');
      }
    } catch (_) { /* AGENTSCOPE_URL 不合法时忽略 */ }
  }
  if (USE_AGENTSCOPE) {
    const agent = await checkAgentHealth();
    if (agent.ok) {
      console.log('  ➜  智能体:     AgentScope 已连接（' + (agent.detail || AGENTSCOPE_URL) + '）');
      /* 全局模型配置以 Python 侧 setting 表为准，启动即读回（猜它可能晚于本进程启动，重试一次） */
      await hydrateModelCfg();
      setTimeout(hydrateModelCfg, 4000);
    } else {
      console.log('  ➜  智能体:     ⚠️ 未连接（' + AGENTSCOPE_URL + '），出题会失败');
      console.log('                请先启动智能体服务：.venv\\Scripts\\python.exe backend\\run.py');
      setTimeout(hydrateModelCfg, 4000);
    }
  } else {
    console.log('  ➜  智能体:     已关闭（AGENTSCOPE=0，直接转发模型 API）');
  }
  /* 持久化与 PR 轮询都在 Python 侧，启动时探测一次并把状态打出来 */
  if (USE_AGENTSCOPE) {
    try {
      const r = await fetchAgentJson('/health');
      const d = (r && r.db) || null;
      if (d && d.available) {
        console.log('  ➜  历史库:     SQLite 就绪 · ' + d.convos + ' 场会话 / ' + d.messages + ' 条消息');
        console.log('                ' + d.path + '（' + d.sizeKB + ' KB，备份=复制此文件）');
        console.log('  ➜  代码审核:   ' + d.repos + ' 个仓库 / ' + d.reviews + ' 份审核报告 · 轮询调度在 Python 侧');
      } else {
        console.log('  ➜  历史库:     ⚠️ 状态未知（' + ((d && (d.reason || d.error)) || 'Python 服务尚未就绪') + '）');
      }
    } catch (e) {
      console.log('  ➜  历史库:     ⚠️ 无法读取状态（' + e.message + '），Python 服务可能还没起来');
    }
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
