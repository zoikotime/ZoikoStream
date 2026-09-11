import { cx } from "../../../ui/tokens";

// The dashboard's section surface.
//
// Deliberately NOT components/admin/Panel: that one is the operations-console surface, and
// it is mounted on the admin console and on every other organization page. Restyling it to
// get this page's softer proportions — a larger title, roomier padding, a rounder corner —
// would have changed all of them. A local surface keeps the change where it belongs.
//
// `bodyClass` lets a caller take over the padding entirely (the events list draws its rows
// edge to edge and pads them itself).
export default function DashboardPanel({ title, action, children, bodyClass, className = "" }) {
  return (
    <section
      className={cx(
        "flex flex-col rounded-2xl border border-slate-200 bg-white",
        "dark:border-white/[0.12] dark:bg-white/[0.04]",
        className
      )}
    >
      {(title || action) && (
        <header className="flex items-center justify-between gap-4 px-6 pb-4 pt-5">
          {title && (
            <h2 className="min-w-0 truncate text-[17px] font-semibold tracking-tight text-slate-900 dark:text-white">
              {title}
            </h2>
          )}
          {action && <div className="shrink-0">{action}</div>}
        </header>
      )}
      <div className={cx("flex-1", bodyClass ?? "px-6 pb-6")}>{children}</div>
    </section>
  );
}
