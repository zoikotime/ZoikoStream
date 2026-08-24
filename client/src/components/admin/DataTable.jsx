import { useEffect, useMemo, useRef, useState } from "react";
import { FiChevronDown, FiChevronLeft, FiChevronRight, FiChevronUp, FiSearch } from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";

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

// At rest the chevron is a faint hint; on hover of its header it darkens, so a sortable column
// announces itself before it is clicked. An active sort is full-contrast and directional.
function SortIcon({ active, dir }) {
  if (!active)
    return (
      <FiChevronDown
        className="text-slate-300 transition-colors duration-150 group-hover/sort:text-slate-500 motion-reduce:transition-none dark:text-slate-600 dark:group-hover/sort:text-slate-400"
        aria-hidden="true"
      />
    );
  return dir === "asc" ? <FiChevronUp aria-hidden="true" /> : <FiChevronDown aria-hidden="true" />;
}

function PageBtn({ disabled, active, onClick, children }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cx(
        "grid h-8 min-w-8 place-items-center rounded-md px-2 text-sm font-medium transition duration-150 motion-reduce:transition-none",
        focusRing,
        active
          ? "bg-violet-600 text-white shadow-sm"
          : cx(
              "text-slate-600 hover:bg-slate-200/80 hover:text-slate-900 active:scale-95 motion-reduce:active:scale-100",
              "disabled:pointer-events-none disabled:opacity-40",
              "dark:text-neutral-300 dark:hover:bg-white/10 dark:hover:text-white"
            )
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
      className={cx(CONSOLE.checkbox, focusRing)}
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
  // Show a ⌘K / Ctrl K hint that actually focuses the field. Opt-in, because the shortcut
  // is global: two tables mounted at once would both claim the same chord.
  searchShortcut = false,
  // Extra controls (filter dropdowns, segmented toggles) rendered in the toolbar beside
  // the search field. Filtering itself stays the caller's — this is layout only.
  toolbar,
  // bulk selection (opt-in)
  selectable = false,
  onSelectionChange,
  bulkActions,
}) {
  const [sort, setSort] = useState(initialSort); // { key, dir } | null
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(() => new Set());
  const searchRef = useRef(null);

  // ⌘K on Apple keyboards, Ctrl K elsewhere. The hint is only rendered when the listener is
  // registered, so the UI never advertises a chord that does nothing.
  const isApple =
    typeof navigator !== "undefined" &&
    /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent || "");
  const shortcutOn = searchable && searchShortcut;
  useEffect(() => {
    if (!shortcutOn) return;
    const onKey = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key?.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
        searchRef.current?.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcutOn]);

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
      {(searchable || toolbar) && (
        <div className="flex flex-wrap items-center gap-2 px-4 pb-3 pt-1">
          {searchable && (
            <div className="relative min-w-0 flex-1 sm:max-w-xs">
              <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
              <input
                ref={searchRef}
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                aria-label="Search table"
                className={cx(CONSOLE.search, shortcutOn && "pr-16", focusRing)}
              />
              {shortcutOn && (
                <kbd
                  aria-hidden="true"
                  className={cx(
                    "pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 items-center gap-0.5 rounded border px-1.5 py-0.5 sm:flex",
                    "border-slate-200 bg-slate-50 text-[10px] font-semibold text-slate-400",
                    "dark:border-white/10 dark:bg-white/[0.05] dark:text-neutral-500"
                  )}
                >
                  {isApple ? "⌘" : "Ctrl"} K
                </kbd>
              )}
            </div>
          )}
          {toolbar && <div className="flex flex-wrap items-center gap-2">{toolbar}</div>}
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
                    // A step darker than the old slate-400: at 11px uppercase, 400 on a tinted
                    // header row sat under the contrast floor in light mode.
                    "px-4 py-3 text-[11px] font-semibold uppercase tracking-wider text-slate-500 dark:text-neutral-500",
                    alignCls(c.align),
                    c.headerClassName
                  )}
                  aria-sort={sort?.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                >
                  {c.sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(c.key)}
                      title={`Sort by ${typeof c.header === "string" ? c.header : c.key}`}
                      className={cx(
                        // The header itself becomes the hover target: a tinted, rounded hit area
                        // rather than a text-colour nudge that nobody notices in light mode.
                        "group/sort -mx-1.5 inline-flex items-center gap-1 rounded-md px-1.5 py-0.5",
                        "transition-colors duration-150 motion-reduce:transition-none",
                        "hover:bg-slate-200/70 hover:text-slate-700 dark:hover:bg-white/10 dark:hover:text-slate-100",
                        sort?.key === c.key && "text-violet-700 dark:text-violet-300",
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
                  {selectable && <td className="px-4 py-3.5" />}
                  {columns.map((c, ci) => (
                    <td key={c.key} className="px-4 py-3.5">
                      {/* Widths vary per column so a loading table reads as rows of content
                          rather than a uniform grey grid. */}
                      <div
                        className={cx(
                          "zk-skeleton h-4 rounded bg-slate-200 dark:bg-white/[0.07]",
                          c.align === "right" ? "ml-auto w-12" : ci === 0 ? "w-40" : "w-24"
                        )}
                      />
                    </td>
                  ))}
                  {rowActions && <td className="px-4 py-3.5" />}
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
                      // `dark:hover:bg-white/[0.06]/40` was here — two opacity modifiers on one
                      // utility, which Tailwind drops, so dark rows had no hover at all.
                      "group transition-colors duration-150 motion-reduce:transition-none",
                      "hover:bg-slate-100/80 dark:hover:bg-white/[0.05]",
                      isSelected && "bg-violet-50/60 dark:bg-violet-500/10",
                      // A clickable row says so: pointer plus a violet leading edge on hover, which
                      // reads even for an operator who cannot distinguish the background tint. The
                      // edge is transparent at rest, so hovering never shifts the row.
                      onRowClick &&
                        "cursor-pointer border-l-[3px] border-l-transparent hover:border-l-violet-500 dark:hover:border-l-violet-400"
                    )}
                  >
                    {selectable && (
                      <td className="px-4 py-3.5">
                        <CheckBox checked={isSelected} onChange={() => toggleRow(key)} label="Select row" />
                      </td>
                    )}
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={cx(
                          "px-4 py-3.5 text-sm text-slate-600 dark:text-neutral-300",
                          alignCls(c.align),
                          c.mono ? "font-mono tabular-nums" : c.align === "right" && "tabular-nums",
                          c.className
                        )}
                      >
                        {c.render ? c.render(row) : row[c.key]}
                      </td>
                    ))}
                    {rowActions && (
                      <td className="px-4 py-3.5 text-right">
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
                <td colSpan={colCount} className="px-4 py-20 text-center">
                  {empty ? (
                    <div className="zk-fade-in mx-auto max-w-sm">
                      {empty.icon && (
                        // Concentric rings behind the glyph give the state a centre of gravity
                        // instead of a small grey dot floating in a large empty table.
                        <div className="relative mx-auto mb-5 grid h-20 w-20 place-items-center">
                          <span
                            aria-hidden="true"
                            className="absolute inset-0 rounded-full bg-gradient-to-br from-violet-500/10 to-indigo-500/5 blur-xl"
                          />
                          <span
                            aria-hidden="true"
                            className="absolute inset-2 rounded-full border border-slate-200 dark:border-white/10"
                          />
                          <span
                            aria-hidden="true"
                            className="zk-float absolute inset-5 rounded-full border border-slate-200 dark:border-white/[0.14]"
                          />
                          <span className="relative grid h-11 w-11 place-items-center rounded-full bg-violet-100 text-violet-600 dark:bg-violet-500/15 dark:text-violet-400">
                            <empty.icon className="text-xl" aria-hidden="true" />
                          </span>
                        </div>
                      )}
                      <p className="text-[15px] font-semibold text-slate-900 dark:text-white">{empty.title}</p>
                      {empty.description && (
                        <p className="mx-auto mt-1.5 max-w-xs text-[13px] leading-relaxed text-slate-500 dark:text-neutral-400">
                          {empty.description}
                        </p>
                      )}
                      {empty.action && <div className="mt-5 flex justify-center">{empty.action}</div>}
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
