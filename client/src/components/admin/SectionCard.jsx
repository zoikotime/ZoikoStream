import { cx, ACCENT } from "../../ui/tokens";
import Card from "../../ui/Card";

// The DRY backbone for every dashboard panel: a Card with a consistent header
// (icon chip + title + subtitle) and an optional right-aligned `action` node.
// `bodyClass` styles the content region; pass `padding="none"` for flush tables.
export default function SectionCard({
  title,
  subtitle,
  icon: Icon,
  accent = "violet",
  action,
  className = "",
  bodyClass = "",
  padding = "md",
  children,
}) {
  return (
    <Card padding="none" className={cx("flex flex-col overflow-hidden", className)}>
      {(title || action) && (
        <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4 dark:border-slate-800">
          <div className="flex min-w-0 items-center gap-2.5">
            {Icon && (
              <span className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg", (ACCENT[accent] || ACCENT.violet).chip)}>
                <Icon className="text-base" />
              </span>
            )}
            <div className="min-w-0">
              <h2 className="truncate font-semibold text-slate-900 dark:text-white">{title}</h2>
              {subtitle && <p className="truncate text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
            </div>
          </div>
          {action && <div className="shrink-0">{action}</div>}
        </div>
      )}
      <div className={cx(padding === "none" ? "" : "p-5", "flex-1", bodyClass)}>{children}</div>
    </Card>
  );
}
