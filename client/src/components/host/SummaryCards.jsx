// client/src/components/host/SummaryCards.jsx
// Host Dashboard KPI row — reuses the shared StatsCard.
import StatsCard from "../../ui/StatsCard";
import { summaryStats } from "../../data/host";

export default function SummaryCards() {
  return (
    <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
      {summaryStats.map((s) => (
        <StatsCard key={s.title} {...s} />
      ))}
    </div>
  );
}
