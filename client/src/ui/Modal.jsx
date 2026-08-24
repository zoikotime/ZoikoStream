import { FiX } from "react-icons/fi";
import { cx } from "./tokens";
import Overlay from "./Overlay";

const SIZES = { sm: "max-w-sm", md: "max-w-md", lg: "max-w-lg", xl: "max-w-2xl" };

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
export default function Modal({ open, onClose, title, size = "md", className = "", children, footer }) {
  return (
    <Overlay open={open} onClose={onClose} className="grid place-items-center p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        onClick={(e) => e.stopPropagation()}
        className={cx(
          "flex max-h-[calc(100vh-2rem)] w-full flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl motion-safe:animate-[zk-fade-in_.25s] dark:border-slate-800 dark:bg-slate-900",
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
