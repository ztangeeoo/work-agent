import type { AgentInfo, AgentMode, ExpertDef } from '../types';

interface Props {
  /** 专家清单：服务端 expert 表（每个专家带自己挂的智能体） */
  experts: ExpertDef[];
  /** 当前会话所属专家；null 表示还没关联专家，卡片上不标「当前」 */
  mode: AgentMode | null;
  /** 当前会话绑定的智能体 id（会话 → 智能体 → 专家 中间那一层） */
  agentId: string | null;
  /** 选中专家 → 由上层弹出该专家的任务配置，确认后绑定它的主智能体 */
  onPick: (m: AgentMode) => void;
  /** 返回当前对话 */
  onBack: () => void;
}

/** 主智能体：库里 is_primary=1 的那条，没有就取第一个 */
function primaryOf(e: ExpertDef): AgentInfo | null {
  const list = e.agents || [];
  return list.find(a => a.primary) || list[0] || null;
}

/**
 * 右侧的专家选择视图。
 * 由左侧「专家」入口进入；选中某位专家后弹出任务配置，确认即把「专家 + 它的主智能体」
 * 绑定到当前空白会话（已有内容的会话则新建一个任务会话）。
 */
export default function ExpertPicker({ experts, mode, agentId, onPick, onBack }: Props) {
  return (
    <div className="expert-view">
      <div className="expert-view-inner">
        <div className="expert-view-head">
          <h1>选择一位专家，开启新任务</h1>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onBack}>← 返回对话</button>
        </div>
        <p className="lead">
          专家是能力包，下面挂着一组智能体，会话绑定的是其中那位主智能体。
          当前会话还是空白时会直接绑定它，已有内容的会话则会新建一个任务会话。
        </p>
        <div className="expert-grid">
          {experts.map(e => {
            const primary = primaryOf(e);
            const isCurrent = e.mode === mode;
            return (
              <button
                key={e.id}
                type="button"
                className={'expert-card' + (isCurrent ? ' current' : '')}
                onClick={() => onPick(e.mode)}
              >
                <div className="expert-card-top">
                  <span className="expert-avatar">{e.icon}</span>
                  <div className="expert-card-title">
                    <div className="expert-name">
                      {e.name}
                      {isCurrent && <span className="expert-now">当前</span>}
                    </div>
                    <div className="expert-role">{e.role}</div>
                  </div>
                </div>
                <div className="expert-desc">{e.desc}</div>
                <div className="expert-tags">
                  {(e.tags || []).map(t => <span key={t} className="expert-tag">{t}</span>)}
                </div>
                {/* 专家 → 智能体：把这位专家挂的智能体直接列出来 */}
                {!!(e.agents || []).length && (
                  <div className="expert-agents">
                    <span className="expert-agents-label">智能体</span>
                    {(e.agents || []).map(a => (
                      <span
                        key={a.id}
                        className={'expert-agent'
                          + (a.primary ? ' is-primary' : '')
                          + (agentId && a.id === agentId ? ' is-current' : '')}
                        title={a.role}
                      >
                        {a.icon} {a.name}
                      </span>
                    ))}
                  </div>
                )}
                <div className="expert-go">
                  {e.cta || '开始'}
                  {primary ? <span className="expert-go-agent">· 主智能体 {primary.name}</span> : null}
                  <span aria-hidden="true"> →</span>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
