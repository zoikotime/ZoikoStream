import { useLayoutEffect, useRef } from "react";
import { FiX } from "react-icons/fi";
import { cx } from "./tokens";
import Overlay from "./Overlay";

const SIZES = { sm: "max-w-sm", md: "max-w-md", lg: "max-w-lg", xl: "max-w-2xl" };

const TABBABLE = [
  "a[href]", "button:not([disabled])", "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])", "textarea:not([disabled])", "[tabindex]:not([tabindex='-1'])",
  "[contenteditable='true']",
].join(",");

// What Tab can land on inside the dialog, in order. Layout-hidden elements are skipped where the
// browser can say so (checkVisibility); jsdom cannot, and has nothing hidden that way.
function tabbables(root) {
  return [...root.querySelectorAll(TABBABLE)].filter(
    (el) => el.tabIndex >= 0 && !el.closest("[hidden],[inert]")
      && (typeof el.checkVisibility !== "function" || el.checkVisibility())
  );
}

// Keyboard focus for a modal dialog (WAI-ARIA APG dialog pattern):
//   * on open, focus moves INTO the dialog — onto the dialog itself, so its name is announced
//     and the next Tab reaches its first control — unless a child already placed focus inside
//     (a dialog that focuses its password field keeps doing so: child effects run first);
//   * while open, Tab and Shift+Tab cycle within the dialog instead of reaching the page behind
//     it. A native listener on the dialog node, so menus portalled OUT of it (ui/Dropdown's
//     field menu, the timezone picker) keep their own keyboard handling;
//   * on close, focus returns to whatever opened the dialog — unless something else has
//     deliberately taken it in the meantime (a page that navigates, or focuses a new row).
// Escape is not handled here: ui/Overlay closes the TOP dismissable layer (ui/dismissStack).
function useDialogFocus(open, dialogRef) {
  useLayoutEffect(() => {
    if (!open) return undefined;
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    // Layout effect: runs before any child's useEffect, so this is still the opener.
    const opener = document.activeElement;
    if (!dialog.contains(document.activeElement)) dialog.focus({ preventScroll: true });

    const onKeyDown = (e) => {
      if (e.key !== "Tab" || e.defaultPrevented) return;
      const items = tabbables(dialog);
      if (items.length === 0) {
        e.preventDefault();
        dialog.focus();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || active === dialog)) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && active === last) {
        e.preventDefault();
        first.focus();
      }
    };
    dialog.addEventListener("keydown", onKeyDown);

    return () => {
      dialog.removeEventListener("keydown", onKeyDown);
      const active = document.activeElement;
      const focusLeftWithDialog = !active || active === document.body || !active.isConnected || dialog.contains(active);
      if (focusLeftWithDialog && opener instanceof HTMLElement && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open, dialogRef]);
}

// Centered dialog. `title` renders a header with a close button.
//
// The panel is HEIGHT-CAPPED and its body scrolls. Without the cap a dialog taller than the
// viewport (the host console's Broadcast settings, the admin console's larger forms) is
// centred by the overlay's `place-items-center` and so overflows equally off the TOP and
// bottom — taking the header, and with it the only close button, off-screen — while the
// panel's own `overflow-hidden` clips the rest and `Overlay`'s body scroll-lock means the
// page can't scroll to reach it. Capping at the viewport minus the overlay's p-4 gutter and
// giving the body `flex-1 overflow-y-auto` keeps the header and footer pinned and makes the
// content scrollable instead.
export default function Modal({ open, onClose, title, size = "md", className = "", closeOnBackdrop = false, children, footer }) {
  const dialogRef = useRef(null);
  useDialogFocus(open, dialogRef);
  return (
    <Overlay open={open} onClose={onClose} closeOnBackdrop={closeOnBackdrop} className="grid place-items-center p-4">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        // -1: focusable by script (on open) but not a Tab stop; no outline around the panel.
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
        className={cx(
          "flex max-h-[calc(100vh-2rem)] w-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl outline-none motion-safe:animate-[zk-fade-in_.25s] dark:border-slate-800 dark:bg-slate-900",
          SIZES[size],
          className
        )}
      >
        {title && (
          <div className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
            <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
            >
              <FiX />
            </button>
          </div>
        )}
        {/* min-h-0 is what actually lets this shrink below its content height — a flex item
            defaults to min-height:auto, which would keep the panel growing past the cap. */}
        <div className="zk-scroll-thin min-h-0 flex-1 overflow-y-auto px-5 py-4 text-sm text-slate-600 dark:text-slate-300">{children}</div>
        {footer && <div className="flex shrink-0 justify-end gap-2 border-t border-slate-100 px-5 py-4 dark:border-slate-800">{footer}</div>}
      </div>
    </Overlay>
  );
}
