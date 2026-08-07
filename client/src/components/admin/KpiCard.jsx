import { CONSOLE, cx, focusRing, panelSurface, t150, type } from "../../ui/tokens";

// Clickable KPI tile for the operational strips on Event Readiness and Media.
//
// Same skin as StatCard — which is a static div with a count-up and a sparkline — plus the
// two things an operational workload strip needs and StatCard has no slot for: a scope/rule
// note under the figure (so "Passed" can say it can drop the moment evidence expires), and a
// press that FILTERS the table below rather than navigating away.
//
// `pressed` renders the engaged state, so the strip and the table below it can never
// disagree about which slice the operator is looking at.
export default function KpiCard({ label, value, note, tone, pressed = false, onClick }) {
  const Tag = onClick ? "button" : "div";
  return (
    <Tag
      {...(onClick ? { type: "button", onClick, "aria-pressed": pressed } : {})}
      className={cx(
        panelSurface,
        t150,
        focusRing,
        "group block w-full p-4 text-left",
        // A clickable card gets a real press affordance: the border lifts, a soft shadow appears,
        // and the surface tints. The old single border-hover was invisible in light mode, so a
        // strip of filters read as six static figures.
        onClick &&
          cx(
            "cursor-pointer hover:border-violet-300 hover:bg-violet-50/40 hover:shadow-sm",
            "active:scale-[0.99] motion-reduce:active:scale-100",
            "dark:hover:border-violet-500/40 dark:hover:bg-violet-500/[0.06]"
          ),
        pressed &&
          "border-violet-400 bg-violet-50/60 ring-1 ring-violet-400 dark:border-violet-500/60 dark:bg-violet-500/[0.08] dark:ring-violet-500/40"
      )}
    >
      <p className={cx(type.label, "font-medium", CONSOLE.muted)}>{label}</p>
      <p className={cx(type.stat, "mt-1", tone || CONSOLE.heading)}>{value}</p>
      {note && <p className={cx("mt-1.5 text-[11px] leading-snug", CONSOLE.faint)}>{note}</p>}
      {/* The filter state is stated in words, not only by the ring — a colour-only "on" state
          fails the non-color-indicator rule every one of these specs carries. */}
      {onClick && (
        <p
          className={cx(
            "mt-2 text-[11px] font-semibold",
            pressed ? "text-violet-700 dark:text-violet-300" : cx(CONSOLE.faint, "opacity-0 transition-opacity group-hover:opacity-100 motion-reduce:transition-none")
          )}
        >
          {pressed ? "Filtering · click to clear" : "Click to filter"}
        </p>
      )}
    </Tag>
  );
}
