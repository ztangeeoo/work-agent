import type { AgentDef, AgentMode, SkillBrief, SkillsPayload } from '../types';
import { stageNames } from '../lib/skills';

interface Props {
  agents: AgentDef[];
  /** 当前正在使用的专家，卡片上加「当前」标记 */
  mode: AgentMode;
  /** 技能绑定关系；服务端不可用时为 null，卡片自动隐藏技能区 */
  skills?: SkillsPayload | null;
  /** 选中专家 → 由上层弹出该专家的任务配置 */
  onPick: (m: AgentMode) => void;
  /** 返回当前对话 */
  onBack: () => void;
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
 * 由左侧「专家」入口进入；选中某位专家后弹出任务配置，确认即新建一个任务会话。
 * 每张卡片底部列出这位专家实际装载的技能：专属的在上，全局共用的在下。
 */
export default function ExpertPicker({ agents, mode, skills, onPick, onBack }: Props) {
  const globals = skills?.assignments.global || [];

  return (
    <div className="expert-view">
      <div className="expert-view-inner">
        <div className="expert-view-head">
          <h1>选择一位专家，开启新任务</h1>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onBack}>← 返回对话</button>
        </div>
        <p className="lead">
          选中专家后填写任务信息，确认即新建一个任务会话。会话与专家永久绑定，之后不能更换；
          想换专家就新建任务。
        </p>
        <div className="expert-grid">
          {agents.map(a => {
            const own = skills?.assignments.experts.find(e => e.id === a.id)?.skills || [];
            const total = own.length + globals.length;
            return (
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

                <div className="expert-go">{a.cta || '开始'} <span aria-hidden="true">→</span></div>
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
