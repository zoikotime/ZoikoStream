import { useEffect } from "react";
import { createPortal } from "react-dom";
import { cx, z } from "./tokens";

// Shared overlay behaviour for Modal + Drawer: portal to <body>, backdrop click to
// close, Esc to close, and body scroll-lock while open. Panel positioning is left
// to the caller (children). Mounts only while open (enter-animated, no exit anim).
export default function Overlay({ open, onClose, className = "", children }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => e.key === "Escape" && onClose?.();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <div className={cx("fixed inset-0", z.overlay)}>
      <div
        className="absolute inset-0 bg-slate-950/60 backdrop-blur-sm motion-safe:animate-[zk-fade-in_.2s]"
        onClick={onClose}
      />
      <div className={cx("relative h-full w-full", className)}>{children}</div>
    </div>,
    document.body
  );
}
