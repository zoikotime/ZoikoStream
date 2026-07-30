import { cx } from "../../ui/tokens";

// Centered empty state for non-table contexts (detail tabs, panels). DataTable has
// its own built-in empty block; this covers everywhere else so the look stays one.
export default function OrganizationEmptyState({
  icon: Icon,
  title,
  description,
  action,
  className = "",
}) {
  return (
    <div className={cx("flex flex-col items-center justify-center px-6 py-14 text-center", className)}>
      {Icon && (
        <div className="mb-3 grid h-11 w-11 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
          <Icon className="text-lg" />
        </div>
      )}
      <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title}</p>
      {description && (
        <p className="mt-1 max-w-sm text-sm text-slate-500 dark:text-slate-400">{description}</p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
