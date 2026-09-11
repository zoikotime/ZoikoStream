import { Link } from "react-router-dom";
import { FiCalendar, FiCheckCircle, FiRadio, FiUsers } from "react-icons/fi";
import { errMsg } from "../../api";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx } from "../../ui/tokens";
import Skeleton from "../../ui/Skeleton";
import { ConsoleButton } from "../../ui/Button";
import { useAuth } from "../../auth/AuthContext";
import { fmtDate } from "../../data/events";
import AttentionRequired from "../../components/organization/AttentionRequired";
import DashboardHeader from "../../components/organization/dashboard/DashboardHeader";
import MetricCard from "../../components/organization/dashboard/MetricCard";
import UpcomingEvents from "../../components/organization/dashboard/UpcomingEvents";
import AnalyticsOverview from "../../components/organization/dashboard/AnalyticsOverview";
import { RANGES } from "../../components/organization/dashboard/dashboardConfig";
import useDashboardData from "../../components/organization/dashboard/useDashboardData";

// How often the live figures re-poll. Only the sessions/counts request is on this timer —
// the analytics aggregate is not (see useDashboardData).
const REFRESH_MS = 30_000;

function DashboardSkeleton() {
  return (
    <div className="mx-auto max-w-[1300px] space-y-6">
      <div className="space-y-2">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="line" className="w-80" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} variant="block" className="h-[124px]" />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <Skeleton variant="block" className="h-80" />
        <Skeleton variant="block" className="h-80" />
      </div>
    </div>
  );
}

/**
 * The organization's home screen — what a member sees when they sign in.
 *
 * This used to be "Organization Overview": a six-tile operations grid (API success rate,
 * ready media assets, current usage, service health…) over a RIGHT NOW band, a sessions
 * table, developer operations, entitlement bars and a security posture panel. Every one of
 * those is a real answer to a real question, but none of them is the question somebody has
 * when they open the product: what is coming up, what is on air, and how did the last lot go.
 *
 * So the operational detail is not deleted — it is left where it is already reachable and
 * already better presented:
 *
 *   sessions table      -> /organization/sessions   (Streaming Sessions)
 *   entitlement bars    -> /organization/billing    (Usage & Entitlements)
 *   media assets        -> /organization/recordings (Media & Replay)
 *   service health      -> /organization/support    (Support & Status)
 *   API keys, ingest    -> /organization/settings?tab=developer
 *
 * What stays here is the one operational signal a member cannot afford to miss —
 * "Attention required" — and even that renders only when there is something in it.
 */
export default function OrganizationDashboard() {
  const { user } = useAuth();
  const { core, analytics, range, setRange } = useDashboardData();

  // Quietly current, with no countdown chrome. The old header carried a pause/resume toggle
  // and a live "Refreshed 11 sec ago · next in 19s" readout; that is instrumentation for an
  // operator watching a broadcast, and it is the single thing that made this page read as a
  // monitoring console. The data still refreshes — the page just stops narrating it.
  useInterval(core.reload, REFRESH_MS);

  if (core.loading && !core.data) return <DashboardSkeleton />;

  if (core.error && !core.data) {
    const status = core.error?.response?.status;
    const diagnosis =
      status === 404
        ? "The API responded but doesn’t have /organization/overview — the server is running an older build. Restart it to pick up the current code."
        : status === 401 || status === 403
        ? "Your session isn’t authorised for this organization. Sign in again."
        : status
        ? `The API returned ${status}: ${errMsg(core.error)}`
        : "The API is unreachable — check that the server is running and that VITE_API_URL points at it.";
    return (
      <div className="mx-auto max-w-[1300px]">
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 dark:border-rose-500/25 dark:bg-rose-500/10">
          <p className="text-[13px] font-semibold text-rose-700 dark:text-rose-300">
            Couldn’t load your dashboard
          </p>
          <p className="mt-1 text-[12px] text-rose-600 dark:text-rose-400">{diagnosis}</p>
          <ConsoleButton variant="secondary" size="sm" className="mt-3" onClick={core.reload}>
            Try again
          </ConsoleButton>
        </div>
      </div>
    );
  }

  const overview = core.data?.overview || {};
  const sessions = overview.sessions || {};
  const attention = overview.attention || [];

  // `?? null` matters: a rejected /events request and a genuine count of zero are different
  // answers, and MetricCard renders them differently ("—" plus a reason vs. "0").
  const upcomingTotal = core.data?.upcoming?.total ?? null;
  const upcomingItems = core.data?.upcoming?.items || [];
  const completedTotal = core.data?.completed?.total ?? null;
  const liveCount = sessions.live ?? null;
  const startingSoon = sessions.starting_soon || 0;

  const viewers = analytics.data?.summary?.viewers ?? null;
  const rangeLabel = RANGES.find((r) => r.key === range)?.label.toLowerCase() ?? "";

  return (
    <div className="mx-auto max-w-[1300px] space-y-6">
      <DashboardHeader user={user} onRefresh={core.reload} refreshing={core.loading} />

      <section aria-label="At a glance" className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard
          label="Upcoming events"
          value={upcomingTotal != null ? upcomingTotal.toLocaleString() : null}
          note={
            upcomingTotal == null
              ? "Couldn’t load events"
              : upcomingItems.length
              ? `Next on ${fmtDate(upcomingItems[0].start_time)}`
              : "Nothing scheduled yet"
          }
          icon={FiCalendar}
          tone="lavender"
          to="/organization/events"
        />

        <MetricCard
          label="Live now"
          value={liveCount != null ? liveCount.toLocaleString() : null}
          note={
            liveCount
              ? "On air now"
              : startingSoon
              ? `${startingSoon} starting within 30 min`
              : "Nothing broadcasting"
          }
          live={Boolean(liveCount)}
          icon={FiRadio}
          tone="lilac"
          to="/organization/sessions"
        />

        <MetricCard
          label="Completed events"
          value={completedTotal != null ? completedTotal.toLocaleString() : null}
          note={completedTotal == null ? "Couldn’t load events" : "Ended, all time"}
          icon={FiCheckCircle}
          tone="sky"
          to="/organization/events"
        />

        <MetricCard
          label="Total viewers"
          value={viewers != null ? viewers.toLocaleString() : null}
          note={
            analytics.loading && !analytics.data
              ? "Loading…"
              : analytics.error
              ? "Analytics unavailable"
              : viewers
              ? `Across events · ${rangeLabel}`
              : "No audience recorded yet"
          }
          icon={FiUsers}
          tone="cream"
          to="/organization/analytics"
        />
      </section>

      <div className="grid gap-5 xl:grid-cols-2">
        <UpcomingEvents events={upcomingItems} total={upcomingTotal} />
        <AnalyticsOverview
          data={analytics.data}
          loading={analytics.loading}
          error={analytics.error}
          range={range}
          onRange={setRange}
          onRetry={analytics.reload}
        />
      </div>

      {/* The only operational panel left, and it renders nothing at all when the org is in
          good standing — which is the normal case. An expiring credential or a blocked event
          is the one thing that should reach somebody on the home screen rather than waiting
          to be discovered on a page they had no reason to open. */}
      {attention.length > 0 && <AttentionRequired items={attention} />}

      {/* Where the operational detail went. A one-line signpost, so simplifying the page
          doesn't leave anyone hunting for a panel that used to be here. */}
      <p className={cx("text-[12px]", CONSOLE.faint)}>
        Looking for sessions, usage, credentials or platform status? They live on{" "}
        <Link to="/organization/sessions" className={CONSOLE.link}>Streaming Sessions</Link>,{" "}
        <Link to="/organization/billing" className={CONSOLE.link}>Usage &amp; Entitlements</Link>,{" "}
        <Link to="/organization/settings?tab=developer" className={CONSOLE.link}>Settings &rsaquo; Developer</Link> and{" "}
        <Link to="/organization/support" className={CONSOLE.link}>Support &amp; Status</Link>.
      </p>
    </div>
  );
}
