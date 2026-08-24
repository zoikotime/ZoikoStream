import { useEffect, useMemo, useRef, useState } from "react";
import { FiClock } from "react-icons/fi";
import { cx, focusRing } from "./tokens";
import { Input } from "./forms";

// Time field: a real <input type="time"> plus OUR OWN scrollable slot list.
//
// Chromium's native time dropdown is browser chrome, not DOM — no selector reaches it, so it
// cannot carry the console's scrollbar or its surface. Here the native picker's trigger is
// suppressed (`.zk-time-field` in index.css hides ::-webkit-calendar-picker-indicator) and the
// clock button opens a styled list instead.
//
// The input itself is untouched, deliberately:
//   · typing stays exact — every minute is a real slot, so the list and typing always agree
//   · the browser keeps its own parsing, validation and locale (12h vs 24h) behaviour
//   · Firefox and Safari, which have no dropdown to suppress, lose nothing
// The value contract is unchanged: "HH:MM" or "", which is what toISO() consumes.
const STEP_MIN = 1;

const pad = (n) => String(n).padStart(2, "0");

// 00:00 … 23:45. Both notations are shown: the field is 24h, but most people read a schedule
// in 12h and one of the two is always the one they were given.
const SLOTS = Array.from({ length: (24 * 60) / STEP_MIN }, (_, i) => {
  const mins = i * STEP_MIN;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return { value: `${pad(h)}:${pad(m)}`, meridiem: `${h12}:${pad(m)} ${h < 12 ? "AM" : "PM"}` };
});

// Index of the slot at or nearest to `value`, so opening on 15:10 lands you at 15:15 rather
// than at midnight.
function nearestSlot(value) {
  const [h, m] = String(value || "").split(":").map(Number);
  if (!Number.isFinite(h) || !Number.isFinite(m)) return -1;
  const i = Math.round((h * 60 + m) / STEP_MIN);
  return Math.min(SLOTS.length - 1, Math.max(0, i));
}

// Where an empty field's list opens: the clock's current time, not midnight — you pick
// "in 20 minutes" starting from now, not by scrolling up from 00:00.
function nowSlot() {
  const d = new Date();
  return nearestSlot(`${pad(d.getHours())}:${pad(d.getMinutes())}`);
}

export default function TimeField({
  value,
  onChange,
  variant = "console",
  label,
  className = "",
  ...rest
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const buttonRef = useRef(null);

  const exact = useMemo(() => SLOTS.findIndex((s) => s.value === value), [value]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // The active slot is chosen when opening, not in an effect — deriving it from `value` in an
  // effect would re-centre the list on every keystroke and fight the user's own scrolling.
  const openList = () => {
    const i = exact >= 0 ? exact : nearestSlot(value);
    setActive(i >= 0 ? i : nowSlot());
    setOpen(true);
  };

  // DOM-only: take focus and bring the active slot into view once the list exists.
  useEffect(() => {
    if (!open) return;
    listRef.current?.focus();
    listRef.current?.querySelector('[data-slot="active"]')?.scrollIntoView({ block: "center" });
  }, [open]);

  const commit = (i) => {
    onChange?.(SLOTS[i].value);
    setOpen(false);
    buttonRef.current?.focus();
  };

  const onKeyDown = (e) => {
    const last = SLOTS.length - 1;
    const move = (next) => {
      e.preventDefault();
      const i = Math.min(last, Math.max(0, next));
      setActive(i);
      listRef.current?.children[i]?.scrollIntoView({ block: "nearest" });
    };
    switch (e.key) {
      case "ArrowDown": return move(active + 1);
      case "ArrowUp": return move(active - 1);
      case "PageDown": return move(active + 4);
      case "PageUp": return move(active - 4);
      case "Home": return move(0);
      case "End": return move(last);
      case "Enter":
      case " ":
        e.preventDefault();
        if (active >= 0) commit(active);
        break;
      case "Escape":
        e.preventDefault();
        setOpen(false);
        buttonRef.current?.focus();
        break;
      case "Tab":
        setOpen(false);
        break;
      default:
        break;
    }
  };

  return (
    <div ref={rootRef} className={cx("relative", className)}>
      <Input
        variant={variant}
        type="time"
        value={value}
        onChange={(e) => onChange?.(e.target.value)}
        className="zk-time-field pr-10"
        {...rest}
      />

      <button
        ref={buttonRef}
        type="button"
        onClick={() => (open ? setOpen(false) : openList())}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label ? `Choose ${label}` : "Choose a time"}
        className={cx(
          "absolute right-1.5 top-1/2 grid h-7 w-7 -translate-y-1/2 place-items-center rounded-md",
          "text-slate-400 transition-colors duration-150 motion-reduce:transition-none",
          "hover:bg-slate-100 hover:text-slate-600 dark:hover:bg-white/10 dark:hover:text-slate-200",
          focusRing
        )}
      >
        <FiClock className="text-[15px]" aria-hidden="true" />
      </button>

      {open && (
        <ul
          ref={listRef}
          role="listbox"
          tabIndex={-1}
          aria-label={label || "Time"}
          onKeyDown={onKeyDown}
          onBlur={(e) => {
            if (!rootRef.current?.contains(e.relatedTarget)) setOpen(false);
          }}
          className={cx(
            // zk-scroll-thin is the console's scrollbar — the whole point of replacing the
            // native dropdown, which could never carry it.
            "zk-scroll-thin zk-pop-in absolute z-40 mt-1.5 max-h-56 w-full overflow-y-auto rounded-xl border p-1 shadow-xl outline-none",
            "border-slate-200 bg-white shadow-slate-900/10",
            "dark:border-white/10 dark:bg-neutral-950 dark:shadow-black/60"
          )}
        >
          {SLOTS.map((slot, i) => (
            <li
              key={slot.value}
              role="option"
              aria-selected={i === exact}
              data-slot={i === active ? "active" : undefined}
              onMouseEnter={() => setActive(i)}
              onClick={() => commit(i)}
              className={cx(
                "flex cursor-pointer items-baseline justify-between gap-3 rounded-lg px-2.5 py-1.5",
                "transition-colors duration-150 motion-reduce:transition-none",
                i === active
                  ? "bg-slate-100 dark:bg-white/[0.08]"
                  : "text-slate-600 dark:text-neutral-300",
                i === exact && "font-semibold text-violet-700 dark:text-violet-300"
              )}
            >
              <span className="tabular-nums">{slot.value}</span>
              <span className="text-[11px] tabular-nums text-slate-400 dark:text-neutral-500">
                {slot.meridiem}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
