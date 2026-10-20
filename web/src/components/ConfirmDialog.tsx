import { useEffect, useRef } from "react";
import { Busy } from "./ui";
export default function ConfirmDialog({
  open,
  title,
  description,
  busy,
  onCancel,
  onConfirm,
}: {
  open: boolean;
  title: string;
  description: string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (open && !dialog?.open) dialog?.showModal();
    else if (!open && dialog?.open) dialog.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      className="modal small-modal confirm-dialog"
      aria-labelledby="confirm-title"
      onCancel={(e) => {
        if (busy) e.preventDefault();
        else onCancel();
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel();
      }}
    >
      <h2 id="confirm-title">{title}</h2>
      <p className="dim">{description}</p>
      <div className="form-footer">
        <button
          className="button secondary"
          type="button"
          disabled={busy}
          onClick={onCancel}
          autoFocus
        >
          保留任务
        </button>
        <button
          className="button primary"
          type="button"
          disabled={busy}
          onClick={onConfirm}
        >
          {busy ? <Busy /> : "确认删除"}
        </button>
      </div>
    </dialog>
  );
}
