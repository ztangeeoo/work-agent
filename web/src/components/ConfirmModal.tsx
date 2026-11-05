import { useEffect, useRef, type ReactNode } from 'react';

interface Props {
  open: boolean;
  title: string;
  /** 说明文案：写清「会发生什么 / 能不能撤回」 */
  children?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  /** 不可撤销的操作：确认按钮走危险色，焦点默认落在「取消」 */
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 通用二次确认弹框。
 *
 * 只做「问一句」这件事：真正的动作由调用方在 onConfirm 里执行，
 * 所以这里不关心业务，也不自己关自己 —— 关不关、关哪个由调用方决定。
 */
export default function ConfirmModal({
  open, title, children, confirmText = '确认', cancelText = '取消', danger, onConfirm, onCancel,
}: Props) {
  const cancelRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    /* 危险操作默认焦点给「取消」：手快回车不会把内容删掉 */
    const t = window.setTimeout(() => cancelRef.current?.focus(), 0);
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { window.clearTimeout(t); document.removeEventListener('keydown', onKey); };
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      id="confirmModal"
      className="modal-mask show"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div className="modal modal-narrow" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          <span className="modal-title">{title}</span>
          <button className="modal-close" onClick={onCancel} title="关闭">✕</button>
        </div>
        <div className="modal-body">
          <div className="confirm-text">{children}</div>
        </div>
        <div className="modal-foot">
          <span className="spacer" />
          <button className="btn btn-ghost btn-sm" ref={cancelRef} onClick={onCancel}>{cancelText}</button>
          <button
            className={'btn btn-sm ' + (danger ? 'btn-danger' : 'btn-brand')}
            onClick={onConfirm}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
