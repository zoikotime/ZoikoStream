import { FiMoon, FiSun } from "react-icons/fi";
import { cx, focusRing } from "./tokens";
import { useTheme } from "../theme/ThemeContext";

// Light/dark switch as a two-position track with a sliding indicator, replacing the
// single icon button that gave no hint about which state you were in or what you were
// switching to. Both icons stay visible; the knob says which one is active.
//
// The theme logic itself is untouched — this calls the same `toggle()` from ThemeContext
// that the icon button called, and derives its position from the same `theme` value.
export default function ThemeToggle({ className = "" }) {
  const { theme, toggle } = useTheme();
  const dark = theme === "dark";

  return (
    <button
      type="button"
      role="switch"
      aria-checked={dark}
      onClick={toggle}
      aria-label={dark ? "Switch to light theme" : "Switch to dark theme"}
      title={dark ? "Switch to light theme" : "Switch to dark theme"}
      className={cx(
        "relative inline-flex h-9 w-[62px] shrink-0 items-center rounded-full border p-1",
        "border-slate-200 bg-slate-100/80 dark:border-white/10 dark:bg-white/[0.06]",
        "transition-colors duration-200 motion-reduce:transition-none",
        focusRing,
        className
      )}
    >
      {/* Sliding indicator. Transform-only so it animates on the compositor. */}
      <span
        aria-hidden="true"
        className={cx(
          "absolute left-1 top-1 h-7 w-7 rounded-full bg-white shadow-sm ring-1 ring-slate-900/5",
          "transition-transform duration-200 ease-out motion-reduce:transition-none",
          "dark:bg-gradient-to-br dark:from-violet-500 dark:to-indigo-600 dark:ring-0",
          dark ? "translate-x-[26px]" : "translate-x-0"
        )}
      />
      <span className="relative z-10 grid h-7 w-7 place-items-center">
        <FiSun
          className={cx(
            "text-[15px] transition-colors duration-200 motion-reduce:transition-none",
            dark ? "text-slate-500" : "text-amber-500"
          )}
          aria-hidden="true"
        />
      </span>
      <span className="relative z-10 grid h-7 w-7 place-items-center">
        <FiMoon
          className={cx(
            "text-[15px] transition-colors duration-200 motion-reduce:transition-none",
            dark ? "text-white" : "text-slate-400"
          )}
          aria-hidden="true"
        />
      </span>
    </button>
  );
}
