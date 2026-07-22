import { useMemo, useState } from "react";
import { FiChevronDown, FiChevronLeft, FiChevronRight, FiChevronUp } from "react-icons/fi";
import { cx, focusRing } from "./tokens";

// Reusable data grid for every admin table (Organizations, Users, Subscriptions, …).
// Features: client-side sortable columns with clear indicators, optional pagination
// (sorts THEN paginates so order is global), sticky header (pass maxHeight to engage),
// subtle row hover, right-aligned tabular numbers, row actions revealed on hover,
// skeleton loading, and an explained empty state.
//
// columns: [{
//   key, header, align?: 'left'|'right', sortable?, sortValue?(row),
//   render?(row), mono?, width?, className?, headerClassName?
// }]
const alignCls = (a) => (a === "right" ? "text-right" : "text-left");

function SortIcon({ active, dir }) {
  if (!active) return <FiChevronDown className="text-slate-300 dark:text-slate-600" aria-hidden="true" />;
  return dir === "asc" ? <FiChevronUp aria-hidden="true" /> : <FiChevronDown aria-hidden="true" />;
}

function PageBtn({ disabled, active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cx(
        "grid h-8 min-w-8 place-items-center rounded-md px-2 text-sm font-medium transition-colors duration-150",
        focusRing,
        active
          ? "bg-violet-600 text-white"
          : "text-slate-600 hover:bg-slate-100 disabled:pointer-events-none disabled:opacity-40 dark:text-slate-300 dark:hover:bg-slate-800"
      )}
    >
      {children}
    </button>
  );
}

export default function DataTable({
  columns,
  rows,
  rowKey,
  loading = false,
  skeletonRows = 6,
  empty,
  rowActions,
  onRowClick,
  initialSort = null,
  pageSize,
  minWidth = 640,
  maxHeight,
  className = "",
}) {
  const [sort, setSort] = useState(initialSort); // { key, dir } | null
  const [page, setPage] = useState(1);

  // Reset to page 1 when the underlying set changes (e.g. a filter narrows it).
  // Render-phase reset — the documented alternative to a setState-in-effect.
  const [prevRows, setPrevRows] = useState(rows);
  if (rows !== prevRows) {
    setPrevRows(rows);
    setPage(1);
  }

  const sorted = useMemo(() => {
    if (!sort) return rows;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return rows;
    const val = col.sortValue || ((r) => r[col.key]);
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...rows].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [rows, sort, columns]);

  const pageCount = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const current = Math.min(page, pageCount);
  const paged = pageSize ? sorted.slice((current - 1) * pageSize, current * pageSize) : sorted;

  const toggleSort = (key) =>
    setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));

  const colCount = columns.length + (rowActions ? 1 : 0);

  return (
    <div>
      <div className={cx("overflow-auto", className)} style={maxHeight ? { maxHeight } : undefined}>
        <table className="w-full border-collapse" style={{ minWidth }}>
          <thead
            className={cx("bg-slate-50/90 backdrop-blur dark:bg-slate-900/80", maxHeight && "sticky top-0 z-10")}
          >
            <tr className="border-b border-slate-200 dark:border-slate-800">
              {columns.map((c) => (
                <th
                  key={c.key}
                  style={c.width ? { width: c.width } : undefined}
                  className={cx(
                    "px-4 py-2.5 text-[11px] font-semibold uppercase tracking-wider text-slate-400",
                    alignCls(c.align),
                    c.headerClassName
                  )}
                  aria-sort={sort?.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                >
                  {c.sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(c.key)}
                      className={cx(
                        "inline-flex items-center gap-1 rounded transition-colors duration-150 hover:text-slate-600 dark:hover:text-slate-200",
                        focusRing,
                        c.align === "right" && "flex-row-reverse"
                      )}
                    >
                      {c.header}
                      <SortIcon active={sort?.key === c.key} dir={sort?.dir} />
                    </button>
                  ) : (
                    c.header
                  )}
                </th>
              ))}
              {rowActions && <th className="w-12 px-4 py-2.5" />}
            </tr>
          </thead>

          <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading &&
              Array.from({ length: skeletonRows }).map((_, i) => (
                <tr key={`sk-${i}`}>
                  {columns.map((c) => (
                    <td key={c.key} className="px-4 py-3">
                      <div
                        className={cx(
                          "zk-skeleton h-4 rounded bg-slate-200 dark:bg-slate-800",
                          c.align === "right" ? "ml-auto w-12" : "w-24"
                        )}
                      />
                    </td>
                  ))}
                  {rowActions && <td className="px-4 py-3" />}
                </tr>
              ))}

            {!loading &&
              paged.map((row) => (
                <tr
                  key={rowKey(row)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cx(
                    "group transition-colors duration-150 hover:bg-slate-50 dark:hover:bg-slate-800/40",
                    onRowClick && "cursor-pointer"
                  )}
                >
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={cx(
                        "px-4 py-3 text-sm text-slate-600 dark:text-slate-300",
                        alignCls(c.align),
                        c.mono ? "font-mono tabular-nums" : c.align === "right" && "tabular-nums",
                        c.className
                      )}
                    >
                      {c.render ? c.render(row) : row[c.key]}
                    </td>
                  ))}
                  {rowActions && (
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-1 opacity-0 transition-opacity duration-150 focus-within:opacity-100 group-hover:opacity-100 motion-reduce:transition-none">
                        {rowActions(row)}
                      </div>
                    </td>
                  )}
                </tr>
              ))}

            {!loading && sorted.length === 0 && (
              <tr>
                <td colSpan={colCount} className="px-4 py-16 text-center">
                  {empty ? (
                    <div className="mx-auto max-w-sm">
                      {empty.icon && (
                        <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
                          <empty.icon className="text-lg" />
                        </div>
                      )}
                      <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{empty.title}</p>
                      {empty.description && (
                        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{empty.description}</p>
                      )}
                      {empty.action && <div className="mt-4 flex justify-center">{empty.action}</div>}
                    </div>
                  ) : (
                    <p className="text-sm text-slate-400">No results.</p>
                  )}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {pageSize && !loading && sorted.length > 0 && (
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 dark:border-slate-800">
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Showing {(current - 1) * pageSize + 1}–{Math.min(current * pageSize, sorted.length)} of {sorted.length}
          </p>
          <div className="flex items-center gap-1">
            <PageBtn disabled={current === 1} onClick={() => setPage(current - 1)}>
              <FiChevronLeft />
            </PageBtn>
            {Array.from({ length: pageCount }, (_, i) => i + 1).map((p) => (
              <PageBtn key={p} active={p === current} onClick={() => setPage(p)}>
                {p}
              </PageBtn>
            ))}
            <PageBtn disabled={current === pageCount} onClick={() => setPage(current + 1)}>
              <FiChevronRight />
            </PageBtn>
          </div>
        </div>
      )}
    </div>
  );
}
