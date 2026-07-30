import { CONSOLE, STAGE_COLOR, cx, type } from "../../../ui/tokens";
import { Sparkline } from "../../../ui/charts";

// The media lifecycle, left to right, as a broadcast actually travels it. Each node's
// colour is fixed per stage; the connecting segment is a gradient between neighbours so
// the rail reads as one pipeline rather than eight separate chips.
//
// The percentage under each stage is measured availability over the selected window — the
// worst region for that stage, because the console's job is to surface the users having a
// bad time, not the average. `status` comes from real service probes, so an unintegrated
// dependency shows a hollow node instead of a green tick.
const RING = {
  ok: "ring-2",
  warn: "ring-2 ring-offset-2",
  down: "ring-2 ring-offset-2 animate-pulse motion-reduce:animate-none",
  not_configured: "ring-1 opacity-50",
};

// `eyebrow` labels the rail (the org console names which services are in scope).
// `dimUnused` fades stages whose `in_use` is false — an org that never records should not
// be shown a confident tick for Preserve.
export default function LifecycleRail({ stages = [], trend = [], eyebrow, dimUnused = false }) {
  if (!stages.length) return null;

  return (
    <section className={cx(CONSOLE.panel, "overflow-hidden")}>
      {eyebrow && (
        <p
          className={cx(
            "px-4 pt-3.5 text-[10px] font-semibold uppercase tracking-[0.14em] sm:px-6",
            type.mono,
            CONSOLE.faint
          )}
        >
          {eyebrow}
        </p>
      )}

      {/* Audience trend across the same window, sitting above the rail it explains. */}
      {trend.length > 1 && (
        <div className="px-1 pt-1" aria-hidden="true">
          <Sparkline data={trend} color="#a3a3a3" height={56} />
        </div>
      )}

      <div className="overflow-x-auto">
        <ol
          className="grid min-w-[46rem] gap-0 px-4 pb-5 pt-4 sm:px-6"
          style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0, 1fr))` }}
        >
          {stages.map((s, i) => {
            const color = STAGE_COLOR[s.stage] || "#a3a3a3";
            const next = stages[i + 1];
            // Unused stages recede but stay in place, so the pipeline still reads as one
            // sequence and the gaps are legible as "not used here".
            const unused = dimUnused && s.in_use === false;
            return (
              <li
                key={s.stage}
                className={cx(
                  "relative flex flex-col items-center transition-opacity",
                  unused && "opacity-35"
                )}
                title={unused ? `${s.label} — not used by this organization` : undefined}
              >
                {/* Connector to the next node — drawn from this cell so it never overflows
                    the last one. */}
                {next && (
                  <span
                    aria-hidden="true"
                    className="absolute left-1/2 top-[7px] h-[2px] w-full"
                    style={{
                      background: `linear-gradient(90deg, ${color}, ${STAGE_COLOR[next.stage] || "#a3a3a3"})`,
                      opacity: 0.55,
                    }}
                  />
                )}
                <span
                  className={cx("relative z-10 h-4 w-4 rounded-full", RING[s.status] || RING.not_configured)}
                  style={{
                    backgroundColor: s.status === "not_configured" ? "transparent" : color,
                    // Tailwind can't express a per-stage ring colour, and the ring is the
                    // stage identity — so it is set inline alongside the fill.
                    "--tw-ring-color": color,
                    boxShadow: s.status === "down" ? `0 0 0 4px ${color}33` : undefined,
                  }}
                  title={`${s.label} — ${s.status.replace("_", " ")}`}
                />
                <p className={cx("mt-2.5 text-center text-[12px] font-medium", CONSOLE.body)}>{s.label}</p>
                <p className={cx("mt-0.5 text-center text-[11px]", type.mono, CONSOLE.faint)}>
                  {unused ? "not in use" : s.availability == null ? "—" : `${s.availability.toFixed(2)}%`}
                </p>
                {s.open_incidents > 0 && (
                  <p className="mt-0.5 text-[10px] font-semibold text-rose-500">
                    {s.open_incidents} open
                  </p>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </section>
  );
}
