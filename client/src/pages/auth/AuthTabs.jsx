import { NavLink, useLocation } from "react-router-dom";
import { cx } from "../../ui/tokens";

// Login / Register / Reset Password across the top of the sign-in card, as in the approved
// login design: the active tab is a white slot joined to the card with its label in violet
// and a thick violet bar beneath it; the others are plain labels on the card's tinted edge,
// with a hairline between two neighbouring inactive tabs. Slot widths are the design's
// (193 : 180 : 219 of the 592-wide card). Sizes use --u (index.css, auth tokens).
//
// Each tab is the EXISTING route rendered as a <NavLink>, so navigation, back/forward and
// deep links work exactly as before. Deliberately not role="tablist": these navigate to
// separate pages rather than swapping panels in place, so links (with NavLink's
// aria-current="page") describe them correctly.
const TABS = [
  ["Login", "/login", "rounded-tl-[calc(24*var(--u))] pl-[4.2%]"],
  ["Register", "/signup", "rounded-t-[calc(16*var(--u))]"],
  ["Reset Password", "/forgot-password", "rounded-tr-[calc(24*var(--u))] pr-[2.4%]"],
];

export default function AuthTabs({ className = "" }) {
  const { pathname } = useLocation();
  const found = TABS.findIndex(([, to]) => pathname.startsWith(to));
  const activeIndex = found === -1 ? 0 : found;

  return (
    <nav
      aria-label="Authentication"
      className={cx("grid h-[max(52px,calc(63*var(--u)))] shrink-0 grid-cols-[193fr_180fr_219fr]", className)}
    >
      {TABS.map(([label, to, edge], i) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            cx(
              "relative flex items-center justify-center whitespace-nowrap transition-colors duration-200 focus-visible:z-10 motion-reduce:transition-none",
              edge,
              // Hairline between two neighbouring tabs that are both inactive.
              i > 0 && i !== activeIndex && i - 1 !== activeIndex &&
                "before:absolute before:left-0 before:top-1/2 before:h-[calc(28*var(--u))] before:w-px before:-translate-y-1/2 before:bg-slate-200 dark:before:bg-white/10",
              isActive
                ? "bg-white text-[length:max(13.5px,calc(18*var(--u)))] font-semibold text-[#5b37e1] dark:bg-[#0d1020] dark:text-violet-300"
                : "text-[length:max(13px,calc(17*var(--u)))] font-medium text-[#1b1c33] hover:text-[#5b37e1] dark:text-slate-300 dark:hover:text-white"
            )
          }
        >
          {({ isActive }) => (
            <span className="relative">
              {label}
              {isActive && (
                <span
                  aria-hidden="true"
                  className="absolute left-1/2 top-[calc(100%_+_10*var(--u))] h-[max(4px,calc(5*var(--u)))] w-[min(calc(150*var(--u)),26vw)] -translate-x-1/2 rounded-t-full bg-[#5b37e1] dark:bg-violet-400"
                />
              )}
            </span>
          )}
        </NavLink>
      ))}
    </nav>
  );
}
