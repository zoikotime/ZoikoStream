import { cx } from "../../ui/tokens";

// The section container for the Operations Center. Flat and bordered (no shadow, no
// hover-lift) — the "engineering console" surface, distinct from the org dashboard's
// shadowed Card. Header is quiet: a small-caps eyebrow over a compact title.
//
//   <Panel eyebrow="Billing" title="Revenue & Growth" action={<Link/>}>…</Panel>
//   <Panel title="Services" flush> <table/> </Panel>   // flush = table draws edge-to-edge
export default function Panel({
  eyebrow,
  title,
  description,
  action,
  flush = false,
  className = "",
  bodyClass = "",
  children,
}) {
  const hasHeader = eyebrow || title || action;
  return (
    <section
      className={cx(
        "rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900/50",
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
              <h2 className="mt-0.5 text-[15px] font-semibold tracking-tight text-slate-900 dark:text-white">
                {title}
              </h2>
            )}
            {description && (
              <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">{description}</p>
            )}
          </div>
          {action && <div className="shrink-0 text-sm">{action}</div>}
        </header>
      )}
      {hasHeader && <div className="border-t border-slate-100 dark:border-slate-800/70" />}
      <div className={cx(flush ? "" : "px-5 py-4", bodyClass)}>{children}</div>
    </section>
  );
}
