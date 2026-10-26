import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { FiCheck, FiChevronDown } from "react-icons/fi";
import { CONSOLE, cx, focusRing } from "./tokens";

// ONE dropdown for every console select: workspace, time range, status/environment filters.
//
// Replaces the native <select> where a richer trigger is needed (icon, two-line label,
// selected indicator) without giving up the keyboard contract a native select has:
//   Enter/Space/ArrowDown/ArrowUp  open
//   ArrowUp/ArrowDown             move the active option
//   Home/End                      first/last
//   Enter/Space                   commit the active option
//   Escape / click outside / blur close, returning focus to the trigger
// The list is role="listbox" with aria-activedescendant, so a screen reader announces the
// active option as it moves — the same behaviour it gets from a native select.
//
// `options`: [{ value, label, hint?, icon?, dot? }]. `value === null|""` selects nothing.
// Controlled only: it never holds a selection of its own, so the caller's state stays the
// single source of truth and existing onChange handlers keep working unchanged.
// Trigger geometry. `md` is the console filter-bar field (h-9), which every existing caller
// gets by default; `lg` matches the taller inputs on a public form, so a page can use this
// dropdown beside an <input> without the two disagreeing about height.
const SIZES = {
  md: "h-9 rounded-lg text-[13px]",
  lg: "h-11 rounded-xl text-sm",
};

// Menu geometry. MAX_H is what max-h-72 used to be, kept as the ceiling so a short list in a
// tall window looks exactly as it did; MIN_H stops a cramped viewport producing a menu too
// short to use, in which case it scrolls. GAP matches the mt-1.5/mb-1.5 offset and EDGE keeps
// the menu off the viewport edge.
const MAX_H = 288;
const MIN_H = 160;
const GAP = 6;
const EDGE = 8;

export default function Dropdown({
  value,
  onChange,
  options = [],
  label,
  placeholder = "Select…",
  align = "left",
  width = "w-56",
  size = "md",
  className = "",
  triggerClassName = "",
  disabled = false,
  title,
  icon: Icon,
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [place, setPlace] = useState({ up: false, maxHeight: MAX_H });
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const triggerRef = useRef(null);

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null;

  // Open with the current selection active, so arrow keys continue from where the user is.
  const openMenu = () => {
    if (disabled) return;
    setActive(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  };
  const closeMenu = ({ refocus = true } = {}) => {
    setOpen(false);
    setActive(-1);
    if (refocus) triggerRef.current?.focus();
  };

  // How tall the menu may be, and which way it opens.
  //
  // max-h-72 alone is a guess about the page, not about the screen: a trigger low in a short
  // viewport still produced a menu running off the bottom, and a list of every organization
  // (the Users filter walks all of them) is long enough to do it every time. The space that
  // actually exists below the trigger — and above it — is measured here instead, and the menu
  // opens upward when that is the side with room.
  //
  // Re-measured on scroll and resize because the trigger moves: the console's main region is
  // its own scroll container, so the viewport gap changes without the window resizing.
  useLayoutEffect(() => {
    if (!open) return undefined;
    const measure = () => {
      const r = triggerRef.current?.getBoundingClientRect();
      if (!r) return;
      const below = window.innerHeight - r.bottom - GAP - EDGE;
      const above = r.top - GAP - EDGE;
      // Flip only when below is genuinely the worse side — never for a few pixels, which
      // would make the menu jump sides as the page scrolls.
      const up = below < Math.min(MAX_H, above) && above > below;
      setPlace({ up, maxHeight: Math.max(MIN_H, Math.min(MAX_H, up ? above : below)) });
    };
    measure();
    window.addEventListener("resize", measure);
    // capture: the scroll happens on an ancestor (main), not on window.
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open]);

  // Opening on the current selection is only useful if it is on screen.
  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) closeMenu({ refocus: false });
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Move DOM focus into the list so keystrokes land here rather than on the page.
  useEffect(() => {
    if (open) listRef.current?.focus();
  }, [open]);

  const commit = (i) => {
    const opt = options[i];
    if (!opt || opt.disabled) return;
    onChange?.(opt.value);
    closeMenu();
  };

  const onKeyDown = (e) => {
    const last = options.length - 1;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => (i >= last ? 0 : i + 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => (i <= 0 ? last : i - 1));
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(last);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        commit(active);
        break;
      case "Escape":
        e.preventDefault();
        closeMenu();
        break;
      case "Tab":
        closeMenu({ refocus: false });
        break;
      default:
        break;
    }
  };

  const onTriggerKeyDown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openMenu();
    }
  };

  const listId = `dd-list-${label?.replace(/\W+/g, "-").toLowerCase() || "menu"}`;

  return (
    <div ref={rootRef} className={cx("relative", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={onTriggerKeyDown}
        disabled={disabled}
        title={title}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        className={cx(
          "flex w-full items-center gap-2 border px-3 text-left font-medium",
          SIZES[size] || SIZES.md,
          "transition-colors duration-150 motion-reduce:transition-none",
          CONSOLE.control,
          "disabled:cursor-not-allowed disabled:opacity-50",
          open && "border-violet-400 dark:border-violet-500/50",
          focusRing,
          triggerClassName
        )}
      >
        {Icon && <Icon className={cx("shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />}
        {selected?.dot}
        <span className={cx("min-w-0 flex-1 truncate", !selected && CONSOLE.faint)}>
          {selected ? selected.label : placeholder}
        </span>
        <FiChevronDown
          className={cx(
            "shrink-0 text-[14px] transition-transform duration-150 motion-reduce:transition-none",
            CONSOLE.faint,
            open && "rotate-180"
          )}
          aria-hidden="true"
        />
      </button>

      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          tabIndex={-1}
          aria-label={label}
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
          onKeyDown={onKeyDown}
          onBlur={(e) => {
            if (!rootRef.current?.contains(e.relatedTarget)) closeMenu({ refocus: false });
          }}
          className={cx(
            "zk-pop-in absolute z-40 rounded-xl border p-1 shadow-xl outline-none",
            // overscroll-contain so reaching the end of the list does not start scrolling the
            // page underneath it; overflow-x-hidden because a long name truncates rather than
            // widening the menu.
            "overflow-y-auto overflow-x-hidden overscroll-contain",
            "border-slate-200 bg-white shadow-slate-900/10",
            "dark:border-white/10 dark:bg-neutral-950 dark:shadow-black/60",
            "zk-scroll-thin",
            place.up ? "bottom-full mb-1.5" : "top-full mt-1.5",
            align === "right" ? "right-0" : "left-0",
            width
          )}
          style={{ maxHeight: place.maxHeight }}
        >
          {options.map((opt, i) => {
            const isSelected = opt.value === value;
            return (
              <li
                key={String(opt.value)}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={isSelected}
                data-active={active === i || undefined}
                aria-disabled={opt.disabled || undefined}
                onMouseEnter={() => setActive(i)}
                onClick={() => commit(i)}
                className={cx(
                  "flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px]",
                  "transition-colors duration-150 motion-reduce:transition-none",
                  opt.disabled && "pointer-events-none opacity-50",
                  active === i
                    ? "bg-slate-100 text-slate-900 dark:bg-white/[0.08] dark:text-white"
                    : CONSOLE.body,
                  isSelected && "font-semibold"
                )}
              >
                {opt.dot}
                {opt.icon && <opt.icon className="shrink-0 text-[14px]" aria-hidden="true" />}
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{opt.label}</span>
                  {opt.hint && (
                    <span className={cx("block truncate text-[11px] font-normal", CONSOLE.faint)}>
                      {opt.hint}
                    </span>
                  )}
                </span>
                {/* A check, not only a colour — the selected row has to be tellable apart
                    from the merely-hovered one without relying on hue. */}
                <FiCheck
                  className={cx(
                    "shrink-0 text-[14px] text-violet-600 dark:text-violet-400",
                    isSelected ? "opacity-100" : "opacity-0"
                  )}
                  aria-hidden="true"
                />
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
