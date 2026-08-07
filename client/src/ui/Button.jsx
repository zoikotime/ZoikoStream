import { Link } from "react-router-dom";
import { cx, focusRing } from "./tokens";

// ONE Button for the whole app, two appearances so both surfaces keep their exact look:
//   appearance="marketing" (default) — emerald, rounded-xl, hover-lift (homepage + public/org marketing).
//   appearance="console"            — flat violet, rounded-lg, no lift; supports loading + icons
//                                     (dashboards). Import { ConsoleButton } for the console default.
// Polymorphic in both: internal href -> <Link>, external href -> <a>, else <button>.

// ── marketing appearance ─────────────────────────────────────────────────────
const M_BASE =
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-xl font-semibold transition-all duration-200 will-change-transform hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-emerald-500 disabled:pointer-events-none disabled:opacity-60 motion-reduce:hover:translate-y-0 motion-reduce:active:scale-100";
const M_SIZES = { sm: "px-3.5 py-2 text-sm", md: "px-5 py-3 text-sm", lg: "px-6 py-3.5 text-base" };
const M_VARIANTS = {
  primary: "bg-emerald-600 text-white shadow-sm shadow-emerald-600/20 hover:bg-emerald-500 hover:shadow-lg hover:shadow-emerald-600/25 dark:focus-visible:ring-offset-slate-950",
  secondary: "bg-white text-slate-800 border border-slate-200 hover:border-slate-300 hover:bg-slate-50 hover:shadow-sm dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700 dark:hover:bg-slate-800 dark:focus-visible:ring-offset-slate-950",
  ghost: "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-white/10",
  dark: "bg-slate-900 text-white hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-100",
  outlineLight: "border border-white/25 text-white hover:bg-white/10 hover:border-white/40 focus-visible:ring-offset-slate-950",
  danger: "bg-rose-600 text-white shadow-sm hover:bg-rose-500",
};

// ── console appearance (was components/admin/Button) ─────────────────────────
const C_BASE = cx(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium",
  focusRing,
  "transition duration-150 ease-out active:scale-[0.98]",
  "disabled:pointer-events-none disabled:opacity-50",
  "motion-reduce:transition-none motion-reduce:active:scale-100"
);
const C_SIZES = { sm: "h-8 px-3 text-[13px]", md: "h-9 px-3.5 text-sm", lg: "h-10 px-4 text-sm" };
// Every variant gains a hover shadow so a console button reads as a raised control rather than a
// bordered label — the secondary variant in particular was near-invisible on a white filter row.
const C_VARIANTS = {
  primary: "bg-violet-600 text-white shadow-sm hover:bg-violet-700 hover:shadow-md active:bg-violet-800 active:shadow-sm",
  secondary:
    "border border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50 hover:shadow-sm dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:border-slate-600 dark:hover:bg-slate-800",
  ghost:
    "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-white/10 dark:hover:text-white",
  danger: "bg-rose-600 text-white shadow-sm hover:bg-rose-700 hover:shadow-md active:bg-rose-800 active:shadow-sm",
};

function ConsoleSpinner() {
  return (
    <span
      className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none"
      aria-hidden="true"
    />
  );
}

export default function Button({
  appearance = "marketing",
  variant = "primary",
  size = "md",
  href,
  onClick,
  className = "",
  children,
  // console-only:
  loading = false,
  disabled = false,
  leftIcon: Left,
  rightIcon: Right,
  iconOnly = false,
  ...rest
}) {
  if (appearance === "console") {
    const cls = cx(
      C_BASE,
      iconOnly ? C_SIZES[size].replace(/px-[\d.]+/, "w-9 px-0") : C_SIZES[size],
      C_VARIANTS[variant],
      className
    );
    const inner = (
      <>
        {loading ? <ConsoleSpinner /> : Left && <Left className="text-base" aria-hidden="true" />}
        {children}
        {!loading && Right && <Right className="text-base" aria-hidden="true" />}
      </>
    );
    if (href && !disabled && href.startsWith("/")) return <Link to={href} onClick={onClick} className={cls} {...rest}>{inner}</Link>;
    if (href && !disabled) return <a href={href} onClick={onClick} className={cls} {...rest}>{inner}</a>;
    return (
      <button type="button" onClick={onClick} className={cls} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
        {inner}
      </button>
    );
  }

  const cls = cx(M_BASE, M_SIZES[size], M_VARIANTS[variant], className);
  if (href && href.startsWith("/")) return <Link to={href} className={cls} {...rest}>{children}</Link>;
  if (href) return <a href={href} className={cls} {...rest}>{children}</a>;
  return <button type="button" onClick={onClick} className={cls} {...rest}>{children}</button>;
}

// Console default — import this in dashboard code so callers don't repeat appearance="console".
export const ConsoleButton = (props) => <Button appearance="console" {...props} />;
