import { Fragment } from "react";
import { Link } from "react-router-dom";
import { FiChevronRight } from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";

// Page-level header for every Organization Admin screen (ZST-ENG-DASH-001 ORG-01..08):
//
//   Organization › Section › Page            (optional breadcrumb)
//   ORGANIZATION CONSOLE / OVERVIEW          (optional eyebrow)
//   Title                                    actions →
//   subtitle
//
//   <OrganizationPageHeader eyebrow="Organization console / Overview" title="Organization overview"
//     breadcrumbs={[["Organization", "/organization/manage"], ["Overview"]]} actions={…} />
//
// `sticky` pins the header (with the actions) to the top on scroll. Off by default.
export default function OrganizationPageHeader({
  title,
  subtitle,
  actions,
  eyebrow,
  breadcrumbs,
  sticky = false,
  className = "",
}) {
  return (
    <div
      className={cx(
        "space-y-2",
        sticky &&
          "sticky top-0 z-20 -mx-4 border-b border-slate-200 bg-white/80 px-4 py-3 backdrop-blur sm:-mx-6 sm:px-6 dark:border-slate-800 dark:bg-slate-950/70",
        className
      )}
    >
      {breadcrumbs?.length > 0 && (
        <nav aria-label="Breadcrumb">
          <ol className="flex flex-wrap items-center gap-1.5 text-[12.5px] text-slate-500 dark:text-neutral-400">
            {breadcrumbs.map(([label, to], i) => (
              <Fragment key={label}>
                {i > 0 && <li aria-hidden="true"><FiChevronRight /></li>}
                <li aria-current={i === breadcrumbs.length - 1 ? "page" : undefined}
                  className={i === breadcrumbs.length - 1 ? "font-medium text-violet-600 dark:text-violet-300" : undefined}>
                  {to ? (
                    <Link to={to} className={cx("rounded hover:text-slate-700 hover:underline dark:hover:text-neutral-200", focusRing)}>{label}</Link>
                  ) : label}
                </li>
              </Fragment>
            ))}
          </ol>
        </nav>
      )}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          {eyebrow && (
            <p className="mb-1 text-[10.5px] font-bold uppercase tracking-[0.14em] text-[#6f5fb0] dark:text-violet-300">{eyebrow}</p>
          )}
          {/* 30px bold, the frames' page-title size, on every organization screen. */}
          {title && (
            <h1 className="truncate text-[26px] font-bold leading-tight tracking-tight text-slate-900 sm:text-[30px] dark:text-white">
              {title}
            </h1>
          )}
          {subtitle && (
            <p className="mt-1 text-[13.5px] text-slate-500 dark:text-neutral-400">{subtitle}</p>
          )}
        </div>
        {actions && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>
        )}
      </div>
    </div>
  );
}
