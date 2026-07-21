import { useState } from "react";
import { FiChevronDown, FiSearch } from "react-icons/fi";
import { cx } from "./tokens";

function Item({ q, a, open, onToggle, id }) {
  return (
    <div className="border-b border-slate-200 dark:border-slate-800">
      <h3>
        <button
          id={`${id}-btn`}
          aria-expanded={open}
          aria-controls={`${id}-panel`}
          onClick={onToggle}
          className="group flex w-full items-center justify-between gap-4 py-5 text-left"
        >
          <span className="font-semibold text-slate-900 transition-colors group-hover:text-emerald-700 dark:text-white dark:group-hover:text-emerald-400">{q}</span>
          <FiChevronDown className={cx("shrink-0 text-slate-400 transition-transform duration-300", open && "rotate-180 text-emerald-600 dark:text-emerald-400")} />
        </button>
      </h3>
      {/* grid-rows trick animates height with no JS measurement */}
      <div
        id={`${id}-panel`}
        role="region"
        aria-labelledby={`${id}-btn`}
        className={cx("grid overflow-hidden transition-all duration-300", open ? "grid-rows-[1fr] pb-5" : "grid-rows-[0fr]")}
      >
        <p className="min-h-0 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{a}</p>
      </div>
    </div>
  );
}

// Accessible accordion, optionally searchable. `items`: [{ q, a }].
export default function FAQ({ items, searchable = true, className = "" }) {
  const [query, setQuery] = useState("");
  const [openIdx, setOpenIdx] = useState(0);

  const q = query.trim().toLowerCase();
  const filtered = q ? items.filter((f) => f.q.toLowerCase().includes(q) || f.a.toLowerCase().includes(q)) : items;

  return (
    <div className={className}>
      {searchable && (
        <div className="relative">
          <FiSearch className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search questions…"
            aria-label="Search FAQ"
            className="w-full rounded-xl border border-slate-200 bg-white py-3 pl-11 pr-4 text-slate-800 outline-none transition focus:border-emerald-500 focus:ring-2 focus:ring-emerald-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500 dark:focus:border-emerald-500 dark:focus:ring-emerald-500/20"
          />
        </div>
      )}

      <div className={cx(searchable && "mt-6")}>
        {filtered.length === 0 ? (
          <p className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">No questions match “{query}”.</p>
        ) : (
          filtered.map((f, i) => (
            <Item
              key={f.q}
              id={`faq-${i}`}
              q={f.q}
              a={f.a}
              open={openIdx === i}
              onToggle={() => setOpenIdx((cur) => (cur === i ? -1 : i))}
            />
          ))
        )}
      </div>
    </div>
  );
}
