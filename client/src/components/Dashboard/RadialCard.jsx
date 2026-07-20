// Lib-free circular progress gauge (SVG). ponytail: an SVG donut beats pulling a chart lib for one ring.
export default function RadialCard({ title, percent, label, footer, color = "#7c3aed" }) {
  const r = 54;
  const circ = 2 * Math.PI * r;
  const offset = circ * (1 - percent / 100);

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <h2 className="mb-2 font-semibold text-slate-900 dark:text-white">{title}</h2>

      <div className="relative mx-auto grid h-40 w-40 place-items-center">
        <svg width="160" height="160" viewBox="0 0 160 160" className="-rotate-90">
          <circle
            cx="80"
            cy="80"
            r={r}
            fill="none"
            strokeWidth="14"
            className="stroke-slate-100 dark:stroke-neutral-800"
          />
          <circle
            cx="80"
            cy="80"
            r={r}
            fill="none"
            stroke={color}
            strokeWidth="14"
            strokeLinecap="round"
            strokeDasharray={circ}
            strokeDashoffset={offset}
          />
        </svg>
        <div className="absolute text-center">
          <p className="text-3xl font-bold text-slate-900 dark:text-white">{percent}%</p>
          <p className="text-xs text-slate-500 dark:text-neutral-400">{label}</p>
        </div>
      </div>

      {footer && (
        <p className="mt-2 text-center text-sm text-slate-500 dark:text-neutral-400">{footer}</p>
      )}
    </div>
  );
}
