import { Link } from "react-router-dom";
import { cx, focusRing } from "./tokens";

// Full state coverage: hover, active (press scale), focus-visible ring, disabled, loading.
// Flat fintech look — no hover-lift, no gradient. Polymorphic: internal href -> <Link>,
// external -> <a>, else <button>.
const BASE = cx(
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium",
  focusRing,
  "transition duration-150 ease-out active:scale-[0.98]",
  "disabled:pointer-events-none disabled:opacity-50",
  "motion-reduce:transition-none motion-reduce:active:scale-100"
);

const SIZES = {
  sm: "h-8 px-3 text-[13px]",
  md: "h-9 px-3.5 text-sm",
  lg: "h-10 px-4 text-sm",
};

const VARIANTS = {
  primary: "bg-violet-600 text-white hover:bg-violet-700 active:bg-violet-800",
  secondary:
    "border border-slate-200 bg-white text-slate-700 hover:border-slate-300 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800",
  ghost: "text-slate-600 hover:bg-slate-100 hover:text-slate-900 dark:text-slate-300 dark:hover:bg-slate-800 dark:hover:text-white",
  danger: "bg-rose-600 text-white hover:bg-rose-700 active:bg-rose-800",
};

function Spinner() {
  return (
    <span
      className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none"
      aria-hidden="true"
    />
  );
}

export default function Button({
  variant = "primary",
  size = "md",
  href,
  loading = false,
  disabled = false,
  leftIcon: Left,
  rightIcon: Right,
  iconOnly = false,
  className = "",
  children,
  ...rest
}) {
  const cls = cx(BASE, iconOnly ? SIZES[size].replace(/px-[\d.]+/, "w-9 px-0") : SIZES[size], VARIANTS[variant], className);
  const inner = (
    <>
      {loading ? <Spinner /> : Left && <Left className="text-base" aria-hidden="true" />}
      {children}
      {!loading && Right && <Right className="text-base" aria-hidden="true" />}
    </>
  );

  if (href && !disabled && href.startsWith("/")) return <Link to={href} className={cls} {...rest}>{inner}</Link>;
  if (href && !disabled) return <a href={href} className={cls} {...rest}>{inner}</a>;
  return (
    <button type="button" className={cls} disabled={disabled || loading} aria-busy={loading || undefined} {...rest}>
      {inner}
    </button>
  );
}
