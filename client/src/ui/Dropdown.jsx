import { useEffect, useRef, useState } from "react";
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
export default function Dropdown({
  value,
  onChange,
  options = [],
  label,
  placeholder = "Select…",
  align = "left",
  width = "w-56",
  className = "",
  triggerClassName = "",
  disabled = false,
  title,
  icon: Icon,
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
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
          "flex h-9 w-full items-center gap-2 rounded-lg border px-3 text-left text-[13px] font-medium",
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
            "zk-pop-in absolute z-40 mt-1.5 max-h-72 overflow-auto rounded-xl border p-1 shadow-xl outline-none",
            "border-slate-200 bg-white shadow-slate-900/10",
            "dark:border-white/10 dark:bg-neutral-950 dark:shadow-black/60",
            "zk-scroll-thin",
            align === "right" ? "right-0" : "left-0",
            width
          )}
        >
          {options.map((opt, i) => {
            const isSelected = opt.value === value;
            return (
              <li
                key={String(opt.value)}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={isSelected}
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
