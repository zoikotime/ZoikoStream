import { useEffect, useRef } from "react";
import { FiAlertTriangle } from "react-icons/fi";
import Modal from "./Modal";
import { ConsoleButton as Button } from "./Button";
import { cx } from "./tokens";

// Destructive-action confirmation, built on the existing Modal (which already portals,
// locks scroll, closes on Esc and on backdrop click).
//
// Replaces window.confirm() for actions where the WORDING carries the risk — "Delete 12
// events?" has to name the count, and a native confirm cannot show the list or match the
// console's language. Kept for the same reason the native one was fine before: it is one
// component, not a dialog framework.
//
// Accessibility: the destructive button takes focus on open (so Enter confirms and Esc
// cancels with no pointer), the body is wired to the dialog via aria-describedby, and the
// icon is decorative.
export default function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title = "Are you sure?",
  body,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  tone = "danger", // "danger" | "primary"
  busy = false,
}) {
  const confirmRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    // rAF so focus lands after the panel's enter animation has mounted it.
    const id = requestAnimationFrame(() => confirmRef.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [open]);

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
            {cancelLabel}
          </Button>
          <Button
            ref={confirmRef}
            variant={tone}
            size="sm"
            loading={busy}
            disabled={busy}
            onClick={onConfirm}
          >
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex gap-3" id="zk-confirm-body">
        {tone === "danger" && (
          <span
            className={cx(
              "mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg",
              "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400"
            )}
          >
            <FiAlertTriangle aria-hidden="true" />
          </span>
        )}
        <div className="min-w-0 text-sm text-slate-600 dark:text-slate-300">{body}</div>
      </div>
    </Modal>
  );
}
