import { useEffect, useState } from "react";
import { Reveal } from "../../ui/motion";
import Skeleton from "../../ui/Skeleton";
import PlatformStatus from "../../components/admin/sections/PlatformStatus";
import CriticalAlerts from "../../components/admin/sections/CriticalAlerts";
import LivePlatform from "../../components/admin/sections/LivePlatform";
import OrgsAttention from "../../components/admin/sections/OrgsAttention";
import RevenueGrowth from "../../components/admin/sections/RevenueGrowth";
import LiveEventActivity from "../../components/admin/sections/LiveEventActivity";
import PlatformActivity from "../../components/admin/sections/PlatformActivity";
import Infrastructure from "../../components/admin/sections/Infrastructure";
import AuditTimeline from "../../components/admin/sections/AuditTimeline";
import QuickActions from "../../components/admin/sections/QuickActions";

// Boot skeleton — mirrors the real layout (header + stat strip + stacked panels).
// TODO(backend): key `loading` off the platform-stats fetch instead of a timer.
function DashboardSkeleton() {
  return (
    <div className="mx-auto max-w-[1440px] space-y-8">
      <div className="space-y-3">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="line" className="w-96" />
      </div>
      <Skeleton variant="block" className="h-24" />
      <Skeleton variant="block" className="h-64" />
      <Skeleton variant="block" className="h-56" />
    </div>
  );
}

// Platform Operations Center. Sections follow a top-down attention hierarchy:
// status → alerts → live now → orgs at risk → revenue → live events → what just
// happened → infrastructure → quick actions. Each reads from data/platform.js.
export default function AdminDashboard() {
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const t = setTimeout(() => setLoading(false), 500);
    return () => clearTimeout(t);
  }, []);

  if (loading) return <DashboardSkeleton />;

  return (
    <div className="mx-auto max-w-[1440px] space-y-8">
      <PlatformStatus />
      <CriticalAlerts />

      <Reveal><LivePlatform /></Reveal>
      <Reveal><OrgsAttention /></Reveal>
      <Reveal><RevenueGrowth /></Reveal>
      <Reveal><LiveEventActivity /></Reveal>

      {/* "What just happened" band — operational feed + audit history side by side. */}
      <div className="grid gap-6 xl:grid-cols-2">
        <Reveal><PlatformActivity /></Reveal>
        <Reveal><AuditTimeline /></Reveal>
      </div>

      <Reveal><Infrastructure /></Reveal>
      <Reveal><QuickActions /></Reveal>
    </div>
  );
}
