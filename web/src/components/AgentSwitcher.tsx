import type { AgentDef, AgentMode } from '../types';

interface Props {
  agents: AgentDef[];
  mode: AgentMode;
  onSwitch: (m: AgentMode) => void;
  onOpenConfig: () => void;
  configLabel: string;
}

/**
 * 聊天框上方的智能体选择器：并排展示可用的智能体，点击切换。
 * 与面试出题官一致——选中态高亮，右侧是当前模式对应的配置入口。
 */
export default function AgentSwitcher({ agents, mode, onSwitch, onOpenConfig, configLabel }: Props) {
  return (
    <div className="agent-bar">
      <div className="agent-switch">
        {agents.map(a => {
          const active = a.mode === mode;
          return (
            <div
              key={a.id}
              className={'agent-card' + (active ? ' active' : '') + (a.mode === 'review' ? ' agent-review' : '')}
              onClick={() => onSwitch(a.mode)}
              title={active ? '当前智能体' : '切换到「' + a.name + '」'}
            >
              <span className="agent-icon">{a.icon}</span>
              <div className="agent-info">
                <div className="agent-name">
                  {a.name}
                  {active && <span className="agent-badge">当前</span>}
                </div>
                <div className="agent-desc">{a.desc}</div>
              </div>
            </div>
          );
        })}
      </div>
      <button className="btn btn-ghost btn-sm" onClick={onOpenConfig}>⚙️ {configLabel}</button>
    </div>
  );
}
