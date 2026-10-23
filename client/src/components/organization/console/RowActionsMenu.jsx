import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FiMoreHorizontal } from "react-icons/fi";
import { cx, focusRing } from "../../../ui/tokens";

// A row's contextual overflow ("⋯"): the secondary and destructive actions of one table row
// (ZST-UX-NAV-001 §16.1: "destructive actions in contextual overflow with confirmation"). The
// trigger is visible at rest; the menu is a real menu — role="menu", arrow keys, Home/End,
// Escape back to the trigger — and is portalled to <body>, because a table's horizontal
// scroller would otherwise clip it at the last rows.
//
// `items`: [{ key, label, icon, onSelect, danger }]. Confirmation is the caller's job (the
// destructive item's onSelect asks before it acts).
export default function RowActionsMenu({ label, items }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [pos, setPos] = useState(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const itemRefs = useRef([]);
  const menuId = useId();

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setActive(-1);
    if (refocus) triggerRef.current?.focus();
  }, []);

  // Anchored under the trigger's right edge, or above it near the bottom of the window.
  const openAt = (index) => {
    const r = triggerRef.current?.getBoundingClientRect();
    if (r) {
      const right = Math.max(8, window.innerWidth - r.right);
      setPos(r.bottom + 140 > window.innerHeight ? { bottom: window.innerHeight - r.top + 4, right } : { top: r.bottom + 4, right });
    }
    setOpen(true);
    setActive(index);
  };

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (!menuRef.current?.contains(e.target) && !triggerRef.current?.contains(e.target)) close(false);
    };
    // The menu is fixed to where the trigger was; any scroll or resize would detach it.
    const onMove = (e) => {
      if (!menuRef.current?.contains(e.target)) close(false);
    };
    document.addEventListener("mousedown", onDown);
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [open, close]);

  useEffect(() => {
    if (open && active >= 0) itemRefs.current[active]?.focus();
  }, [open, active]);

  const onTriggerKeyDown = (e) => {
    if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      openAt(0);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      openAt(items.length - 1);
    } else if (e.key === "Escape" && open) {
      e.preventDefault();
      close();
    }
  };

  const onMenuKeyDown = (e) => {
    const n = items.length;
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % n);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + n) % n);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(n - 1);
    } else if (e.key === "Tab") {
      // Back to the trigger before the browser acts on Tab, so focus moves on from the row.
      // The menu is portalled to <body>; left where it is, Tab continued from the end of the
      // page and landed on whatever floats there.
      close();
    }
  };

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        aria-label={label}
        title="More actions"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => (open ? close() : openAt(-1))}
        onKeyDown={onTriggerKeyDown}
        className={cx(
          "inline-grid h-8 w-8 place-items-center rounded-lg text-[16px] transition-colors",
          "text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:text-neutral-300 dark:hover:bg-white/10 dark:hover:text-white",
          open && "bg-slate-100 text-slate-800 dark:bg-white/10 dark:text-white",
          focusRing
        )}
      >
        <FiMoreHorizontal aria-hidden="true" />
      </button>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            id={menuId}
            role="menu"
            aria-label={label}
            onKeyDown={onMenuKeyDown}
            style={pos || undefined}
            className="zk-pop-in fixed z-50 min-w-[11rem] rounded-xl border border-slate-200 bg-white p-1 text-left shadow-xl dark:border-white/10 dark:bg-[#151a2e]"
          >
            {items.map((it, i) => {
              const Icon = it.icon;
              return (
                <button
                  key={it.key}
                  ref={(el) => { itemRefs.current[i] = el; }}
                  type="button"
                  role="menuitem"
                  tabIndex={active === i ? 0 : -1}
                  onClick={() => {
                    close();
                    it.onSelect();
                  }}
                  className={cx(
                    "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-[13px] font-medium outline-none",
                    it.danger
                      ? "text-rose-600 hover:bg-rose-50 focus:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-500/10 dark:focus:bg-rose-500/10"
                      : "text-slate-700 hover:bg-slate-50 focus:bg-slate-50 dark:text-neutral-200 dark:hover:bg-white/[0.06] dark:focus:bg-white/[0.06]"
                  )}
                >
                  {Icon && <Icon aria-hidden="true" className="shrink-0 text-[14px]" />}
                  {it.label}
                </button>
              );
            })}
          </div>,
          document.body
        )}
    </>
  );
}
