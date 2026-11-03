import type { AgentDef, AgentMode } from '../types';

interface Props {
  agents: AgentDef[];
  /** 当前正在使用的专家，卡片上加「当前」标记 */
  mode: AgentMode;
  /** 选中专家 → 由上层弹出该专家的任务配置 */
  onPick: (m: AgentMode) => void;
  /** 返回当前对话 */
  onBack: () => void;
}

/**
 * 右侧的专家选择视图。
 * 由左侧「专家」入口进入；选中某位专家后弹出任务配置，确认即新建一个任务会话。
 */
export default function ExpertPicker({ agents, mode, onPick, onBack }: Props) {
  return (
    <div className="expert-view">
      <div className="expert-view-inner">
        <div className="expert-view-head">
          <h1>选择一位专家，开启新任务</h1>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onBack}>← 返回对话</button>
        </div>
        <p className="lead">
          选中专家后填写任务信息，确认即新建一个独立任务会话；历史会话会记住它属于哪位专家。
        </p>
        <div className="expert-grid">
          {agents.map(a => (
            <button
              key={a.id}
              type="button"
              className={'expert-card' + (a.mode === mode ? ' current' : '')}
              onClick={() => onPick(a.mode)}
            >
              <div className="expert-card-top">
                <span className="expert-avatar">{a.icon}</span>
                <div className="expert-card-title">
                  <div className="expert-name">
                    {a.name}
                    {a.mode === mode && <span className="expert-now">当前</span>}
                  </div>
                  <div className="expert-role">{a.role}</div>
                </div>
              </div>
              <div className="expert-desc">{a.desc}</div>
              <div className="expert-tags">
                {(a.tags || []).map(t => <span key={t} className="expert-tag">{t}</span>)}
              </div>
              <div className="expert-go">{a.cta || '开始'} <span aria-hidden="true">→</span></div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
