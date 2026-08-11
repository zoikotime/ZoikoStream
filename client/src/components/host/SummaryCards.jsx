// client/src/components/host/SummaryCards.jsx
// Producer KPI row — every value is live socket state, so these move on their own.
//
// This uses a LOCAL tile rather than ui/StatsCard on purpose. ui/StatsCard is shared with
// the org dashboard, the admin console and the moderator console; its hover lifts the card
// and drops a large shadow, and its 44px icon tile pushed the label into truncation at
// six-up ("Particip…", "Engage…" in the old layout). Restyling it would have restyled four
// other pages, so the studio keeps its own denser tile and StatsCard stays exactly as is.
//
// Hierarchy is label → value → context, top to bottom: the metric is the largest thing in
// the tile and the only one in the heading colour, so a row of six reads as one instrument
// strip instead of six competing colour blocks. Only the icon and the live dot take accent.
import { FiEye, FiTrendingUp, FiUsers, FiMic, FiActivity, FiClock } from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Counter from "../../ui/Counter";
import { STUDIO, KPI_ACCENT } from "./studio";

const fmtDuration = (s) => {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
};

// One tile. `value` may be a number (tweened by Counter) or a pre-formatted string —
// a duration must not be animated digit by digit.
function Kpi({ title, value, icon: Icon, accent = "slate", suffix, context, live = false }) {
  const isNumber = typeof value === "number";
  return (
    <div
      className={cx(
        "flex min-h-[92px] flex-col justify-between gap-2 p-3",
        STUDIO.card,
        STUDIO.cardHover
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <p className={cx("min-w-0 truncate", STUDIO.eyebrow, STUDIO.muted)} title={title}>
          {title}
        </p>
        <Icon aria-hidden="true" className={cx("shrink-0 text-[15px]", KPI_ACCENT[accent])} />
      </div>

      <p className={cx("flex items-baseline gap-0.5", STUDIO.heading)}>
        <span className="text-[26px] font-semibold leading-none tracking-tight tabular-nums">
          {isNumber ? <Counter value={value} /> : value}
        </span>
        {suffix && (
          <span className={cx("text-[13px] font-medium leading-none", STUDIO.faint)}>{suffix}</span>
        )}
      </p>

      {/* Context earns the remaining space instead of leaving it blank, and doubles as the
          non-colour signal for "live" — the dot alone would be colour-only status. */}
      <p className={cx("flex items-center gap-1.5 truncate text-[11px] leading-none", live ? "text-green-600 dark:text-green-400" : STUDIO.faint)}>
        {live && (
          <span
            aria-hidden="true"
            className="h-1.5 w-1.5 shrink-0 animate-pulse rounded-full bg-green-500 motion-reduce:animate-none"
          />
        )}
        {live ? "Live now" : context}
      </p>
    </div>
  );
}

export default function SummaryCards({ analytics, live }) {
  const a = analytics || {};
  const cards = [
    {
      title: "Live Viewers", value: a.viewers ?? 0, icon: FiEye, accent: "green",
      context: "Audience idle", live,
    },
    {
      title: "Peak Viewers", value: a.peak_viewers ?? 0, icon: FiTrendingUp, accent: "brand",
      context: "This broadcast",
    },
    {
      title: "On Stage", value: (a.speakers ?? 0) + (a.hosts ?? 0), icon: FiMic, accent: "brand",
      context: "Hosts and speakers",
    },
    {
      title: "Participants", value: a.participants ?? 0, icon: FiUsers, accent: "blue",
      context: "Everyone connected", live,
    },
    {
      title: "Engagement", value: a.engagement ?? 0, icon: FiActivity, accent: "amber",
      suffix: "/100", context: "Weighted index",
    },
    {
      title: "Avg Watch", value: fmtDuration(a.avg_watch_seconds), icon: FiClock, accent: "rose",
      context: "Mean time in room",
    },
  ];

  return (
    // 2 → 3 → 6 up. Six across only from xl, so a tile never gets narrower than its label.
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6 xl:gap-3">
      {cards.map((c) => <Kpi key={c.title} {...c} />)}
    </div>
  );
}
