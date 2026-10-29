import { useEffect, useLayoutEffect, useRef, useState } from "react";
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

// Menu geometry. The menu was `w-full` of a 220px field, so "Jamaica Bible Church Inc.
// Trinity Ville" wrapped onto two lines. It now grows to fit the names it shows, up to MAX_W;
// a longer name truncates, full name in its title, rather than wrapping. EDGE is ui/Dropdown's
// gap to the edge of the visible area.
const MAX_W = 384;
const EDGE = 8;

// The box the menu must stay inside. The console's main region is its own scroll container
// (see ui/Dropdown), so a menu reaching past it would give it a horizontal scrollbar: its
// clientWidth excludes its own scrollbar, and its left edge is the sidebar's right. Anywhere
// else, the window.
function visibleArea(el) {
  const main = el.closest("main");
  if (main?.clientWidth) {
    const left = main.getBoundingClientRect().left + main.clientLeft;
    return { left, right: left + main.clientWidth };
  }
  return { left: 0, right: document.documentElement.clientWidth || window.innerWidth };
}

export default function OrgFilter({ value, onChange, label = "Filter by organization" }) {
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [matches, setMatches] = useState([]);
  const [failed, setFailed] = useState(false);
  const [place, setPlace] = useState({ alignRight: false, maxWidth: MAX_W });
  const boxRef = useRef(null);
  const fieldRef = useRef(null);

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

  // Which way the menu grows, and how wide it may get: rightward from the field's left edge
  // unless that side is genuinely short, then leftward from its right edge — the flip
  // ui/Dropdown makes vertically. SupportAccessPanel pushes this field to the right of its row
  // (ml-auto), where a rightward menu would run off the screen. Never narrower than the field.
  useLayoutEffect(() => {
    if (!open) return undefined;
    const measure = () => {
      const field = fieldRef.current?.getBoundingClientRect();
      if (!field) return;
      const area = visibleArea(fieldRef.current);
      const roomRight = area.right - EDGE - field.left;
      const roomLeft = field.right - (area.left + EDGE);
      const alignRight = roomRight < Math.min(MAX_W, roomLeft);
      setPlace({ alignRight, maxWidth: Math.max(field.width, Math.min(MAX_W, alignRight ? roomLeft : roomRight)) });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [open]);

  if (value) {
    return (
      <span className="inline-flex max-w-full items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-[13px] text-slate-700 dark:border-white/15 dark:bg-white/5 dark:text-neutral-100" data-testid="org-filter-selected">
        <span className="min-w-0 truncate" title={value.name}>{value.name}</span>
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
        ref={fieldRef}
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
        <ul
          role="listbox"
          style={{ maxWidth: place.maxWidth }}
          className={cx(
            "absolute z-20 mt-1 max-h-64 w-max min-w-full overflow-y-auto overflow-x-hidden rounded-lg border border-slate-200 bg-white py-1 text-[13px] shadow-lg dark:border-white/10 dark:bg-neutral-950",
            place.alignRight ? "right-0" : "left-0"
          )}
        >
          {failed && <li className="px-3 py-2 text-rose-600 dark:text-rose-400">Couldn&apos;t search organizations.</li>}
          {!failed && matches.length === 0 && <li className="px-3 py-2 text-slate-500">No matching organizations</li>}
          {matches.map((o) => (
            <li key={o.id}>
              <button
                type="button"
                role="option"
                aria-selected={false}
                title={o.name}
                onClick={() => { onChange({ id: o.id, name: o.name }); setOpen(false); setText(""); }}
                className="block w-full truncate px-3 py-1.5 text-left text-slate-700 hover:bg-slate-50 dark:text-neutral-100 dark:hover:bg-white/10"
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
