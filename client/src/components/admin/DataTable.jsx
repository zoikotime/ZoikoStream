import { useMemo, useState } from "react";
import { FiChevronDown, FiChevronLeft, FiChevronRight, FiChevronUp, FiSearch } from "react-icons/fi";
import { cx, focusRing } from "../../ui/tokens";

// Reusable data grid for every table (Organizations, Users, Events, Billing, …).
// Features: client-side sortable columns with clear indicators, optional pagination
// (sorts THEN paginates so order is global), optional built-in search, optional bulk
// row selection, sticky header (pass maxHeight to engage), subtle row hover,
// right-aligned tabular numbers, row actions revealed on hover, skeleton loading,
// an explained empty state, and responsive horizontal scroll (minWidth).
//
// Every added capability is OPT-IN — omit its prop and the table renders exactly as
// before, so existing call sites are untouched.
//
// columns: [{
//   key, header, align?: 'left'|'right', sortable?, sortValue?(row),
//   render?(row), mono?, width?, className?, headerClassName?
// }]
// search:    searchable + (searchKeys?: string[] | getSearchText?(row)) + searchPlaceholder?
// selection: selectable + rowKey + onSelectionChange?(Set) + bulkActions?({selected, clear})
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
          : "text-slate-600 hover:bg-slate-100 disabled:pointer-events-none disabled:opacity-40 dark:text-neutral-300 dark:hover:bg-white/[0.06]"
      )}
    >
      {children}
    </button>
  );
}

// Shared checkbox for select-all / per-row. `indeterminate` set via callback ref.
function CheckBox({ checked, indeterminate = false, onChange, label }) {
  return (
    <input
      type="checkbox"
      checked={checked}
      aria-label={label}
      ref={(el) => el && (el.indeterminate = indeterminate)}
      onChange={onChange}
      onClick={(e) => e.stopPropagation()}
      className={cx(
        "h-4 w-4 shrink-0 cursor-pointer rounded border-slate-300 accent-violet-600 dark:border-slate-600",
        focusRing
      )}
    />
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
  // search (opt-in)
  searchable = false,
  searchKeys,
  getSearchText,
  searchPlaceholder = "Search…",
  // bulk selection (opt-in)
  selectable = false,
  onSelectionChange,
  bulkActions,
}) {
  const [sort, setSort] = useState(initialSort); // { key, dir } | null
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(() => new Set());

  // Built-in search: narrow rows before sort/paginate.
  const q = query.trim().toLowerCase();
  const filtered = useMemo(() => {
    if (!searchable || !q) return rows;
    return rows.filter((r) => {
      const text = getSearchText
        ? getSearchText(r)
        : (searchKeys && searchKeys.length ? searchKeys : columns.map((c) => c.key)).map((k) => r[k]).join(" ");
      return String(text).toLowerCase().includes(q);
    });
  }, [rows, q, searchable, getSearchText, searchKeys, columns]);

  // Reset to page 1 when the underlying set changes (filter narrows it, or search).
  // Render-phase reset — the documented alternative to a setState-in-effect.
  const [prevKey, setPrevKey] = useState({ rows, q });
  if (rows !== prevKey.rows || q !== prevKey.q) {
    setPrevKey({ rows, q });
    setPage(1);
  }

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return filtered;
    const val = col.sortValue || ((r) => r[col.key]);
    const dir = sort.dir === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = val(a);
      const bv = val(b);
      if (av == null) return 1;
      if (bv == null) return -1;
      if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
      return String(av).localeCompare(String(bv)) * dir;
    });
  }, [filtered, sort, columns]);

  const pageCount = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;
  const current = Math.min(page, pageCount);
  const paged = pageSize ? sorted.slice((current - 1) * pageSize, current * pageSize) : sorted;

  const toggleSort = (key) =>
    setSort((s) => (s?.key === key ? (s.dir === "asc" ? { key, dir: "desc" } : null) : { key, dir: "asc" }));

  // Selection acts on the full filtered/sorted set (not just the current page).
  const allKeys = selectable ? sorted.map(rowKey) : [];
  const allChecked = allKeys.length > 0 && allKeys.every((k) => selected.has(k));
  const someChecked = !allChecked && allKeys.some((k) => selected.has(k));
  const commitSelection = (next) => {
    setSelected(next);
    onSelectionChange?.(next);
  };
  const toggleRow = (k) => {
    const next = new Set(selected);
    next.has(k) ? next.delete(k) : next.add(k);
    commitSelection(next);
  };
  const toggleAll = () => commitSelection(allChecked ? new Set() : new Set(allKeys));
  const clearSelection = () => commitSelection(new Set());

  const colCount = columns.length + (rowActions ? 1 : 0) + (selectable ? 1 : 0);

  return (
    <div>
      {searchable && (
        <div className="px-4 pb-3 pt-1">
          <div className="relative max-w-xs">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder}
              aria-label="Search table"
              className={cx(
                "h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-700 outline-none placeholder:text-slate-400 dark:border-white/10 dark:bg-white/[0.03] dark:text-neutral-200",
                focusRing
              )}
            />
          </div>
        </div>
      )}

      {selectable && selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-3 border-b border-slate-100 px-4 py-2.5 dark:border-white/10">
          <span className="text-sm font-medium text-slate-600 dark:text-neutral-300">{selected.size} selected</span>
          {bulkActions?.({ selected: [...selected], clear: clearSelection })}
          <button
            type="button"
            onClick={clearSelection}
            className={cx("ml-auto rounded-md px-2 py-1 text-xs font-medium text-slate-500 hover:bg-slate-100 dark:text-neutral-400 dark:hover:bg-white/[0.06]", focusRing)}
          >
            Clear
          </button>
        </div>
      )}

      <div className={cx("overflow-auto", className)} style={maxHeight ? { maxHeight } : undefined}>
        <table className="w-full border-collapse" style={{ minWidth }}>
          <thead
            className={cx("bg-slate-50/90 backdrop-blur dark:bg-white/[0.04]", maxHeight && "sticky top-0 z-10")}
          >
            <tr className="border-b border-slate-200 dark:border-white/10">
              {selectable && (
                <th className="w-10 px-4 py-2.5">
                  <CheckBox checked={allChecked} indeterminate={someChecked} onChange={toggleAll} label="Select all rows" />
                </th>
              )}
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

          <tbody className="divide-y divide-slate-100 dark:divide-white/[0.07]">
            {loading &&
              Array.from({ length: skeletonRows }).map((_, i) => (
                <tr key={`sk-${i}`}>
                  {selectable && <td className="px-4 py-3" />}
                  {columns.map((c) => (
                    <td key={c.key} className="px-4 py-3">
                      <div
                        className={cx(
                          "zk-skeleton h-4 rounded bg-slate-200 dark:bg-white/[0.07]",
                          c.align === "right" ? "ml-auto w-12" : "w-24"
                        )}
                      />
                    </td>
                  ))}
                  {rowActions && <td className="px-4 py-3" />}
                </tr>
              ))}

            {!loading &&
              paged.map((row) => {
                const key = rowKey(row);
                const isSelected = selectable && selected.has(key);
                return (
                  <tr
                    key={key}
                    onClick={onRowClick ? () => onRowClick(row) : undefined}
                    className={cx(
                      "group transition-colors duration-150 hover:bg-slate-50 dark:hover:bg-white/[0.06]/40",
                      isSelected && "bg-violet-50/60 dark:bg-violet-500/10",
                      onRowClick && "cursor-pointer"
                    )}
                  >
                    {selectable && (
                      <td className="px-4 py-3">
                        <CheckBox checked={isSelected} onChange={() => toggleRow(key)} label="Select row" />
                      </td>
                    )}
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={cx(
                          "px-4 py-3 text-sm text-slate-600 dark:text-neutral-300",
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
                );
              })}

            {!loading && sorted.length === 0 && (
              <tr>
                <td colSpan={colCount} className="px-4 py-16 text-center">
                  {empty ? (
                    <div className="mx-auto max-w-sm">
                      {empty.icon && (
                        <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-white/[0.07]">
                          <empty.icon className="text-lg" />
                        </div>
                      )}
                      <p className="text-sm font-semibold text-slate-700 dark:text-neutral-200">{empty.title}</p>
                      {empty.description && (
                        <p className="mt-1 text-sm text-slate-500 dark:text-neutral-400">{empty.description}</p>
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
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 dark:border-white/10">
          <p className="text-xs text-slate-500 dark:text-neutral-400">
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
