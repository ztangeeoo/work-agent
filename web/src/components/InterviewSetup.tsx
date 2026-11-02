import { useEffect, useRef, useState } from 'react';
import type { ModelCfg, Profile } from '../types';
import { EXPS, INDUSTRIES, LEVELS, TRACKS, TRACK_SUBS } from '../data/tracks';
import { IMG_EXT, parseResume } from '../lib/resume';

interface Props {
  /** 弹框是否打开 */
  open: boolean;
  onClose: () => void;
  profile: Profile;
  onProfileChange: (p: Profile) => void;
  onConfirm: () => void;
  genErr: string;
  cfg: ModelCfg;
  onNeedCfg: () => void;
  toast: (msg: string) => void;
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
 * 面试配置弹框。
 * 点输入框上方的「⚙️ 面试配置」打开，选好方向 / 级别 / 简历后确认生成；
 * 生成后自动关闭，把屏幕还给对话。
 */
export default function InterviewSetup({
  open, onClose, profile, onProfileChange, onConfirm, genErr, cfg, onNeedCfg, toast,
}: Props) {
  const [hint, setHint] = useState('上传简历后，AI 将结合简历内容针对性出题');
  const [thumb, setThumb] = useState('');
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const thumbRef = useRef('');

  useEffect(() => () => { if (thumbRef.current) URL.revokeObjectURL(thumbRef.current); }, []);

  /* Esc 关闭（与模型配置弹框一致的操作习惯） */
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

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

  return (
    <div className="modal-mask show" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal modal-wide modal-tall">
        <div className="modal-head">
          <span className="modal-title">🎯 面试出题官 · 面试配置</span>
          <button className="modal-close" onClick={onClose} title="关闭">✕</button>
        </div>

        <div className="modal-body">
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
          <div
            className={'upload-box' + (drag ? ' drag' : '')}
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              const f = e.dataTransfer.files?.[0];
              if (f) handleFile(f);
            }}
          >
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
        </div>

        {/* 操作区固定在弹框底部：表单再长也点得到「确认」 */}
        <div className="modal-foot">
          <button className="btn btn-ghost btn-sm" onClick={onClose}>取消</button>
          <div className="spacer"></div>
          <button className="btn btn-primary btn-sm" onClick={() => {
            if (!cfg.base || !cfg.model) {
              onNeedCfg();
              toast('❌ 请先完成「模型配置」');
              return;
            }
            onConfirm();
          }}>🚀 确认并生成面试题</button>
        </div>
      </div>
    </div>
  );
}
