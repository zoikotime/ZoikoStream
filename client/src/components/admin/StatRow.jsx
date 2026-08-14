import { CONSOLE, cx, type } from "../../ui/tokens";

// Label-left / value-right row, the console's unit for a small posture panel.
//
// Extracted because DeveloperPlatform, DeveloperOps and SecuritySupport each had their own
// copy, and the eleven pages added since needed a twelfth. The em-dash convention lives here
// now: a null value renders "—" in the faint tone with `reason` on hover, never a 0 that
// would read as a measured clean result.
//
//   <StatRow label="Active credentials" value={4} />
//   <StatRow label="Rate-limit events" value={null} reason="Needs request telemetry" />
//   <StatRow label="Expiring" value={2} dot="warning" />   // leading status indicator
//
// `dot` (opt-in) adds a leading indicator so a posture list scans as states rather than as a
// column of numbers. Literal green/amber/rose — index.css remaps emerald→violet, so the brand
// scales cannot express health here. `separated` draws a hairline above the row, letting a
// caller build a divided list without wrapping every row.
const DOT = {
  ok: "bg-green-500",
  success: "bg-green-500",
  warning: "bg-amber-500",
  danger: "bg-rose-500",
  info: "bg-blue-500",
  brand: "bg-violet-500",
  neutral: "bg-slate-300 dark:bg-neutral-600",
};

export default function StatRow({
  label,
  value,
  tone,
  mono,
  reason,
  dot,
  separated = false,
  className = "",
}) {
  const missing = value == null;
  return (
    <div
      className={cx(
        "group flex items-baseline justify-between gap-4 py-[7px]",
        separated && cx("border-t pt-2.5 first:border-t-0 first:pt-[7px]", CONSOLE.divider),
        // Hover is a text lift only: these rows are readouts, not controls, so a fill or a
        // border would imply they can be clicked.
        "transition-colors duration-150 motion-reduce:transition-none",
        className
      )}
    >
      <p className={cx("flex min-w-0 items-baseline gap-2 text-[13px]", CONSOLE.body)}>
        {dot && (
          <span
            aria-hidden="true"
            className={cx("mt-[1px] h-1.5 w-1.5 shrink-0 rounded-full", DOT[dot] || DOT.neutral)}
          />
        )}
        <span className="min-w-0">{label}</span>
      </p>
      <span
        className={cx(
          "shrink-0 text-[13px] font-semibold tabular-nums",
          mono && type.mono,
          missing ? CONSOLE.faint : tone || CONSOLE.heading
        )}
        title={missing ? reason : undefined}
      >
        {missing ? "—" : value}
      </span>
    </div>
  );
}
