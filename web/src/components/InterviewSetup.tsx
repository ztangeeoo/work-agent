import { useEffect, useRef, useState } from 'react';
import type { ModelCfg, Profile } from '../types';
import { EXPS, INDUSTRIES, LEVELS, TRACKS, TRACK_SUBS } from '../data/tracks';
import { IMG_EXT, parseResume } from '../lib/resume';

interface Props {
  /** 是否展开；收起后只显示一行摘要 */
  open: boolean;
  onToggle: () => void;
  profile: Profile;
  onProfileChange: (p: Profile) => void;
  onConfirm: () => void;
  genErr: string;
  cfg: ModelCfg;
  onNeedCfg: () => void;
  toast: (msg: string) => void;
  /** 已有对话时，收起状态下提示「点这里改配置」 */
  compact?: boolean;
}

function ChipGroup({ items, value, onPick }: { items: string[]; value: string; onPick: (v: string) => void }) {
  return (
    <div className="chip-group">
      {items.map(v => (
        <span key={v} className={'chip' + (value === v ? ' active' : '')} onClick={() => onPick(v)}>{v}</span>
      ))}
    </div>
  );
}

/**
 * 面试配置卡（内联，不用弹框）。
 * 直接出现在对话流里，用户只需点选 / 输入，然后确认生成。
 */
export default function InterviewSetup({
  open, onToggle, profile, onProfileChange, onConfirm, genErr, cfg, onNeedCfg, toast, compact,
}: Props) {
  const [hint, setHint] = useState('上传简历后，AI 将结合简历内容针对性出题');
  const [thumb, setThumb] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const thumbRef = useRef('');

  useEffect(() => () => { if (thumbRef.current) URL.revokeObjectURL(thumbRef.current); }, []);

  const set = (patch: Partial<Profile>) => onProfileChange({ ...profile, ...patch });

  const handleFile = async (file: File) => {
    toast('解析简历中…');
    try {
      const text = await parseResume(file, cfg, setHint);
      if (!text || !text.trim()) throw new Error('未能提取到文本内容');
      set({ resumeText: text, resumeName: file.name });
      if (IMG_EXT.includes(file.name.split('.').pop()!.toLowerCase())) {
        if (thumbRef.current) URL.revokeObjectURL(thumbRef.current);
        thumbRef.current = URL.createObjectURL(file);
        setThumb(thumbRef.current);
      } else {
        setThumb('');
      }
      setHint('✅ 简历已解析，出题将针对简历内容');
      toast('✅ 简历解析成功');
    } catch (err: any) {
      toast('❌ ' + (err.message || err));
    }
  };

  const subs = TRACK_SUBS[profile.track] || [];
  const subItems = ['随机', ...subs];
  const summary = [
    profile.track,
    profile.sub && profile.sub !== '随机' ? profile.sub : '细分随机',
    profile.level,
    profile.exp,
    profile.industry === '不限' ? '' : profile.industry,
    `${profile.count} 道`,
    profile.resumeName ? '含简历' : '',
  ].filter(Boolean).join(' · ');

  return (
    <div className={'setup-card' + (open ? '' : ' is-folded')}>
      <div className="setup-head" onClick={onToggle}>
        <span className="setup-icon">🎯</span>
        <div className="setup-titles">
          <div className="setup-title">面试配置</div>
          <div className="setup-sub">{open ? '选好方向与级别，点下面按钮出题' : summary}</div>
        </div>
        <div className="spacer"></div>
        <span className="setup-fold">{open ? '收起' : (compact ? '展开修改' : '展开')}</span>
      </div>

      {open && (
        <div className="setup-body">
          <label className="field">技术方向</label>
          <ChipGroup items={TRACKS} value={profile.track}
            onPick={(v) => set({ track: v, sub: '随机' })} />
          <label className="field">细分方向（可不选，默认随机）</label>
          <ChipGroup items={subItems} value={profile.sub} onPick={(v) => set({ sub: v })} />
          <div className="row2">
            <div>
              <label className="field">级别</label>
              <ChipGroup items={LEVELS} value={profile.level} onPick={(v) => set({ level: v })} />
            </div>
            <div>
              <label className="field">工作经验</label>
              <ChipGroup items={EXPS} value={profile.exp} onPick={(v) => set({ exp: v })} />
            </div>
          </div>
          <label className="field">行业背景</label>
          <ChipGroup items={INDUSTRIES} value={profile.industry} onPick={(v) => set({ industry: v })} />
          <div className="row2" style={{ marginTop: 12 }}>
            <div>
              <label className="field">题目数量</label>
              <div className="qcount">
                <input type="text" value={profile.count}
                  onChange={(e) => { const v = parseInt(e.target.value); if (v > 0 && v <= 50) set({ count: v }); }} />
                <span className="hint">道</span>
              </div>
            </div>
            <div>
              <label className="field">出题语言</label>
              <select value={profile.lang} onChange={(e) => set({ lang: e.target.value })}>
                <option>中文</option>
                <option>英文</option>
                <option>中英混合</option>
              </select>
            </div>
          </div>
          <label className="field">补充要求（可选）</label>
          <input type="text" value={profile.note} placeholder="如：偏重项目实战、侧重系统设计…"
            onChange={(e) => set({ note: e.target.value })} />
          <label className="field">候选人简历（可选）</label>
          <div className="upload-box" onClick={() => fileRef.current?.click()}>
            <div className="icon">📄</div>
            <div>点击或拖拽上传简历<br /><span style={{ fontSize: 11 }}>支持 .pdf / .docx / .txt / .md / 图片(.png .jpg)</span></div>
          </div>
          <input ref={fileRef} type="file" accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.webp,.bmp" style={{ display: 'none' }}
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }} />
          {profile.resumeName && (
            <div className="file-badge" style={{ display: 'flex' }}>
              <span>{thumb ? <img src={thumb} alt="" style={{ width: 26, height: 26, objectFit: 'cover', borderRadius: 4, display: 'block' }} /> : '📄'}</span>
              <span className="name">
                {profile.resumeName}（{profile.resumeText ? profile.resumeText.length + ' 字' : '原文未保留，请重新上传'}）
              </span>
              <span className="del" onClick={() => { set({ resumeText: '', resumeName: '' }); setThumb(''); setHint('上传简历后，AI 将结合简历内容针对性出题'); }}>✕</span>
            </div>
          )}
          <div className="hint">{hint}</div>
          {genErr && <div className="err-text" style={{ display: 'block' }}>{genErr}</div>}

          <div className="setup-actions">
            <button className="btn btn-ghost btn-sm" onClick={onToggle}>稍后再说</button>
            <div className="spacer"></div>
            <button className="btn btn-primary btn-sm" onClick={() => {
              if (!cfg.base || !cfg.model) {
                onNeedCfg();
                toast('❌ 请先完成「模型配置」');
                return;
              }
              onConfirm();
            }}>🚀 生成面试题</button>
          </div>
        </div>
      )}
    </div>
  );
}
