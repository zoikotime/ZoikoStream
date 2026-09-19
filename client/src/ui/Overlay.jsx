import { useEffect } from "react";
import { createPortal } from "react-dom";
import { cx, z } from "./tokens";
import { register } from "./dismissStack";

// Shared overlay behaviour for Modal + Drawer: portal to <body>, backdrop click to
// close, Esc to close, and body scroll-lock while open. Panel positioning is left
// to the caller (children). Mounts only while open (enter-animated, no exit anim).

// Escape closes ONLY the top of the stack: a dialog opened from inside a drawer (request
// content access, confirm an action) must not take the drawer down with it and lose the
// operator's context.
//
// The stack moved to ui/dismissStack so that Android's Back can consult the same one — a
// second, private list here would mean Escape and Back disagreeing about which layer is on
// top, which is worse than either being wrong on its own.
//
// It also fixes a leak this file had: the entry was pushed on open and never removed on
// close, so the array grew for the life of the tab and, more visibly, closing a nested dialog
// left ITS dead entry on top — after which Escape stopped closing the drawer underneath,
// because the top of the stack belonged to something no longer on screen.

export default function Overlay({ open, onClose, className = "", children }) {
  useEffect(() => {
    if (!open) return;
    const layer = register(() => onClose?.());
    const onKey = (e) => e.key === "Escape" && layer.isTop() && onClose?.();
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      layer.release();
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
