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
export default function StatRow({ label, value, tone, mono, reason, className = "" }) {
  const missing = value == null;
  return (
    <div className={cx("flex items-baseline justify-between gap-4 py-[7px]", className)}>
      <p className={cx("min-w-0 text-[13px]", CONSOLE.body)}>{label}</p>
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
