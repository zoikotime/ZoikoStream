// client/src/components/moderation/Panel.jsx
// Shared panel shell + compact action button for the Moderator Dashboard, so
// every moderation surface (participants, chat, polls, feed) looks identical.
import { cx } from "../../ui/tokens";

const TONE = {
  emerald: "text-emerald-600 hover:bg-emerald-50 dark:text-emerald-400 dark:hover:bg-emerald-500/10",
  blue: "text-blue-600 hover:bg-blue-50 dark:text-blue-400 dark:hover:bg-blue-500/10",
  amber: "text-amber-600 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-500/10",
  rose: "text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-500/10",
  slate: "text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800",
};

// Icon (+ optional lg-only label) button used for every moderation action.
export function ActionButton({ icon: Icon, label, title, tone = "slate", active = false, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title || label}
      aria-label={title || label}
      className={cx(
        "inline-flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-medium transition",
        active ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400" : TONE[tone]
      )}
    >
      <Icon className="text-sm" />
      {label && <span className="hidden lg:inline">{label}</span>}
    </button>
  );
}

// `scroll` (default) makes the body a flex-1 internal scroll area — right for a
// column that fills its height. Pass scroll={false} for cards inside a scrolling column.
// `toolbar` is an optional row pinned between the header and the scroll area — search /
// filter controls belong there, not inside the body, where they'd scroll out of reach.
export default function Panel({ title, count, badge, action, toolbar, scroll = true, className = "", children }) {
  return (
    <section className={cx("flex min-h-0 flex-col overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900", className)}>
      <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
        <div className="flex min-w-0 items-center gap-2">
          <h2 className="truncate font-semibold text-slate-900 dark:text-white">{title}</h2>
          {count != null && (
            <span className="shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
              {count}
            </span>
          )}
          {badge}
        </div>
        {action}
      </div>
      {toolbar && (
        <div className="shrink-0 space-y-2 border-b border-slate-100 px-3 py-2 dark:border-slate-800">{toolbar}</div>
      )}
      <div className={cx("p-3", scroll && "flex-1 overflow-y-auto")}>{children}</div>
    </section>
  );
}
