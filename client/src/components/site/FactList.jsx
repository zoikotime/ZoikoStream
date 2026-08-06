import { cx } from "../../ui/tokens";

// Label/value rows for the public pages, carrying the same rule the console's StatRow does:
// a null value renders an em dash with its reason on hover, never a plausible-looking figure.
//
// This is the marketing-side counterpart of components/admin/StatRow — same convention,
// different surface palette (slate marketing tokens rather than the console's CONSOLE map),
// which is why it is a separate component rather than a variant.
export default function FactList({ items, className = "" }) {
  return (
    <dl className={cx("divide-y divide-slate-200 dark:divide-slate-800", className)}>
      {items.map(({ label, value, reason }) => (
        <div key={label} className="flex items-baseline justify-between gap-6 py-3">
          <dt className="min-w-0 text-sm text-slate-600 dark:text-slate-400">{label}</dt>
          <dd
            className={cx(
              "shrink-0 text-sm font-semibold",
              value == null ? "text-slate-400 dark:text-slate-500" : "text-slate-900 dark:text-white"
            )}
            title={value == null ? reason : undefined}
          >
            {value == null ? "—" : value}
          </dd>
        </div>
      ))}
    </dl>
  );
}
