import { cx, STATUS } from "./tokens";

// Status pill. `status` keys into the shared STATUS map (active/trial/error/…).
// `dot` adds a leading indicator dot; `live` makes it pulse.
export default function Badge({ status = "neutral", dot = false, live = false, className = "", children }) {
  const tone = STATUS[status] || STATUS.neutral;
  return (
    <span className={cx("inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-semibold", tone, className)}>
      {(dot || live) && (
        <span className={cx("h-1.5 w-1.5 rounded-full bg-current", live && "animate-pulse")} />
      )}
      {children}
    </span>
  );
}
