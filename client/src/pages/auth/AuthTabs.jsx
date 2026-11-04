import { NavLink, useLocation } from "react-router-dom";
import { cx } from "../../ui/tokens";

// Login / Register / Reset Password across the top of the sign-in card (ZoikoSema reference:
// a soft strip, the active tab raised in white and joined to the card body, a short underline
// that slides between tabs). Each tab is the EXISTING route rendered as a <NavLink>, so
// navigation, back/forward and deep links work exactly as before.
//
// Deliberately not role="tablist": these navigate to separate pages rather than swapping
// panels in place, so links (with NavLink's aria-current="page") describe them correctly.
const TABS = [
  ["Login", "/login"],
  ["Register", "/signup"],
  ["Reset Password", "/forgot-password"],
];

export default function AuthTabs({ className = "" }) {
  const { pathname } = useLocation();
  const found = TABS.findIndex(([, to]) => pathname.startsWith(to));
  const activeIndex = found === -1 ? 0 : found;

  return (
    <nav
      aria-label="Authentication"
      className={cx(
        "relative grid shrink-0 grid-cols-3 gap-1 border-b border-slate-200/80 bg-slate-100/80 px-2 pt-2 dark:border-white/10 dark:bg-white/[0.04]",
        className
      )}
    >
      {TABS.map(([label, to]) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            cx(
              "-mb-px rounded-t-[18px] px-1 pb-[calc(var(--auth-s1)+4px)] pt-[var(--auth-s1)] whitespace-nowrap text-center text-[13px] transition-colors sm:text-[14.5px] duration-200 motion-reduce:transition-none",
              isActive
                ? "bg-white font-semibold text-slate-950 dark:bg-[#0d1020] dark:text-white"
                : "font-medium text-slate-500 hover:bg-white/60 hover:text-slate-900 dark:text-slate-400 dark:hover:bg-white/5 dark:hover:text-white"
            )
          }
        >
          {label}
        </NavLink>
      ))}

      {/* One short underline that slides between tabs — the movement is what tells you the
          page swapped. Each slot is exactly one third of the padded strip. */}
      <span
        aria-hidden="true"
        style={{ transform: `translateX(calc(${activeIndex} * (100% + 0.25rem)))` }}
        className="pointer-events-none absolute bottom-[3px] left-2 flex w-[calc((100%-1rem-0.5rem)/3)] justify-center transition-transform duration-300 ease-out motion-reduce:transition-none"
      >
        <span className="h-[3px] w-[52%] rounded-full bg-gradient-to-r from-violet-600 to-blue-500" />
      </span>
    </nav>
  );
}
