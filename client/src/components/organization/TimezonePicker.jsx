import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FiCheck, FiChevronDown, FiSearch } from "react-icons/fi";
import { TIMEZONE_ROWS, filterTimezones, groupTimezones } from "../../data/timezoneSearch";
import { cx } from "../../ui/tokens";

// Lookup for rendering the CURRENT value's label. Not a search index — see data/timezoneSearch.
const BY_ZONE = new Map(TIMEZONE_ROWS.map((r) => [r.zone, r]));

// The timezone field: the original grouped list, with a search box on top.
//
// Deliberately NOT a redesign. It renders the same region headings the old <select>'s
// <optgroup>s did — Universal, Americas, Europe, Asia… — with the same full tzLabel() text
// underneath ("IST (India) — Mumbai, Delhi, Bengaluru, Colombo (GMT+5:30)"), in the same
// order. The only addition is the search box, which narrows that list in place; a group whose
// rows all filter out disappears rather than leaving an empty heading.
//
// The VALUE is unchanged and must stay unchanged: an IANA identifier ("Asia/Kolkata", never
// "IST"). It is what POST /events stores and what Intl.DateTimeFormat({ timeZone }) is handed
// downstream, and that throws a RangeError on anything else.
//
// No network, no library: the list is already local, and searching it is a substring match.

// Trigger geometry copied from ui/forms Select (the "console" variant) so the field sits in
// the modal's grid identically to the Date and Time inputs beside it. Duplicated rather than
// exported from there because this is a button, not a <select>, and only the resting look is
// shared — the focus ring below belongs to the listbox, not the control.
const TRIGGER = "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left text-sm "
  + "text-slate-800 outline-none transition hover:border-slate-300 focus:border-violet-400 "
  + "focus:ring-2 focus:ring-violet-500/20 dark:border-slate-700 dark:bg-slate-800 "
  + "dark:text-slate-100 dark:hover:border-slate-600";

export default function TimezonePicker({ value, onChange, id, className = "" }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const rootRef = useRef(null);
  // The panel lives in a portal at document.body, so it is NOT inside rootRef — the
  // outside-click check has to consult it separately or every click within the dropdown
  // (the search box included) would read as "outside" and close it.
  const panelRef = useRef(null);
  const searchRef = useRef(null);
  const listRef = useRef(null);
  const listId = useId();

  // Where to paint the panel, in viewport coordinates.
  //
  // It is rendered through a PORTAL rather than inside this component's own DOM, because the
  // Create Event modal's body is `overflow-y-auto` (ui/Modal.jsx) — an absolutely positioned
  // child would be clipped at that scroll container's edge, which is exactly where a 400px
  // list opened near the bottom of the form would land. A portal + fixed coordinates lets the
  // panel float over the modal, as the design shows.
  const [rect, setRect] = useState(null);


  // Two views of the same result: `matches` is flat, so the keyboard has a single index to
  // walk across group boundaries; `grouped` is what actually renders.
  const matches = useMemo(() => filterTimezones(query), [query]);
  const grouped = useMemo(() => groupTimezones(query), [query]);

  // The selected zone's own label, read from the same table. An unrecognised value (a zone
  // stored before this list existed) shows the raw identifier rather than a guess or a blank.
  const selectedLabel = BY_ZONE.get(value)?.label || value || "Select a timezone";

  // Focus the search box once the panel actually exists.
  //
  // Keyed on `rect` as well as `open`: the panel is portalled and only renders after the
  // layout effect below has measured the trigger, so on the render where `open` first flips
  // true there is no input to focus yet. Missing that left the dropdown open but unfocused —
  // Escape, arrows and typing all went nowhere.
  useEffect(() => {
    if (!open || !rect) return;
    searchRef.current?.focus();
    // Only the transition into "panel exists" matters; re-placing on scroll must not steal
    // focus back mid-interaction.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, Boolean(rect)]);

  // Close on an outside click or Escape. Pointerdown, not click, so the list closes before a
  // click elsewhere in the modal lands on whatever was underneath it.
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (e) => {
      const inTrigger = rootRef.current?.contains(e.target);
      const inPanel = panelRef.current?.contains(e.target);
      if (!inTrigger && !inPanel) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  // Keep the highlighted row in view when arrowing past the visible window.
  useEffect(() => {
    if (!open) return;
    // Optional CALL, not just optional chaining: scrollIntoView is unimplemented in jsdom and
    // in some embedded webviews, and keeping the highlight in view is a nicety — it must not
    // be able to throw inside an effect and take the whole picker down.
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [active, open]);

  const place = () => {
    const el = rootRef.current;
    if (!el) return;
    const t = el.getBoundingClientRect();
    const GAP = 8;
    const MARGIN = 14;        // viewport breathing room on every edge
    const MAX_W = 440;        // comfortable for a full label without dominating the modal
    const MAX_H = 400;        // WHOLE panel, search header included
    const MIN_H = 200;        // below this, flipping is better than shrinking further

    const width = Math.min(MAX_W, window.innerWidth - MARGIN * 2);
    // Space available to the panel on each side, already excluding the viewport margin.
    const below = window.innerHeight - t.bottom - GAP - MARGIN;
    const above = t.top - GAP - MARGIN;

    // Prefer below. Flip up only when below cannot hold a usable panel AND above is roomier.
    const flip = below < MIN_H && above > below;
    const room = Math.max(flip ? above : below, 0);

    setRect({
      // Left-anchored to the field, pulled back only as far as needed to stay on screen.
      left: Math.max(MARGIN, Math.min(t.left, window.innerWidth - width - MARGIN)),
      top: flip ? undefined : t.bottom + GAP,
      bottom: flip ? window.innerHeight - t.top + GAP : undefined,
      width,
      // The budget for the ENTIRE panel, not just the list.
      //
      // This is what was wrong before: maxHeight was applied to the scrolling list alone, so
      // the panel's real height was list + search header + borders. Opened upward near the top
      // of the window that overflowed past y=0 and took the search box off-screen with it —
      // the one control that must never scroll away. Capping the panel and letting the list
      // flex inside it keeps the header on screen in both directions.
      maxHeight: Math.max(MIN_H, Math.min(MAX_H, room)),
    });
  };

  // Layout effect: measured and positioned before paint, so the panel never appears in the
  // wrong place for a frame.
  useLayoutEffect(() => {
    if (!open) return undefined;
    place();
    const onMove = () => place();
    window.addEventListener("resize", onMove);
    // Capture phase: the modal body is the element that actually scrolls, not the window.
    window.addEventListener("scroll", onMove, true);
    return () => {
      window.removeEventListener("resize", onMove);
      window.removeEventListener("scroll", onMove, true);
    };
  }, [open]);

  const openList = () => {
    setQuery("");
    // Start on the current selection so Enter without typing is a no-op rather than a change.
    const i = matches.findIndex((r) => r.zone === value);
    setActive(i >= 0 ? i : 0);
    setOpen(true);
  };

  const commit = (zone) => {
    if (zone) onChange(zone);
    setOpen(false);
    setQuery("");
  };

  const onSearchKeyDown = (e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => Math.min(i + 1, matches.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      commit(matches[active]?.zone);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setOpen(false);
    } else if (e.key === "Home") {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setActive(Math.max(matches.length - 1, 0));
    }
  };

  return (
    <div ref={rootRef} className={cx("relative", className)}>
      <button
        type="button"
        id={id}
        onClick={() => (open ? setOpen(false) : openList())}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            openList();
          }
        }}
        className={cx(TRIGGER, "cursor-pointer pr-9")}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
      >
        <span className="block truncate">{selectedLabel}</span>
      </button>
      <FiChevronDown
        className={cx(
          "pointer-events-none absolute right-3 top-[1.15rem] -translate-y-1/2 text-slate-400 transition-transform duration-150 motion-reduce:transition-none",
          open && "rotate-180"
        )}
        aria-hidden="true"
      />

      {open && rect && createPortal(
        <div
          ref={panelRef}
          style={{
            position: "fixed",
            left: rect.left,
            ...(rect.top !== undefined ? { top: rect.top } : { bottom: rect.bottom }),
            width: rect.width,
            // Caps the WHOLE panel. The search header is a non-shrinking flex child and the
            // list flexes into what is left, so the header cannot be pushed off-screen when
            // the panel opens upward — which is exactly what happened when this cap lived on
            // the list alone.
            maxHeight: rect.maxHeight,
          }}
          className={cx(
            "z-[60] flex flex-col overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl",
            "ring-1 ring-black/[0.03] dark:border-slate-700 dark:bg-slate-800 dark:ring-white/5"
          )}
        >
          {/* Pinned by flex layout, not by `sticky`: it is a shrink-0 sibling of the scroll
              container rather than a child of it, so it is always visible whichever way the
              panel opened and never scrolls with the list. */}
          <div className="relative shrink-0 border-b border-slate-100 bg-white p-2.5 dark:border-slate-700 dark:bg-slate-800">
            <FiSearch
              className="pointer-events-none absolute left-[1.4rem] top-1/2 -translate-y-1/2 text-slate-400"
              aria-hidden="true"
            />
            <input
              ref={searchRef}
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                // Any change to the result set invalidates the old index.
                setActive(0);
              }}
              onKeyDown={onSearchKeyDown}
              placeholder="Search timezone…"
              aria-label="Search timezone"
              aria-controls={listId}
              aria-activedescendant={open && matches[active] ? `${listId}-${matches[active].zone}` : undefined}
              className="w-full rounded-lg border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 hover:border-slate-300 focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:placeholder:text-slate-500"
            />
          </div>

          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            aria-label="Timezone"
            className="zk-scroll-thin min-h-0 flex-1 overflow-y-auto overflow-x-hidden pb-2"
          >
            {matches.length === 0 ? (
              <li className="px-3 py-10 text-center text-sm text-slate-500 dark:text-slate-400">
                No timezones found
              </li>
            ) : (
              grouped.map(([group, rows]) => (
                <li key={group}>
                  {/* The region heading the old <optgroup> rendered. role="presentation" so
                      it is not announced as a selectable option. */}
                  <p
                    role="presentation"
                    className="px-3.5 pb-1.5 pt-3.5 text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-400 dark:text-slate-500"
                  >
                    {group}
                  </p>
                  <ul role="group" aria-label={group}>
                    {rows.map((r) => {
                      // Index into the FLAT list, so ArrowDown walks across group boundaries
                      // exactly as it did through the old flat <select>.
                      const i = matches.indexOf(r);
                      const selected = r.zone === value;
                      return (
                        <li key={r.zone}>
                          <button
                            type="button"
                            id={`${listId}-${r.zone}`}
                            role="option"
                            aria-selected={selected}
                            data-active={i === active ? "true" : undefined}
                            onMouseMove={() => setActive(i)}
                            onClick={() => commit(r.zone)}
                            className={cx(
                              // Full label, never truncated — this is the text the old
                              // dropdown showed, and it is the whole reason the panel is
                              // wider than the field.
                              "flex w-full items-center justify-between gap-3 px-3.5 py-2 text-left text-sm",
                              "transition-colors duration-100 motion-reduce:transition-none",
                              selected
                                ? "bg-violet-50 font-medium text-violet-900 dark:bg-violet-500/15 dark:text-violet-100"
                                : i === active
                                  ? "bg-slate-50 text-slate-800 dark:bg-white/[0.06] dark:text-slate-100"
                                  : "text-slate-700 dark:text-slate-200"
                            )}
                          >
                            <span>{r.label}</span>
                            {selected && (
                              <FiCheck
                                className="shrink-0 text-violet-600 dark:text-violet-300"
                                aria-hidden="true"
                              />
                            )}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </li>
              ))
            )}
          </ul>
        </div>,
        document.body
      )}
    </div>
  );
}
