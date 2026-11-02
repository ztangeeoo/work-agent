/* 本地服务健康检查：同源 → 当前主机:8787 → localhost:8787 */

const PROXY_FALLBACK = 'http://localhost:8787';

export interface HealthState {
  api: string;        // 可用的 API 前缀（'' = 同源）
  proxyOk: boolean;
  agentOk: boolean;
}

export function proxyCandidates(): string[] {
  const list = [''];
  const h = location.hostname;
  if (h && h !== 'localhost' && h !== '127.0.0.1') list.push('http://' + h + ':8787');
  list.push(PROXY_FALLBACK);
  return [...new Set(list)];
}

async function ping(origin: string): Promise<any> {
  try {
    const ctl = new AbortController();
    const t = setTimeout(() => ctl.abort(), 2500);
    const r = await fetch(origin + '/api/health', { cache: 'no-store', signal: ctl.signal });
    clearTimeout(t);
    if (!r.ok) return false;
    return await r.json();
  } catch (_) { return false; }
}

export async function checkHealth(): Promise<HealthState> {
  for (const origin of proxyCandidates()) {
    const j = await ping(origin);
    if (j && j.ok) {
      return { api: origin, proxyOk: true, agentOk: j.agentOk !== false };
    }
  }
  return { api: '', proxyOk: false, agentOk: false };
}
