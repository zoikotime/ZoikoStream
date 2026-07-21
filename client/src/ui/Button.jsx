import { Link } from "react-router-dom";
import { cx } from "./tokens";

const BASE =
  "inline-flex shrink-0 items-center justify-center gap-2 whitespace-nowrap rounded-xl font-semibold transition-all duration-200 will-change-transform hover:-translate-y-0.5 active:translate-y-0 active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:ring-emerald-500 disabled:pointer-events-none disabled:opacity-60 motion-reduce:hover:translate-y-0 motion-reduce:active:scale-100";

const SIZES = { sm: "px-3.5 py-2 text-sm", md: "px-5 py-3 text-sm", lg: "px-6 py-3.5 text-base" };

const VARIANTS = {
  primary: "bg-emerald-600 text-white shadow-sm shadow-emerald-600/20 hover:bg-emerald-500 hover:shadow-lg hover:shadow-emerald-600/25 dark:focus-visible:ring-offset-slate-950",
  secondary: "bg-white text-slate-800 border border-slate-200 hover:border-slate-300 hover:bg-slate-50 hover:shadow-sm dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700 dark:hover:bg-slate-800 dark:focus-visible:ring-offset-slate-950",
  ghost: "text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-white/10",
  dark: "bg-slate-900 text-white hover:bg-slate-800 dark:bg-white dark:text-slate-900 dark:hover:bg-slate-100",
  outlineLight: "border border-white/25 text-white hover:bg-white/10 hover:border-white/40 focus-visible:ring-offset-slate-950",
  danger: "bg-rose-600 text-white shadow-sm hover:bg-rose-500",
};

// Polymorphic: internal path -> <Link>, hash/external -> <a>, else <button>.
export default function Button({ href, onClick, variant = "primary", size = "md", className = "", children, ...rest }) {
  const cls = cx(BASE, SIZES[size], VARIANTS[variant], className);
  if (href && href.startsWith("/")) return <Link to={href} className={cls} {...rest}>{children}</Link>;
  if (href) return <a href={href} className={cls} {...rest}>{children}</a>;
  return <button type="button" onClick={onClick} className={cls} {...rest}>{children}</button>;
}
