import { FiX } from "react-icons/fi";
import { cx } from "./tokens";
import Overlay from "./Overlay";

const SIDE = {
  right: { pos: "right-0 top-0 h-full", anim: "motion-safe:animate-[zk-slide-left_.3s]", size: "w-80 max-w-[85vw]" },
  left: { pos: "left-0 top-0 h-full", anim: "motion-safe:animate-[zk-slide-right_.3s]", size: "w-80 max-w-[85vw]" },
  bottom: { pos: "bottom-0 inset-x-0", anim: "motion-safe:animate-[zk-slide-up_.3s]", size: "max-h-[85vh]" },
};

// Sliding panel from a screen edge. `side`: right (default) | left | bottom.
export default function Drawer({ open, onClose, side = "right", title, className = "", children }) {
  const s = SIDE[side];
  return (
    <Overlay open={open} onClose={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        onClick={(e) => e.stopPropagation()}
        className={cx(
          "absolute flex flex-col border-slate-200 bg-white shadow-2xl dark:border-slate-800 dark:bg-slate-900",
          side === "bottom" ? "rounded-t-2xl border-t" : side === "right" ? "border-l" : "border-r",
          s.pos, s.size, s.anim, className
        )}
      >
        <div className="flex items-center justify-between border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <h2 className="font-semibold text-slate-900 dark:text-white">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close"
            className="grid h-8 w-8 place-items-center rounded-lg text-slate-400 transition hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-slate-800 dark:hover:text-slate-200"
          >
            <FiX />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-5 py-4">{children}</div>
      </div>
    </Overlay>
  );
}
