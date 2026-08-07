import { CONSOLE, cx } from "../../ui/tokens";

// The section container for the Operations Center. Flat and bordered (no shadow, no
// hover-lift) — the "engineering console" surface, distinct from the org dashboard's
// shadowed Card. Header is quiet: a small-caps eyebrow over a compact title.
//
//   <Panel eyebrow="Billing" title="Revenue & Growth" action={<Link/>}>…</Panel>
//   <Panel title="Services" flush> <table/> </Panel>   // flush = table draws edge-to-edge
// `static` opts out of the hover response — use it for a panel that is purely decorative.
export default function Panel({
  eyebrow,
  title,
  description,
  action,
  count,
  flush = false,
  static: isStatic = false,
  className = "",
  bodyClass = "",
  children,
}) {
  const hasHeader = eyebrow || title || action;
  return (
    // A border that answers the pointer is what makes a wall of sections read as a live surface
    // rather than a printed report. It is a border-only change — never a fill or a lift — so a
    // panel still never looks clickable, which most of them are not.
    <section
      className={cx(
        CONSOLE.panel,
        !isStatic && cx(CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none"),
        className
      )}
    >
      {hasHeader && (
        <header className="flex items-start justify-between gap-4 px-5 py-4">
          <div className="min-w-0">
            {eyebrow && (
              <p className="text-[11px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">
                {eyebrow}
              </p>
            )}
            {title && (
              <h2 className="mt-0.5 flex items-center gap-2 text-[15px] font-semibold tracking-tight text-slate-900 dark:text-white">
                <span className="min-w-0">{title}</span>
                {/* `count` renders only when there is something to count, so a quiet panel
                    doesn't carry a "0" badge. */}
                {count > 0 && (
                  <span className="grid h-[18px] min-w-[18px] shrink-0 place-items-center rounded-full bg-rose-500/15 px-1 text-[10px] font-bold text-rose-600 dark:bg-rose-500/20 dark:text-rose-400">
                    {count}
                  </span>
                )}
              </h2>
            )}
            {description && (
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{description}</p>
            )}
          </div>
          {action && <div className="shrink-0 text-sm">{action}</div>}
        </header>
      )}
      {hasHeader && <div className={cx("border-t", CONSOLE.divider)} />}
      <div className={cx(flush ? "" : "px-5 py-4", bodyClass)}>{children}</div>
    </section>
  );
}
