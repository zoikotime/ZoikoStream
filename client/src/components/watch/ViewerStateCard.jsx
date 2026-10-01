// client/src/components/watch/ViewerStateCard.jsx
// The one shape every non-playing viewer state takes in the player slot (pre-event, capacity
// waiting, access window closed): a recognisable icon AND a short heading AND one short
// sentence. ZST-SPEC-VAP-001 §6.4: icon plus text for each state, nothing that depends on
// colour alone, and no critical step that needs more than one short sentence of reading.
//
// role="status" + aria-live="polite": when the page moves from one state to another (the
// broadcast starts, a seat frees up) a screen reader announces the new state without the
// viewer having to go looking for it.
import { useId } from "react";

export default function ViewerStateCard({ icon: Icon, tone = "neutral", title, children, actions, testId }) {
  const headingId = useId();
  const iconTone = {
    neutral: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-300",
    waiting: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400",
    attention: "bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-300",
  }[tone];
  return (
    <section
      role="status"
      aria-live="polite"
      aria-labelledby={headingId}
      data-testid={testId}
      className="flex min-h-[16rem] w-full flex-col items-center justify-center gap-3 rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900 sm:aspect-video sm:p-10"
    >
      {Icon && (
        <span className={`grid h-14 w-14 place-items-center rounded-full ${iconTone}`} aria-hidden="true">
          <Icon className="text-2xl" />
        </span>
      )}
      <h2 id={headingId} className="text-lg font-semibold text-slate-900 dark:text-white">{title}</h2>
      <div className="max-w-md space-y-1.5 text-sm text-slate-600 dark:text-slate-300">{children}</div>
      {actions && <div className="mt-1 flex flex-wrap items-center justify-center gap-2">{actions}</div>}
    </section>
  );
}

// A 48×48-minimum action for these cards (and the error page), with a visible focus ring.
export const STATE_ACTION =
  "inline-flex min-h-12 min-w-12 items-center justify-center gap-2 rounded-xl px-4 text-sm font-semibold transition " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500 focus-visible:ring-offset-2 " +
  "dark:focus-visible:ring-offset-slate-900";
