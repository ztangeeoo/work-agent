import { useEffect, useState } from 'react';
import type { Repo } from '../types';
import { removeRepo, saveRepo, testRepo } from '../lib/review';

interface Props {
  /** 是否展开 */
  open: boolean;
  onToggle: () => void;
  /** 当前要编辑的仓库；null = 新增 */
  repo: Repo | null;
  /** 是否已有仓库（用于文案） */
  hasRepo: boolean;
  onSaved: (repo: Repo, isNew: boolean) => void;
  onDeleted: (id: string) => void;
  toast: (msg: string) => void;
}

const INTERVALS = [
  { v: 30, label: '30 秒' },
  { v: 60, label: '1 分钟' },
  { v: 120, label: '2 分钟' },
  { v: 300, label: '5 分钟' },
  { v: 600, label: '10 分钟' },
  { v: 1800, label: '30 分钟' },
];

/**
 * 仓库配置卡（内联，不用弹框）。
 * 首次进入审核模式时会自动展开，用户填地址即可；已有仓库时收起成一行摘要。
 */
export default function RepoSetup({ open, onToggle, repo, hasRepo, onSaved, onDeleted, toast }: Props) {
  const [url, setUrl] = useState('');
  const [provider, setProvider] = useState('auto');
  const [token, setToken] = useState('');
  const [intervalSec, setIntervalSec] = useState(120);
  const [autoPost, setAutoPost] = useState(false);
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState('');
  const [err, setErr] = useState('');
  const [testInfo, setTestInfo] = useState('');

  /* 展开时用当前仓库回填（切换仓库/首次打开都会刷新） */
  useEffect(() => {
    if (!open) return;
    setUrl(repo?.url || '');
    setProvider(repo ? repo.provider : 'auto');
    setToken('');
    setIntervalSec(repo?.intervalSec || 120);
    setAutoPost(!!repo?.autoPost);
    setEnabled(repo ? repo.enabled : true);
    setErr('');
    setTestInfo('');
    setBusy('');
  }, [open, repo?.id]);

  const payload = () => ({
    id: repo?.id,
    url,
    provider: provider === 'auto' ? '' : provider,
    /* 编辑时留空 = 不修改已有 Token */
    token: token ? token : (repo ? undefined : ''),
    intervalSec,
    autoPost,
    enabled,
  });

  const doSave = async (silent = false): Promise<Repo | null> => {
    setErr('');
    if (!url.trim()) { setErr('请填写代码仓库地址'); return null; }
    setBusy('save');
    try {
      const saved = await saveRepo(payload());
      onSaved(saved, !repo);
      if (!silent) toast('✅ 仓库已保存');
      return saved;
    } catch (e: any) {
      setErr(e.message || String(e));
      return null;
    } finally {
      setBusy('');
    }
  };

  const doTest = async () => {
    setTestInfo('');
    const saved = await doSave(true);
    if (!saved) return;
    setBusy('test');
    try {
      const r = await testRepo(saved.id);
      setTestInfo('✅ 连接正常，当前有 ' + r.openPrs + ' 个开放 PR');
      toast('✅ 连接正常');
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy('');
    }
  };

  const doDelete = async () => {
    if (!repo) return;
    if (!window.confirm('删除仓库「' + repo.owner + '/' + repo.name + '」？\n该仓库的 PR 记录与审核报告会一起删除。')) return;
    setBusy('del');
    try {
      await removeRepo(repo.id);
      onDeleted(repo.id);
      toast('已删除仓库');
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setBusy('');
    }
  };

  const summary = repo
    ? `${repo.owner}/${repo.name} · 每 ${Math.round(repo.intervalSec / 60) || 1} 分钟轮询 · ${repo.hasToken ? '已配 Token' : '未配 Token'}`
    : '还没有配置仓库，填入代码地址即可开始自动审核';

  return (
    <div className={'setup-card is-review' + (open ? '' : ' is-folded')}>
      <div className="setup-head" onClick={onToggle}>
        <span className="setup-icon">🛡️</span>
        <div className="setup-titles">
          <div className="setup-title">仓库配置</div>
          <div className="setup-sub">{summary}</div>
        </div>
        <div className="spacer"></div>
        <span className="setup-fold">{open ? '收起' : (hasRepo ? '展开修改' : '展开填写')}</span>
      </div>

      {open && (
        <div className="setup-body">
          <label className="field">代码地址</label>
          <input
            type="text"
            value={url}
            placeholder="https://github.com/owner/repo   或   git@github.com:owner/repo.git"
            onChange={(e) => setUrl(e.target.value)}
          />
          <div className="hint">
            支持 GitHub / GitLab（含自建实例）。私有仓库需要在下面填 Access Token；公开仓库可以留空（限流较低）。
          </div>

          <div className="row2" style={{ marginTop: 12 }}>
            <div>
              <label className="field">平台（默认自动识别）</label>
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>
                <option value="auto">自动识别</option>
                <option value="github">GitHub</option>
                <option value="gitlab">GitLab</option>
              </select>
            </div>
            <div>
              <label className="field">轮询间隔</label>
              <select value={intervalSec} onChange={(e) => setIntervalSec(Number(e.target.value))}>
                {INTERVALS.map(i => <option key={i.v} value={i.v}>{i.label}</option>)}
              </select>
            </div>
          </div>

          <label className="field">Access Token（可选）</label>
          <input
            type="password"
            value={token}
            placeholder={repo?.hasToken ? '已配置，留空表示不修改' : 'github_pat_... / glpat-...（只存在本机 SQLite）'}
            onChange={(e) => setToken(e.target.value)}
          />
          <div className="hint">
            GitHub 需要 <code>repo</code> 读权限（回帖还需要 issues 写权限）；GitLab 需要 <code>api</code> 权限。
          </div>

          <div className="row2" style={{ marginTop: 14 }}>
            <label className="check-line">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              <span>启用自动轮询</span>
            </label>
            <label className="check-line">
              <input type="checkbox" checked={autoPost} onChange={(e) => setAutoPost(e.target.checked)} />
              <span>审核完成后自动回帖到 PR</span>
            </label>
          </div>
          <div className="hint">
            自动回帖会以你的账号在 PR 下发表评论，默认关闭；随时也可以在审核报告上手动点「回帖」。
          </div>

          {testInfo && <div className="ok-text" style={{ display: 'block' }}>{testInfo}</div>}
          {err && <div className="err-text" style={{ display: 'block' }}>{err}</div>}

          <div className="setup-actions">
            {repo && (
              <button className="btn btn-ghost btn-sm danger" disabled={!!busy} onClick={doDelete}>
                删除仓库
              </button>
            )}
            <div className="spacer"></div>
            <button className="btn btn-ghost btn-sm" disabled={!!busy} onClick={doTest}>
              {busy === 'test' ? '测试中…' : '测试连接'}
            </button>
            <button className="btn btn-primary btn-sm" disabled={!!busy}
              onClick={() => doSave().then(r => { if (r) onToggle(); })}>
              {busy === 'save' ? '保存中…' : '保存并轮询'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
