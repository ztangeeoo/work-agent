import type { AgentDef } from '../types';

interface Props {
  agent: AgentDef;
  onClickCard: () => void;
  onOpenCfg: () => void;
}

export default function AgentBar({ agent, onClickCard, onOpenCfg }: Props) {
  return (
    <div className="agent-bar">
      <div className="agent-card" onClick={onClickCard} title="点击切换到该智能体，并新建一个会话">
        <span className="agent-icon">{agent.icon}</span>
        <div className="agent-info">
          <div className="agent-name">{agent.name}<span className="agent-badge">当前</span></div>
          <div className="agent-desc">{agent.desc} · 点击卡片切换到该智能体并新建会话</div>
        </div>
      </div>
      <button className="btn btn-ghost btn-sm" onClick={onOpenCfg}>⚙️ 面试配置</button>
    </div>
  );
}
