import { cx } from "./tokens";
import { Reveal } from "./motion";

// Numbered timeline: vertical on mobile, horizontal on large screens.
// `items`: [{ icon, title, body }]. Column count adapts to items via --cols
// (see .zk-timeline in index.css) so any length works.
export default function Timeline({ items, className = "" }) {
  return (
    <div className={cx("relative", className)}>
      {/* Connecting line */}
      <div className="absolute left-6 top-0 h-full w-0.5 bg-gradient-to-b from-emerald-200 via-emerald-300 to-indigo-200 lg:left-0 lg:top-7 lg:h-0.5 lg:w-full lg:bg-gradient-to-r dark:from-emerald-500/40 dark:via-emerald-500/30 dark:to-indigo-500/40" />

      <ol className="zk-timeline relative grid gap-8 lg:gap-4" style={{ "--cols": items.length }}>
        {items.map((s, i) => (
          <li key={s.title} className="relative pl-16 lg:pl-0">
            <Reveal delay={i * 120}>
              <span className="absolute left-0 top-0 grid h-12 w-12 place-items-center rounded-2xl border border-emerald-200 bg-white text-emerald-600 shadow-sm transition-transform duration-300 hover:scale-110 lg:relative lg:mb-4 dark:border-emerald-500/30 dark:bg-slate-900 dark:text-emerald-400">
                {s.icon && <s.icon className="text-xl" />}
                <span className="absolute -right-1 -top-1 grid h-5 w-5 place-items-center rounded-full bg-emerald-600 text-[10px] font-bold text-white ring-4 ring-white dark:ring-slate-950">
                  {i + 1}
                </span>
              </span>
              <h3 className="font-semibold text-slate-900 dark:text-white">{s.title}</h3>
              <p className="mt-1 text-sm leading-relaxed text-slate-600 dark:text-slate-400">{s.body}</p>
            </Reveal>
          </li>
        ))}
      </ol>
    </div>
  );
}
