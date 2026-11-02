import { CopilotKit } from '@copilotkit/react-core';
import ChatApp from './ChatApp';

/**
 * CopilotKit 公开授权 key。
 *
 * 背景：`useCopilotChatHeadless_c`（headless 聊天 hook）在 1.83 属于
 * Intelligence 特性，provider 上必须带一个非空 publicLicenseKey 才放行。
 * 该校验是纯本地布尔检查（Boolean(publicApiKey)），key 只作为
 * `x-copilotcloud-public-api-key` 请求头发往 chatApiEndpoint——
 * 也就是我们的自托管 runtime（/api/copilotkit），不会联网验证、不会外发。
 *
 * 因此：
 *  - 默认使用占位值 'self-hosted-local'，零注册、零外网依赖，适合本地/局域网部署；
 *  - 如果要用 Copilot Cloud 的增值特性（MCP、AI suggestions 等），可去
 *    https://cloud.copilotkit.ai 免费注册，然后把 key 写进 web/.env.local：
 *      VITE_COPILOTKIT_KEY=ckpb_xxx...
 */
const PUBLIC_LICENSE_KEY =
  (import.meta.env.VITE_COPILOTKIT_KEY as string | undefined) || 'self-hosted-local';

/**
 * 前后端分离版入口：
 *  - CopilotKit Provider 指向 Node 服务上的自托管 runtime（/api/copilotkit）
 *  - runtime 内部由 AgentScopeAdapter 转接到 Python 智能体服务（8790），
 *    因此「策划官 / 面试官 / 评分官」三智能体流水线完整保留
 */
export default function App() {
  return (
    <CopilotKit runtimeUrl="/api/copilotkit" publicLicenseKey={PUBLIC_LICENSE_KEY}>
      <ChatApp />
    </CopilotKit>
  );
}
