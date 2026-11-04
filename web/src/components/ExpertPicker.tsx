import type { AgentInfo, AgentMode, ExpertDef, SkillBrief, SkillsPayload } from '../types';
import { stageNames } from '../lib/skills';

interface Props {
  /** 专家清单：服务端 expert 表（每个专家带自己挂的智能体） */
  experts: ExpertDef[];
  /** 当前会话所属专家；null 表示还没关联专家，卡片上不标「当前」 */
  mode: AgentMode | null;
  /** 当前会话绑定的智能体 id（会话 → 智能体 → 专家 中间那一层） */
  agentId: string | null;
  /** 技能绑定关系；服务端不可用时为 null，卡片自动隐藏技能区 */
  skills?: SkillsPayload | null;
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

/** 技能芯片：专属技能与全局技能用不同样式区分 */
function SkillChip({ skill, global }: { skill: SkillBrief; global?: boolean }) {
  const label = skill.title || skill.name;
  const title = [
    skill.description,
    global ? '全局技能：所有专家共用' : `作用于：${stageNames(skill.stages)}`,
    `标识：${skill.name}`,
  ].filter(Boolean).join('\n');
  return (
    <span
      className={'expert-skill' + (global ? ' is-global' : '')}
      title={title}
    >
      {global && <i className="expert-skill-mark">全局</i>}
      {label}
    </span>
  );
}

/**
 * 右侧的专家选择视图。
 * 由左侧「专家」入口进入；选中某位专家后弹出任务配置，确认即把「专家 + 它的主智能体」
 * 绑定到当前空白会话（已有内容的会话则新建一个任务会话）。
 *
 * 卡片上分两层：专家挂的智能体，以及这些智能体实际装载的技能
 * （专属的在上，全局共用的在下）。
 */
export default function ExpertPicker({ experts, mode, agentId, skills, onPick, onBack }: Props) {
  const globals = skills?.assignments.global || [];

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
            /* 技能归属按 expert 的 mode 对齐（库里 id 与 mode 一一对应，两个键都试一次） */
            const binding = skills?.assignments.experts.find(
              x => x.mode === e.mode || x.id === e.id,
            );
            const own = binding?.skills || [];
            const total = own.length + globals.length;
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
                {/* 技能：这些智能体实际装载的技能 */}
                {skills && (
                  <div className="expert-skills">
                    <div className="expert-skills-head">
                      <span className="expert-skills-title">技能</span>
                      <span className="expert-skills-count">{total}</span>
                    </div>
                    {total === 0 ? (
                      <div className="expert-skills-empty">尚未绑定技能</div>
                    ) : (
                      <div className="expert-skill-list">
                        {own.map(s => <SkillChip key={s.id} skill={s} />)}
                        {globals.map(s => <SkillChip key={s.id} skill={s} global />)}
                      </div>
                    )}
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

        {skills && skills.assignments.unassigned.length > 0 && (
          <div className="skill-warn">
            <div className="skill-warn-head">
              有 {skills.assignments.unassigned.length} 个技能未生效
            </div>
            <ul className="skill-warn-list">
              {skills.assignments.unassigned.map(u => (
                <li key={u.id}>
                  <code>{u.id}</code>
                  <span>{u.issues.join('；')}</span>
                </li>
              ))}
            </ul>
            <p className="skill-warn-tip">
              在对应 <code>SKILL.md</code> 的 frontmatter 里补上
              <code>scope: global</code> 或 <code>scope: expert</code> + <code>experts</code>
              后即生效，不用重启。
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
