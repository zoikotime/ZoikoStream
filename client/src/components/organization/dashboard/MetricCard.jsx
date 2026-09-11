import { Link } from "react-router-dom";
import { cx, focusRing } from "../../../ui/tokens";

// A single headline figure for the dashboard's KPI row.
//
// Deliberately NOT components/admin/MetricTile: that tile is an operations widget — it
// carries a sparkline, a freshness age, a unit, a tone ramp and a footer link, which is the
// right density for the Super Admin console and the wrong density here. This one holds a
// label, a number, one line of context and an icon, and nothing else.
//
// Colour is decoration, never information: the tone only tints the surface and the icon
// chip. Anything the reader has to KNOW is in the text, so the card still reads correctly in
// greyscale or to someone who cannot separate violet from blue.
//
// Dark mode is not the same pastel dimmed: a 50-level tint over a near-black page turns to
// mud. Each dark surface is a low-alpha wash of the SAME hue with a faint hue-matched border,
// which keeps the four cards distinguishable from each other and from the page in both
// themes, while the figure on top stays at full contrast.
const TONES = {
  lavender: {
    surface: "border-violet-100 bg-violet-50 dark:border-violet-400/20 dark:bg-violet-500/[0.09]",
    chip: "bg-violet-100 text-violet-600 dark:bg-violet-500/20 dark:text-violet-300",
  },
  lilac: {
    surface: "border-purple-100 bg-purple-50 dark:border-purple-400/20 dark:bg-purple-500/[0.09]",
    chip: "bg-purple-100 text-purple-600 dark:bg-purple-500/20 dark:text-purple-300",
  },
  sky: {
    surface: "border-sky-100 bg-sky-50 dark:border-sky-400/20 dark:bg-sky-500/[0.09]",
    chip: "bg-sky-100 text-sky-600 dark:bg-sky-500/20 dark:text-sky-300",
  },
  cream: {
    surface: "border-amber-100 bg-amber-50 dark:border-amber-400/20 dark:bg-amber-500/[0.09]",
    chip: "bg-amber-100 text-amber-600 dark:bg-amber-500/20 dark:text-amber-300",
  },
};

export default function MetricCard({
  label,
  value,
  note,
  icon: Icon,
  tone = "lavender",
  to,
  live = false,
}) {
  const t = TONES[tone] || TONES.lavender;
  // `value == null` means the platform has no producer for this figure — never 0. The two
  // are different answers and the card must not blur them: 0 is a measurement, "—" is the
  // absence of one, and `note` carries the reason.
  const hasValue = value != null;

  const body = (
    <>
      <div className="flex items-start justify-between gap-3">
        <p className="text-[14px] font-medium text-slate-600 dark:text-neutral-300">{label}</p>
        {Icon && (
          <span
            aria-hidden="true"
            className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg", t.chip)}
          >
            <Icon className="text-[15px]" />
          </span>
        )}
      </div>

      <p
        className={cx(
          "mt-3 font-bold tracking-tight tabular-nums",
          hasValue
            ? "text-[32px] leading-none text-slate-900 dark:text-white"
            : "text-[26px] leading-none text-slate-400 dark:text-neutral-500"
        )}
      >
        {hasValue ? value : "—"}
      </p>

      {note && (
        <p className="mt-2 flex items-center gap-1.5 text-[12px] text-slate-500 dark:text-neutral-400">
          {/* The dot is paired with the word "Live", so the state is never colour-only. */}
          {live && (
            <span
              aria-hidden="true"
              className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-500 motion-safe:animate-pulse"
            />
          )}
          <span className="min-w-0 truncate">{note}</span>
        </p>
      )}
    </>
  );

  const shell = cx("rounded-2xl border p-5 transition-colors duration-150 motion-reduce:transition-none", t.surface);

  if (!to) return <div className={shell}>{body}</div>;

  return (
    <Link
      to={to}
      // The label is already in the card; the accessible name adds the destination so a
      // screen-reader user hears where the link goes, not just the number on it.
      aria-label={`${label}${hasValue ? `: ${value}` : ""} — open`}
      className={cx(shell, "block hover:border-slate-300 dark:hover:border-white/25", focusRing)}
    >
      {body}
    </Link>
  );
}
