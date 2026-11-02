/* mock OpenAI 兼容 LLM 服务：验证 CopilotKit → AgentScopeAdapter → 上游 全链路
 *
 * 用法：node tests/mock_llm.js
 *   - 面试场景：返回两段题目（第 1 题 / 第 2 题）
 *   - 审核场景：按系统提示里的角色（侦察官 / 审核官 / 裁决官）返回对应内容，
 *     审核官与裁决官返回 JSON，供 agent_server 的结构化输出兜底解析
 */
const http = require('http');

const SEARCH_HINTS = {
  scout: [
    '本次改动新增了登录超时重试逻辑，并把超时时间改为可配置。\n',
    '- 涉及文件：src/auth/login.ts、src/config.ts\n',
    '- 重点风险区：重试次数无上限、配置读取可能为空、错误码被吞掉',
  ],
  reviewer: [
    '{"issues":[',
    '{"severity":"blocker","file":"src/auth/login.ts","line":42,"title":"重试没有上限会打爆下游",',
    '"detail":"catch 中无条件递归调用 login()，网络抖动时会无限重试，最终触发连接池耗尽。",',
    '"suggestion":"改成最多重试 3 次，并使用指数退避：for (let i = 0; i < 3; i++) { ... await sleep(2 ** i * 200); }"},',
    '{"severity":"major","file":"src/config.ts","line":12,"title":"配置项缺省值缺失导致 NaN",',
    '"detail":"Number(process.env.LOGIN_TIMEOUT) 在未配置时得到 NaN，setTimeout 会立即触发。",',
    '"suggestion":"const t = Number(process.env.LOGIN_TIMEOUT ?? 3000); if (!Number.isFinite(t)) throw new Error(...)"},',
    '{"severity":"minor","file":"src/auth/login.ts","line":58,"title":"错误信息丢失了原始 cause",',
    '"detail":"catch (e) 后重新抛出新的 Error，原始堆栈被丢弃，线上排查困难。",',
    '"suggestion":"throw new Error(\\"login failed\\", { cause: e })"},',
    '{"severity":"nit","file":"src/auth/login.ts","line":30,"title":"魔法数字 3000",',
    '"detail":"超时时间硬编码，与 config 中的默认值重复。",',
    '"suggestion":"统一从 config 读取。"}',
    '],"notes":"已检查正确性、安全、并发、可维护性四个维度"}',
  ],
  judge: [
    '{"risk":"high","decision":"request_changes","summary":"改动方向合理，但重试没有上限会在故障时放大流量，必须改完再合。",',
    '"positives":["超时时间提成配置项，便于按环境调整","补充了失败日志，便于定位"],',
    '"suggestions":["合入前：给重试加次数上限与退避","合入前：配置解析做非法值兜底","后续：补一个重试次数的单元测试"]}',
  ],
};

const INTERVIEW_CHUNKS = [
  '**第 1 题：进程与线程的区别**\n请从调度、内存、通信三个角度解释。\n【考察点】操作系统\n\n',
  '**第 2 题：MySQL 索引什么时候失效**\n请列举至少三种场景。\n追问：那 like %x 为什么绕不过索引？',
];

function pickChunks(body) {
  /* 用系统提示里的「角色名」精确判断，避免命中用户消息里的引用 */
  if (body.includes('「侦察官」')) return SEARCH_HINTS.scout;
  if (body.includes('「审核官」')) return SEARCH_HINTS.reviewer;
  if (body.includes('「裁决官」')) return SEARCH_HINTS.judge;
  return INTERVIEW_CHUNKS;
}

http.createServer((req, res) => {
  let body = '';
  req.on('data', c => body += c);
  req.on('end', () => {
    const chunks = pickChunks(body);
    console.log('[mock] 收到请求（' + (chunks[0] || '').slice(0, 28).replace(/\n/g, ' ') + '…）');
    res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8' });
    let i = 0;
    const t = setInterval(() => {
      if (i < chunks.length) {
        res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: chunks[i] } }] }) + '\n\n');
        i++;
      } else {
        res.write('data: [DONE]\n\n');
        clearInterval(t);
        res.end();
      }
    }, 40);
  });
}).listen(9999, '127.0.0.1', () => console.log('[mock] LLM 已启动: http://127.0.0.1:9999/v1'));
