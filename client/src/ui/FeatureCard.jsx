import { FiArrowRight } from "react-icons/fi";
import { cx, ACCENT } from "./tokens";
import Card from "./Card";

// Icon + title + body card, optional bullet list and CTA link.
// Consolidates the homepage pillar / resource / proof / pathway cards.
export default function FeatureCard({
  icon: Icon,
  title,
  body,
  accent = "emerald",
  points,
  cta,
  href,
  className = "",
}) {
  const a = ACCENT[accent];
  return (
    <Card
      href={href}
      hover
      padding={points ? "xl" : "lg"}
      className={cx("group flex h-full flex-col rounded-3xl", href && a.hoverBorder, className)}
    >
      {Icon && (
        <span className={cx("grid h-12 w-12 place-items-center rounded-2xl transition-transform duration-300 group-hover:scale-110", a.chip)}>
          <Icon className="text-2xl" />
        </span>
      )}
      <h3 className="mt-5 text-lg font-bold text-slate-900 dark:text-white">{title}</h3>
      <p className="mt-2 flex-1 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{body}</p>

      {points && (
        <ul className="mt-5 space-y-2">
          {points.map((p) => (
            <li key={p} className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
              <svg className="shrink-0 text-emerald-500" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3"><path d="M20 6 9 17l-5-5" /></svg>
              {p}
            </li>
          ))}
        </ul>
      )}

      {cta && (
        <span className={cx("mt-6 inline-flex items-center gap-1.5 text-sm font-semibold", a.text)}>
          {cta} <FiArrowRight className="transition group-hover:translate-x-1" />
        </span>
      )}
    </Card>
  );
}
