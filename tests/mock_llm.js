/* mock OpenAI 兼容 LLM 服务：用于验证 CopilotKit → AgentScopeAdapter → 上游 全链路 */
const http = require('http');

http.createServer((req, res) => {
  let body = '';
  req.on('data', c => body += c);
  req.on('end', () => {
    console.log('[mock] 收到请求: ' + body.slice(0, 300));
    res.writeHead(200, { 'Content-Type': 'text/event-stream; charset=utf-8' });
    const chunks = [
      '**第 1 题：进程与线程的区别**\n请从调度、内存、通信三个角度解释。\n【考察点】操作系统\n\n',
      '**第 2 题：MySQL 索引什么时候失效**\n请列举至少三种场景。\n追问：那 like %x 为什么绕不过索引？',
    ];
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
    }, 80);
  });
}).listen(9999, '127.0.0.1', () => console.log('[mock] LLM 已启动: http://127.0.0.1:9999/v1'));
