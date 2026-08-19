import { NavLink, useLocation } from "react-router-dom";
import { cx } from "../../ui/tokens";

// Sign In / Create Organization tab strip at the top of the auth card. Each tab is the
// EXISTING route rendered as a <NavLink>, so navigation, back/forward and deep links keep
// working exactly as before — the strip is presentation over the router, not new state.
//
// Deliberately not role="tablist": these navigate to separate pages rather than swapping
// panels in place, so links (with NavLink's aria-current) describe them correctly.
const TABS = [
  ["Sign In", "/login"],
  ["Create Organization", "/signup"],
];

// Sits flush with the card edge, so it cancels <Card padding="xl"> (p-7 sm:p-8).
export default function AuthTabs({ className = "" }) {
  const { pathname } = useLocation();
  // One underline that slides between the two tabs, rather than one per tab fading in and
  // out — the movement is what tells you the card swapped.
  const activeIndex = pathname.startsWith("/signup") ? 1 : 0;

  return (
    <nav
      aria-label="Authentication"
      className={cx(
        "relative -mx-7 -mt-7 mb-7 grid grid-cols-2 border-b border-slate-200 sm:-mx-8 sm:-mt-8 dark:border-slate-800",
        className
      )}
    >
      {TABS.map(([label, to]) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            cx(
              "py-4 text-center text-sm transition-colors duration-200 first:rounded-tl-[20px] last:rounded-tr-[20px] motion-reduce:transition-none",
              isActive
                ? "font-semibold text-violet-700 dark:text-violet-400"
                : "font-medium text-slate-500 hover:bg-slate-50 hover:text-slate-800 dark:text-slate-400 dark:hover:bg-white/5 dark:hover:text-slate-200"
            )
          }
        >
          {label}
        </NavLink>
      ))}

      <span
        aria-hidden="true"
        style={{ transform: `translateX(${activeIndex * 100}%)` }}
        className="absolute -bottom-px left-0 h-0.5 w-1/2 rounded-full bg-gradient-to-r from-violet-600 to-fuchsia-500 transition-transform duration-300 ease-out motion-reduce:transition-none"
      />
    </nav>
  );
}
