/**
 * AgentScope 服务适配器（CopilotKit ServiceAdapter）
 * 作用：把 CopilotKit runtime 的聊天请求转成智能体服务（app/）的
 *       OpenAI 兼容请求（/v1/chat/completions），SSE 流转发回 CopilotKit。
 *       「策划官 / 面试官 / 评分官」三智能体流水线因此完整保留。
 *
 * 模型配置（base/key/model/profile）由前端在每次发消息前 POST 到
 * /api/model-config，存于 server.js 内存，本适配器通过 getCfg() 读取。
 */

function createAgentScopeAdapter({ agentBase, getCfg, onStage }) {
  return {
    name: 'AgentScopeAdapter',

    async process(request) {
      const cfg = getCfg() || {};
      if (!cfg.base || !cfg.model) {
        throw new Error('模型未配置：请先点击右上角齿轮图标完成「模型配置」');
      }

      // CopilotKit Message（AG-UI 格式）→ OpenAI messages
      const messages = [];
      for (const m of request.messages || []) {
        if (typeof m.isTextMessage === 'function' && m.isTextMessage()) {
          messages.push({ role: m.role, content: m.content });
        }
      }
      if (!messages.length) throw new Error('没有可发送的消息');

      let res;
      try {
        res = await fetch(agentBase + '/v1/chat/completions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            base: cfg.base,
            key: cfg.key || '',
            model: cfg.model,
            messages,
            profile: cfg.profile || {},
            /* review 模式：跳过面试三智能体，走「代码审核官」追问 */
            agent_mode: cfg.agentMode === 'review' ? 'review' : 'interview',
            review: cfg.agentMode === 'review' ? (cfg.review || null) : null,
            /* 当前仓库 id：Python 侧据此绑定只读仓库工具（列 MR / 拉 diff / 读文件） */
            repo_id: cfg.agentMode === 'review' ? (cfg.repoId || '') : '',
            temperature: 0.7,
            stream: true,
          }),
        });
      } catch (e) {
        throw new Error('无法连接智能体服务（' + agentBase + '）：' + (e.message || e));
      }

      if (!res.ok) {
        let msg = 'HTTP ' + res.status;
        try {
          const j = await res.json();
          msg = (j.error && j.error.message) || j.message || msg;
        } catch (_) {}
        throw new Error(msg);
      }

      // SSE 流转发给 CopilotKit 的事件流
      request.eventSource.stream(async (eventStream$) => {
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        const messageId = 'msg_' + Date.now();
        let started = false;
        const start = () => {
          if (!started) {
            eventStream$.sendTextMessageStart({ messageId });
            started = true;
          }
        };
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buf += dec.decode(value, { stream: true });
            const lines = buf.split('\n');
            buf = lines.pop();
            for (const line of lines) {
              const s = line.trim();
              if (!s.startsWith('data:')) continue;
              const payload = s.slice(5).trim();
              if (!payload || payload === '[DONE]') continue;
              try {
                const j = JSON.parse(payload);
                // 智能体阶段事件：不进入正文，只上报给流水线面板
                if (j && j.agent_stage) {
                  if (typeof onStage === 'function') {
                    try { onStage(j.agent_stage, cfg); } catch (_) {}
                  }
                  continue;
                }
                const d = j.choices && j.choices[0] && j.choices[0].delta && j.choices[0].delta.content;
                if (d) {
                  start();
                  eventStream$.sendTextMessageContent({ messageId, content: d });
                }
              } catch (_) {}
            }
          }
          if (started) eventStream$.sendTextMessageEnd({ messageId });
        } catch (e) {
          start();
          eventStream$.sendTextMessageContent({ messageId, content: '\n\n❌ 流式传输中断：' + (e.message || e) });
          eventStream$.sendTextMessageEnd({ messageId });
        }
        eventStream$.complete();
      });

      return { threadId: request.threadId || 'th_' + Date.now() };
    },
  };
}

module.exports = { createAgentScopeAdapter };
