// client/src/components/host/SummaryCards.jsx
// Producer KPI row — every value is live socket state, so these move on their own.
// Reuses the shared StatsCard (its Counter tweens between values); nothing bespoke here.
import { FiEye, FiTrendingUp, FiUsers, FiMic, FiActivity, FiClock } from "react-icons/fi";
import StatsCard from "../../ui/StatsCard";

const fmtDuration = (s) => {
  if (s == null) return "—";
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
};

export default function SummaryCards({ analytics, live }) {
  const a = analytics || {};
  const cards = [
    { title: "Live Viewers", value: a.viewers ?? 0, icon: FiEye, accent: "emerald", live },
    { title: "Peak Viewers", value: a.peak_viewers ?? 0, icon: FiTrendingUp, accent: "indigo" },
    { title: "On Stage", value: (a.speakers ?? 0) + (a.hosts ?? 0), icon: FiMic, accent: "violet" },
    { title: "Participants", value: a.participants ?? 0, icon: FiUsers, accent: "blue", live },
    { title: "Engagement", value: a.engagement ?? 0, icon: FiActivity, accent: "amber", suffix: "/100" },
    // A string value renders as-is (StatsCard only animates numbers) — right for a duration.
    { title: "Avg Watch", value: fmtDuration(a.avg_watch_seconds), icon: FiClock, accent: "rose" },
  ];
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 xl:grid-cols-6">
      {cards.map((c) => (
        <StatsCard key={c.title} {...c} />
      ))}
    </div>
  );
}
