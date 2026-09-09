import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { FiChevronDown, FiZap } from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import { QUICK_ACTIONS } from "./quickActions";

// The topbar's compact Quick Actions menu.
//
// A menu of navigation shortcuts, so it is built on the account-menu idiom in Topbar.jsx
// (role="menu" + role="menuitem", `zk-pop-in`, the same panel treatment) rather than on
// ui/Dropdown — that one is a controlled listbox with a selected value, and there is nothing
// here to select.
//
// Keyboard contract matches the console's other menus, because a header control that can
// only be reached with a mouse is not finished:
//   Enter/Space/ArrowDown/ArrowUp  open (ArrowUp opens on the last item)
//   ArrowUp/ArrowDown              move the active item
//   Home/End                       first/last
//   Enter/Space                    follow the active item
//   Escape / click outside         close, returning focus to the trigger
//
// Destinations come from ./quickActions so this and the Profile page cannot disagree about
// where "Billing" goes.
export default function QuickActionsMenu({ className = "" }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const itemRefs = useRef([]);

  const close = useCallback((refocus = true) => {
    setOpen(false);
    setActive(-1);
    if (refocus) triggerRef.current?.focus();
  }, []);

  // Click-outside and Escape, the same pair every other popover in the console honours.
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) close(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open, close]);

  // Move real DOM focus with the active index, so a screen reader announces each item and
  // the visible highlight can never disagree with what Enter will follow.
  useEffect(() => {
    if (open && active >= 0) itemRefs.current[active]?.focus();
  }, [open, active]);

  const go = (to) => {
    close(false);
    navigate(to);
  };

  const onTriggerKeyDown = (e) => {
    // Escape has to be handled HERE as well as on the menu: opening by click leaves focus on
    // the trigger with no active item, so the keypress never reaches the panel.
    if (e.key === "Escape" && open) {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      setOpen(true);
      setActive(0);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setOpen(true);
      setActive(QUICK_ACTIONS.length - 1);
    }
  };

  const onMenuKeyDown = (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % QUICK_ACTIONS.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + QUICK_ACTIONS.length) % QUICK_ACTIONS.length);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(QUICK_ACTIONS.length - 1);
    } else if (e.key === "Tab") {
      // Tabbing out of a menu closes it rather than leaving an orphaned popover open.
      close(false);
    }
  };

  return (
    <div ref={rootRef} className={cx("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? close() : (setOpen(true), setActive(-1)))}
        onKeyDown={onTriggerKeyDown}
        aria-haspopup="menu"
        aria-expanded={open}
        className={cx(
          // Geometry matches the topbar's other pill controls (h-9 fields, rounded-lg), so
          // the row's alignment and rhythm are unchanged.
          "inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-[13px] font-medium",
          "transition-colors duration-150 motion-reduce:transition-none",
          "border-slate-200 bg-white hover:bg-slate-50",
          "dark:border-white/10 dark:bg-white/[0.04] dark:hover:bg-white/[0.07]",
          open && "bg-slate-50 dark:bg-white/[0.07]",
          CONSOLE.body,
          focusRing
        )}
        title="Quick actions for this organization"
      >
        <FiZap className="shrink-0 text-[14px] text-violet-500 dark:text-violet-400" aria-hidden="true" />
        {/* The label is the control's identity, so it survives to the smallest breakpoint
            the topbar supports; only the chevron is optional. */}
        <span>Quick Actions</span>
        <FiChevronDown
          className={cx(
            "hidden shrink-0 text-[13px] transition-transform duration-150 sm:block",
            "motion-reduce:transition-none",
            CONSOLE.faint,
            open && "rotate-180"
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Quick actions"
          onKeyDown={onMenuKeyDown}
          className={cx(
            // `right-0` keeps the panel inside the viewport on narrow screens, where the
            // trigger sits close to the right edge.
            "zk-pop-in absolute right-0 top-11 z-30 w-72 overflow-hidden rounded-xl border p-1 shadow-xl",
            "border-slate-200 bg-white shadow-slate-900/10",
            "dark:border-white/10 dark:bg-neutral-950 dark:shadow-black/60"
          )}
        >
          <p className={cx("px-3 pb-1.5 pt-2 text-[11px] font-semibold uppercase tracking-wide", CONSOLE.faint)}>
            Quick actions
          </p>
          {QUICK_ACTIONS.map((action, index) => (
            <button
              key={action.title}
              ref={(node) => { itemRefs.current[index] = node; }}
              role="menuitem"
              type="button"
              // -1 keeps the menu a single tab stop; arrow keys move within it, which is the
              // contract a menu is expected to have.
              tabIndex={active === index ? 0 : -1}
              onClick={() => go(action.to)}
              onMouseEnter={() => setActive(index)}
              className={cx(
                "flex w-full items-start gap-2.5 rounded-lg px-3 py-2 text-left",
                "transition-colors duration-150 motion-reduce:transition-none",
                "hover:bg-slate-100 dark:hover:bg-white/[0.07]",
                active === index && "bg-slate-100 dark:bg-white/[0.07]",
                focusRing
              )}
            >
              <action.icon
                className={cx("mt-0.5 h-4 w-4 shrink-0", CONSOLE.faint)}
                aria-hidden="true"
              />
              <span className="min-w-0">
                <span className={cx("block truncate text-[13px] font-medium", CONSOLE.heading)}>
                  {action.title}
                </span>
                <span className={cx("mt-0.5 block text-[11px] leading-4", CONSOLE.faint)}>
                  {action.desc}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
