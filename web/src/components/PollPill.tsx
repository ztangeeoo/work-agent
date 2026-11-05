import type { PollScheduler, Repo } from '../types';
import { pollViewOf, useNow } from '../lib/poll';

interface Props {
  repo: Repo | null;
  /** 轮询调度器状态：进程活着但调度循环挂了，自动轮询其实不会跑 */
  scheduler?: PollScheduler | null;
  /** SSE 刚推来「开始轮询」时先亮起来，不用等服务端状态刷新 */
  live?: boolean;
  /** 暂停 / 恢复自动轮询；不传则不显示开关 */
  onToggle?: () => void;
  busy?: boolean;
}

/**
 * 轮询状态指示条（对话头部）。
 * 一盏灯 + 一句话 + 倒计时：让「这个会话到底有没有在轮询」一眼可见，
 * 灯在呼吸 = 此刻真的在跑；灯常亮 = 开着但空闲，正在等下一轮。
 */
export default function PollPill({ repo, scheduler, live, onToggle, busy }: Props) {
  const now = useNow(1000);
  const shown: Repo | null = repo && live ? { ...repo, polling: true } : repo;
  const view = pollViewOf(shown, now);
  const schedDown = !!repo && !!scheduler && !scheduler.running;

  return (
    <span
      className={'poll-pill is-' + (schedDown ? 'warn' : view.dot)}
      title={
        schedDown
          ? '后台轮询调度没有运行：只有「立即轮询」会生效，自动轮询不会发生。重启 start.bat 即可恢复。'
          : [view.label, view.detail, repo?.lastError ? '最近错误：' + repo.lastError : '']
              .filter(Boolean).join('\n')
      }
    >
      <i className="poll-dot" aria-hidden="true" />
      <span className="poll-label">{schedDown ? '调度未启动' : view.label}</span>
      {!schedDown && view.detail && <span className="poll-detail">{view.detail}</span>}
      {schedDown && <span className="poll-detail">重启 start.bat</span>}
      {repo && onToggle && (
        <button
          type="button"
          className={'poll-toggle' + (repo.enabled ? ' is-on' : '')}
          disabled={!!busy}
          onClick={onToggle}
          title={repo.enabled ? '暂停自动轮询（已排队的任务不会中断）' : '恢复自动轮询'}
        >
          {repo.enabled ? '暂停' : '开启'}
        </button>
      )}
    </span>
  );
}
