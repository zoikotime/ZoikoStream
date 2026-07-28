import { Link } from "react-router-dom";
import { cx } from "./tokens";

// The canonical surface. Every card in the app is a <Card> so the border/bg/radius/
// shadow live in exactly one place.
const PADDING = { none: "", sm: "p-4", md: "p-5", lg: "p-6", xl: "p-7 sm:p-8" };

const VARIANTS = {
  base: "border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900",
  subtle: "border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/60",
  glass: "zk-glass border border-white/15 dark:border-white/10",
  dark: "border border-white/10 bg-white/5 backdrop-blur-md",
};

const HOVER = "transition duration-300 hover:-translate-y-1 hover:shadow-lg dark:hover:shadow-black/40";

export default function Card({
  as,
  href,
  variant = "base",
  padding = "lg",
  hover = false,
  className = "",
  children,
  ...rest
}) {
  const cls = cx("rounded-2xl", VARIANTS[variant], PADDING[padding], hover && HOVER, className);
  if (href && href.startsWith("/")) return <Link to={href} className={cls} {...rest}>{children}</Link>;
  if (href) return <a href={href} className={cls} {...rest}>{children}</a>;
  const Tag = as || "div";
  return <Tag className={cls} {...rest}>{children}</Tag>;
}
