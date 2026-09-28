import { useEffect, useRef, useState } from "react";
import { FiSearch, FiX } from "react-icons/fi";
import api from "../../api";
import { CONSOLE, cx } from "../../ui/tokens";

// Organization filter that SEARCHES the server instead of listing a fetched page.
//
// The console's other organization dropdowns load the first 100/200 organizations, so any
// organization past that point cannot be selected at all. This asks GET /admin/organizations
// with `q` as the operator types and offers the matches.
//
//   <OrgFilter value={org} onChange={setOrg} />   // org is { id, name } | null
export default function OrgFilter({ value, onChange, label = "Filter by organization" }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [matches, setMatches] = useState([]);
  const [failed, setFailed] = useState(false);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    const t = setTimeout(() => {
      api.get("/admin/organizations", { params: { q: text.trim() || undefined, page_size: 20 } })
        .then((r) => { if (alive) { setMatches(r.data.items || []); setFailed(false); } })
        .catch(() => { if (alive) { setMatches([]); setFailed(true); } });
    }, 250);
    return () => { alive = false; clearTimeout(t); };
  }, [text, open]);

  useEffect(() => {
    if (!open) return undefined;
    const close = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  if (value) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] text-slate-700 dark:border-white/15 dark:bg-white/5 dark:text-neutral-100" data-testid="org-filter-selected">
        {value.name}
        <button type="button" onClick={() => onChange(null)} aria-label="Clear organization filter" className="text-slate-400 hover:text-slate-700 dark:hover:text-white">
          <FiX />
        </button>
      </span>
    );
  }

  return (
    <div ref={boxRef} className="relative min-w-[220px]">
      <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
      <input
        value={text}
        onChange={(e) => { setText(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}
        placeholder="All organizations"
        aria-label={label}
        role="combobox"
        aria-expanded={open}
        aria-autocomplete="list"
        className={CONSOLE.search}
      />
      {open && (
        <ul role="listbox" className={cx("absolute z-20 mt-1 max-h-64 w-full overflow-auto rounded-lg border border-slate-200 bg-white py-1 text-[13px] shadow-lg dark:border-white/10 dark:bg-neutral-950")}>
          {failed && <li className="px-3 py-2 text-rose-600 dark:text-rose-400">Couldn&apos;t search organizations.</li>}
          {!failed && matches.length === 0 && <li className="px-3 py-2 text-slate-500">No matching organizations</li>}
          {matches.map((o) => (
            <li key={o.id}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                onClick={() => { onChange({ id: o.id, name: o.name }); setOpen(false); setText(""); }}
                className="block w-full px-3 py-1.5 text-left text-slate-700 hover:bg-slate-50 dark:text-neutral-100 dark:hover:bg-white/10"
              >
                {o.name}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
