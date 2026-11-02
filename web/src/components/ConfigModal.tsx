import { useState } from 'react';
import type { ModelCfg } from '../types';

interface Props {
  open: boolean;
  onClose: () => void;
  cfg: ModelCfg;
  onSave: (cfg: ModelCfg) => void;
  toast: (msg: string) => void;
}

export default function ConfigModal({ open, onClose, cfg, onSave, toast }: Props) {
  const [base, setBase] = useState(cfg.base);
  const [key, setKey] = useState(cfg.key);
  const [model, setModel] = useState(cfg.model);
  const [models, setModels] = useState<string[]>([]);
  const [conn, setConn] = useState('');
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const cur = (): ModelCfg => ({ base: base.trim(), key: key.trim(), model: model.trim() });

  const pullModels = async () => {
    const c = cur();
    if (!c.base) { toast('❌ 请先填写 API 地址'); return; }
    setBusy(true);
    try {
      const res = await fetch('/api/models', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ base: c.base, key: c.key }),
      });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(j.error?.message || ('HTTP ' + res.status));
      const list: string[] = j.models || [];
      if (!list.length) { toast('未返回模型列表，可手动填写模型名'); return; }
      setModels(list);
      if (!model.trim()) { setModel(list[0]); }
      toast(`✅ 获取到 ${list.length} 个模型，点模型名可下拉选择`);
    } catch (err: any) {
      toast('❌ ' + (err.message || err));
    } finally {
      setBusy(false);
    }
  };

  const test = async () => {
    const c = cur();
    if (!c.base || !c.model) { toast('❌ 请先填写 API 地址和模型名'); return; }
    setConn('测试中…');
    try {
      const res = await fetch('/api/chat', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          base: c.base, key: c.key, model: c.model,
          messages: [{ role: 'user', content: '回复：ok' }], max_tokens: 8, stream: false,
        }),
      });
      if (res.ok) {
        setConn('已连接 · ' + c.model);
        toast('✅ 模型连接成功');
      } else {
        let m = 'HTTP ' + res.status;
        try { const e = await res.json(); m = e.error?.message || m; } catch (_) {}
        setConn('连接失败');
        toast('❌ ' + m);
      }
    } catch (err: any) {
      setConn('连接失败');
      toast('❌ 本地服务未启动：请运行 start.bat');
    }
  };

  return (
    <div className="modal-mask show" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="modal">
        <div className="modal-head">
          <span className="modal-title">模型配置</span>
          <button className="modal-close" onClick={onClose} title="关闭">✕</button>
        </div>
        <div className="modal-body">
          <label className="field">API 地址（OpenAI 兼容）</label>
          <input type="url" value={base} placeholder="https://api.deepseek.com/v1"
            onChange={(e) => setBase(e.target.value)} />
          <div className="hint">常见：DeepSeek <code>https://api.deepseek.com/v1</code> · 通义 <code>https://dashscope.aliyuncs.com/compatible-mode/v1</code> · Kimi <code>https://api.moonshot.cn/v1</code> · Ollama <code>http://localhost:11434/v1</code></div>
          <label className="field">API Key</label>
          <input type="password" value={key} placeholder="sk-...（Ollama 本地模型可留空）"
            onChange={(e) => setKey(e.target.value)} />
          <label className="field">模型名称</label>
          <input type="text" list="modelOptions" value={model} placeholder="deepseek-chat"
            onChange={(e) => setModel(e.target.value)} />
          <datalist id="modelOptions">
            {models.map(m => <option key={m} value={m} />)}
          </datalist>
          <div className="hint">DeepSeek: <code>deepseek-chat</code> · 通义: <code>qwen-plus</code> · Kimi: <code>moonshot-v1-8k</code> · Ollama: <code>llama3.1</code></div>
          <div className="conn-row">
            <span className={'conn-dot ' + (conn.startsWith('已连接') ? 'ok' : conn === '连接失败' ? 'err' : '')}></span>
            <span>{conn || '未连接模型'}</span>
          </div>
          <div className="modal-actions">
            <button className="btn btn-ghost btn-sm" disabled={busy} onClick={pullModels}>{busy ? '拉取中…' : '拉取模型列表'}</button>
            <button className="btn btn-ghost btn-sm" onClick={() => { onSave(cur()); onClose(); }}>保存配置</button>
            <button className="btn btn-primary btn-sm" onClick={test}>测试连接</button>
          </div>
        </div>
      </div>
    </div>
  );
}
