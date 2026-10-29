import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { FiCheck, FiChevronDown } from "react-icons/fi";
import { register } from "./dismissStack";
import { CONSOLE_FIELD } from "./forms/consoleField";
import { CONSOLE, cx, focusRing, z } from "./tokens";

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
//   a printable key               type-ahead: jumps to the next option starting with it
//
// `options`: [{ value, label, hint?, icon?, dot? }]. `value === null|""` selects nothing.
// Controlled only: it never holds a selection of its own, so the caller's state stays the
// single source of truth and existing onChange handlers keep working unchanged.
//
// An open menu is a dismissable layer (ui/dismissStack), like a modal or a drawer: Escape and
// Android's Back close the MENU, not the dialog it was opened in. A dialog's Escape listener is
// on `document` and asks the stack whether the dialog is on top; a menu the stack does not know
// about leaves the answer "yes", so the same keypress closed the menu AND the dialog, form and
// all. Escape is handled the same way here — on `document`, by whoever is on top — so which
// layer closes never depends on the order React's handlers and document listeners run in.
//
// variant="field" is a form field rather than a console filter: it wears the console form
// control's look (ui/forms "console", like the inputs beside it), its menu is the form's
// surface, and it is named by the field's visible <Label id> (`labelId`) as a combobox whose
// text is its value. Its menu is rendered into <body> at fixed coordinates: a form lives in a
// scroll container (a modal's body is overflow-y-auto), which would clip an absolutely
// positioned menu. It is a native <select>'s replacement where the browser-drawn option list is
// the problem — that list is painted outside the page, so no style of ours reaches it or its
// first frame (a white panel over the dark Create Event dialog) and it ignores the dialog's
// bounds.
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
// Keys typed within this window of each other search together ("wo" -> Workshop), as in a
// native select; after it, a key starts a new search.
const TYPEAHEAD_MS = 500;

// The next enabled option, after `from`, whose label starts with `text`. A run of one repeated
// letter cycles through that letter's options instead of looking for "ww".
function typeaheadIndex(options, text, from) {
  const s = text.toLowerCase();
  const cycling = s.length > 1 && [...s].every((c) => c === s[0]);
  const needle = cycling ? s[0] : s;
  // A fresh letter, or the cycle, moves past the current option; a longer prefix may stay on it.
  const start = s.length === 1 || cycling ? from + 1 : Math.max(from, 0);
  for (let k = 0; k < options.length; k += 1) {
    const i = (start + k) % options.length;
    const o = options[i];
    if (!o.disabled && String(o.label).toLowerCase().startsWith(needle)) return i;
  }
  return -1;
}

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
  menuClassName = "",
  disabled = false,
  title,
  icon: Icon,
  variant = "console",
  labelId,
}) {
  const field = variant === "field";
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [place, setPlace] = useState({ up: false, maxHeight: MAX_H });
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const triggerRef = useRef(null);
  const typed = useRef({ text: "", at: 0 });

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : null;

  // Open with the current selection active, so arrow keys continue from where the user is —
  // or on the option a type-ahead key found (`at`).
  const openMenu = (at) => {
    if (disabled) return;
    setActive(at ?? (selectedIndex >= 0 ? selectedIndex : 0));
    setOpen(true);
  };

  const typeahead = (key, from) => {
    const now = Date.now();
    const t = typed.current;
    t.text = now - t.at > TYPEAHEAD_MS ? key : t.text + key;
    t.at = now;
    return typeaheadIndex(options, t.text, from);
  };
  const isTypeaheadKey = (e) => e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey;
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
      // A field's menu floats over its dialog, so the dialog's edges bound it, not just the
      // viewport's: it stays inside the panel the field belongs to.
      const box = field ? triggerRef.current.closest('[role="dialog"]')?.getBoundingClientRect() : null;
      const floor = box ? Math.min(window.innerHeight, box.bottom) : window.innerHeight;
      const ceiling = box ? Math.max(0, box.top) : 0;
      const below = floor - r.bottom - GAP - EDGE;
      const above = r.top - ceiling - GAP - EDGE;
      // Flip only when below is genuinely the worse side — never for a few pixels, which
      // would make the menu jump sides as the page scrolls.
      const up = below < Math.min(MAX_H, above) && above > below;
      setPlace({
        up,
        maxHeight: Math.max(MIN_H, Math.min(MAX_H, up ? above : below)),
        // Viewport coordinates for the field's fixed-position menu: under (or over) the
        // trigger, exactly its width.
        left: r.left,
        width: r.width,
        top: up ? undefined : r.bottom + GAP,
        bottom: up ? window.innerHeight - r.top + GAP : undefined,
      });
    };
    measure();
    window.addEventListener("resize", measure);
    // capture: the scroll happens on an ancestor (main), not on window.
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, field]);

  // Opening on the current selection is only useful if it is on screen.
  useEffect(() => {
    if (!open) return;
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView?.({ block: "nearest" });
  }, [open, active]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      // The list counts as inside: a field's menu is portalled out of rootRef.
      if (rootRef.current?.contains(e.target) || listRef.current?.contains(e.target)) return;
      closeMenu({ refocus: false });
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Escape and Back, as the top dismissable layer (see the note at the top). The dialog this
  // menu sits in registered first, so its listener sees it is no longer on top and stays open.
  useEffect(() => {
    if (!open) return undefined;
    const layer = register(() => closeMenu());
    const onKey = (e) => {
      if (e.key !== "Escape" || !layer.isTop()) return;
      e.preventDefault();
      closeMenu();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      layer.release();
    };
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
      // Escape is the dismiss layer's (above), not this handler's.
      case "Tab":
        // A field's list sits at the end of <body>, so Tab from it would leave the form; back
        // on the trigger, Tab carries on to the next field as it would from a native select.
        closeMenu({ refocus: field });
        break;
      default:
        if (isTypeaheadKey(e)) {
          e.preventDefault();
          const i = typeahead(e.key, active);
          if (i >= 0) setActive(i);
        }
        break;
    }
  };

  const onTriggerKeyDown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      openMenu();
    } else if (isTypeaheadKey(e)) {
      e.preventDefault();
      const i = typeahead(e.key, selectedIndex);
      openMenu(i >= 0 ? i : undefined);
    }
  };

  const listId = `dd-list-${label?.replace(/\W+/g, "-").toLowerCase() || "menu"}`;
  // A field is named by its visible label; a filter by its `label` prop.
  const naming = field && labelId ? { "aria-labelledby": labelId } : { "aria-label": label };

  // A field's row: the form's selected/active tints (the Timezone picker beside it uses the
  // same), so the two menus in one dialog read as one design.
  const fieldOptionTone = (isSelected, isActive) =>
    isSelected
      ? "bg-violet-50 font-medium text-violet-900 dark:bg-violet-500/15 dark:text-violet-100"
      : isActive
        ? "bg-slate-100 text-slate-900 dark:bg-white/[0.06] dark:text-slate-100"
        : "text-slate-700 dark:text-slate-200";

  const menu = (
    <ul
      ref={listRef}
      id={listId}
      role="listbox"
      tabIndex={-1}
      {...naming}
      aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
      onKeyDown={onKeyDown}
      onBlur={(e) => {
        if (!rootRef.current?.contains(e.relatedTarget)) closeMenu({ refocus: false });
      }}
      className={cx(
        "zk-pop-in rounded-xl border p-1 shadow-xl outline-none",
        // overscroll-contain so reaching the end of the list does not start scrolling the
        // page underneath it; overflow-x-hidden because a long name truncates rather than
        // widening the menu.
        "overflow-y-auto overflow-x-hidden overscroll-contain",
        field
          ? cx(
              // Above the dialog's overlay layer (z.overlay), below toasts. Painted dark on its
              // very first frame: it is page DOM, so `dark:` applies from the start.
              z.modal,
              "border-slate-200 bg-white shadow-slate-900/10 ring-1 ring-black/[0.03]",
              "dark:border-slate-700 dark:bg-slate-800 dark:shadow-black/60 dark:ring-white/5"
            )
          : cx(
              "absolute z-40",
              "border-slate-200 bg-white shadow-xl shadow-slate-900/15 ring-1 ring-slate-900/5",
              "dark:border-white/10 dark:bg-neutral-950 dark:shadow-black/60 dark:ring-white/10"
            ),
        "zk-scroll-thin",
        !field && (place.up ? "bottom-full mb-1.5" : "top-full mt-1.5"),
        !field && (align === "right" ? "right-0" : "left-0"),
        !field && width,
        menuClassName
      )}
      style={
        field
          ? {
              position: "fixed",
              left: place.left,
              width: place.width,
              ...(place.up ? { bottom: place.bottom } : { top: place.top }),
              maxHeight: place.maxHeight,
            }
          : { maxHeight: place.maxHeight }
      }
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
              "flex cursor-pointer items-center gap-2.5 rounded-lg px-2.5 py-2",
              field || size === "lg" ? "text-sm" : "text-[13px]",
              "transition-colors duration-150 motion-reduce:transition-none",
              opt.disabled && "pointer-events-none opacity-50",
              field
                ? fieldOptionTone(isSelected, active === i)
                : cx(
                    isSelected
                      ? active === i
                        ? "bg-violet-100 text-violet-950 dark:bg-violet-500/25 dark:text-violet-50"
                        : "bg-violet-50/70 text-violet-900 dark:bg-violet-500/15 dark:text-violet-100"
                      : active === i
                        ? "bg-slate-100 text-slate-900 dark:bg-white/[0.08] dark:text-white"
                        : CONSOLE.body,
                    isSelected && "font-semibold"
                  )
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
  );

  return (
    <div ref={rootRef} className={cx("relative", open && "z-30", className)}>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={onTriggerKeyDown}
        disabled={disabled}
        title={title}
        role={field ? "combobox" : undefined}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={field && open ? listId : undefined}
        {...naming}
        className={cx(
          field
            ? cx(CONSOLE_FIELD, "flex cursor-pointer items-center gap-2 text-left")
            : cx(
                "flex w-full items-center gap-2 border px-3 text-left font-medium",
                SIZES[size] || SIZES.md,
                "transition-colors duration-150 motion-reduce:transition-none",
                CONSOLE.control
              ),
          "disabled:cursor-not-allowed disabled:opacity-50",
          open && "border-violet-400 dark:border-violet-500/50",
          !field && focusRing,
          triggerClassName
        )}
      >
        {Icon && <Icon className={cx("shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />}
        {selected?.dot}
        <span className={cx("min-w-0 flex-1 truncate", selected ? "text-slate-800 dark:text-slate-100" : CONSOLE.faint)}>
          {selected ? selected.label : placeholder}
        </span>
        <FiChevronDown
          className={cx(
            "shrink-0 text-[14px] transition-transform duration-150 motion-reduce:transition-none",
            // The native select's chevron colour, for a field.
            field ? "text-slate-400" : CONSOLE.faint,
            open && "rotate-180"
          )}
          aria-hidden="true"
        />
      </button>

      {open && (field ? createPortal(menu, document.body) : menu)}
    </div>
  );
}
