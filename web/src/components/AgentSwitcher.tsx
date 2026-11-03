import type { AgentDef, AgentMode } from '../types';

interface Props {
  agents: AgentDef[];
  mode: AgentMode;
  onSwitch: (m: AgentMode) => void;
  onOpenConfig: () => void;
  configLabel: string;
  /** 配置卡当前是否展开，用于高亮按钮 */
  configOpen?: boolean;
}

/**
 * 输入框上方的智能体选择条：两个智能体做成分段标签，点击即切换。
 * 切换只改变「谁来回话 + 显示哪张配置卡」，不新建/不切换左侧会话。
 */
export default function AgentSwitcher({ agents, mode, onSwitch, onOpenConfig, configLabel, configOpen }: Props) {
  return (
    <div className="agent-bar">
      <div className="agent-tabs" role="tablist">
        {agents.map(a => {
          const active = a.mode === mode;
          return (
            <button
              key={a.id}
              role="tab"
              aria-selected={active}
              className={'agent-tab' + (active ? ' active' : '')}
              onClick={() => onSwitch(a.mode)}
              title={a.desc}
            >
              {a.name}
            </button>
          );
        })}
      </div>
      <div className="spacer"></div>
      <button
        className={'btn btn-ghost btn-sm' + (configOpen ? ' is-on' : '')}
        onClick={onOpenConfig}
        title={'打开' + configLabel + '弹框'}
      >
        {configLabel}
      </button>
    </div>
  );
}
