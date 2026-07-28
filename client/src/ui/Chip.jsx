import { cx } from "./tokens";

// Selectable / filter chip. `active` toggles the emerald selected state.
// Renders a <button> when onClick is given, else a static <span> (tag).
export default function Chip({ active = false, onClick, icon: Icon, className = "", children, ...rest }) {
  const cls = cx(
    "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition",
    active
      ? "bg-emerald-600 text-white"
      : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700",
    className
  );
  const content = (
    <>
      {Icon && <Icon className="text-sm" />}
      {children}
    </>
  );
  return onClick ? (
    <button type="button" onClick={onClick} aria-pressed={active} className={cls} {...rest}>{content}</button>
  ) : (
    <span className={cls} {...rest}>{content}</span>
  );
}
