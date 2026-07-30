import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { FiClock } from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, type } from "../../ui/tokens";
import Skeleton from "../../ui/Skeleton";
import { ConsoleButton } from "../../ui/Button";
import { downloadJson } from "../../utils/export";
import CommandFilters from "../../components/admin/sections/CommandFilters";
import LifecycleRail from "../../components/admin/sections/LifecycleRail";
import KpiRow from "../../components/admin/sections/KpiRow";
import SessionsAttention from "../../components/admin/sections/SessionsAttention";
import StageMatrix from "../../components/admin/sections/StageMatrix";
import IncidentsSecurity from "../../components/admin/sections/IncidentsSecurity";
import ActionQueues from "../../components/admin/sections/ActionQueues";
import GovernanceExposure from "../../components/admin/sections/GovernanceExposure";
import PrivilegedActivity from "../../components/admin/sections/PrivilegedActivity";
import UpcomingEvents from "../../components/admin/sections/UpcomingEvents";

// The console re-reads itself on a timer; an operator should never have to wonder whether
// what they are looking at is current. The "Refreshed Ns ago" line is the receipt.
const REFRESH_MS = 30_000;
const SLO_SECONDS = 600; // beyond this the page says so instead of quietly going stale

// Footing notes — the console's operating rules. Static because they describe how the
// system behaves, not what it currently measures.
const RULES = [
  "Live mode is the default; test data never enters readiness, badge, or attention counts.",
  "Lifecycle, health, risk, readiness, and mode remain separate governed axes.",
  "High-risk actions require scoped elevation, impact preview, step-up authentication, and audit.",
];

function CommandCenterSkeleton() {
  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      <div className="space-y-3">
        <Skeleton variant="title" className="w-80" />
        <Skeleton variant="line" className="w-[28rem]" />
      </div>
      <Skeleton variant="block" className="h-11" />
      <Skeleton variant="block" className="h-32" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} variant="block" className="h-44" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <Skeleton variant="block" className="h-72" />
        <Skeleton variant="block" className="h-72" />
      </div>
    </div>
  );
}

export default function AdminDashboard() {
  const [filters, setFilters] = useState({
    range: "live",
    region: null,
    scope: "core_live",
    include_test: false,
  });

  // useApi holds the latest thunk without re-running, so reload() picks up current filters.
  const { data, loading, error, reload } = useApi(() =>
    api
      .get("/admin/command-center", {
        params: {
          range: filters.range,
          region: filters.region || undefined,
          scope: filters.scope,
          include_test: filters.include_test,
          // datetime-local has no zone; toISOString sends the operator's wall-clock as a
          // real instant so the server and the picker agree on the window.
          ...(filters.range === "custom" && filters.from
            ? {
                from: new Date(filters.from).toISOString(),
                to: filters.to ? new Date(filters.to).toISOString() : undefined,
              }
            : {}),
        },
      })
      .then((r) => ({ ...r.data, fetched_at: Date.now() }))
  );

  const applyFilters = useCallback(
    (next) => {
      setFilters(next);
      // The window is a server-side query parameter, so a filter change is a refetch —
      // not a client-side filter of an already-scoped payload.
      reload();
    },
    [reload]
  );

  useInterval(reload, REFRESH_MS);

  // Age ticks locally so the freshness line moves every second without a request. The
  // clock read happens in the interval, never during render — a render must be pure, and
  // reading Date.now() there would make the same props paint differently each pass.
  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(
    () => setAgeSeconds(Math.floor((Date.now() - data.fetched_at) / 1000)),
    1000,
    Boolean(data)
  );
  const withinSlo = ageSeconds <= SLO_SECONDS;

  const exportSnapshot = useCallback(() => {
    if (!data) return;
    downloadJson(data, `zoikostream-command-center-${new Date().toISOString().slice(0, 19)}`);
    toast.success("Snapshot exported");
  }, [data]);

  const ageLabel = useMemo(
    () => (ageSeconds < 1 ? "just now" : `${ageSeconds} sec ago`),
    [ageSeconds]
  );

  if (loading && !data) return <CommandCenterSkeleton />;

  if (error && !data) {
    // Name the actual failure. "The API didn't answer" sends someone hunting the network
    // when a 404 means the running server predates this endpoint and 401/403 means the
    // session expired — different fixes, so the page distinguishes them.
    const status = error?.response?.status;
    const diagnosis =
      status === 404
        ? "The API responded, but doesn’t have /admin/command-center — the server is running an older build. Restart it to pick up the current code."
        : status === 401 || status === 403
        ? "Your session isn’t authorised for the platform console. Sign in again as a super admin."
        : status
        ? `The platform API returned ${status}: ${errMsg(error)}`
        : "The platform API is unreachable — check that the API server is running and that VITE_API_URL points at it.";

    return (
      <div className="mx-auto max-w-[1500px]">
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 dark:border-rose-500/25 dark:bg-rose-500/10">
          <p className="text-[13px] font-semibold text-rose-700 dark:text-rose-300">
            Couldn’t load the Command Center
          </p>
          <p className="mt-1 text-[12px] text-rose-600 dark:text-rose-400">{diagnosis}</p>
          <p className="mt-1 text-[12px] text-rose-600/80 dark:text-rose-400/80">
            Nothing on this page is safe to read until it loads.
          </p>
          <ConsoleButton variant="secondary" size="sm" className="mt-3" onClick={reload}>
            Try again
          </ConsoleButton>
        </div>
      </div>
    );
  }

  const kpis = data?.kpis || {};

  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className={cx("text-[28px] font-bold leading-tight tracking-tight sm:text-[32px]", CONSOLE.heading)}>
            Platform Command Center
          </h1>
          <p className={cx("mt-1 text-[13px]", CONSOLE.muted)}>
            Global operational, security, governance, and customer picture for ZoikoStream.
          </p>
          <p className={cx("mt-2 flex items-center gap-1.5 text-[12px]", type.mono)}>
            <span className={withinSlo ? "text-green-600 dark:text-green-400" : "text-amber-600 dark:text-amber-400"}>
              Refreshed {ageLabel}
            </span>
            <span className={CONSOLE.faint}>·</span>
            <span className={withinSlo ? "text-green-600 dark:text-green-400" : "text-amber-600 dark:text-amber-400"}>
              {withinSlo ? "within SLO" : "stale — refresh"}
            </span>
          </p>
        </div>

        <ConsoleButton
          href="/admin/live-events"
          leftIcon={FiClock}
          className="shadow-lg shadow-violet-600/25"
        >
          Open Live Operations
        </ConsoleButton>
      </div>

      {/* Window controls */}
      <CommandFilters
        value={filters}
        onChange={applyFilters}
        regions={data?.regions || []}
        onRefresh={reload}
        onExport={exportSnapshot}
        refreshing={loading}
      />

      {/* Lifecycle rail */}
      <LifecycleRail stages={data?.lifecycle || []} trend={kpis.concurrent_audience?.series || []} />

      {/* KPI row */}
      <KpiRow kpis={kpis} age={`${ageSeconds}s`} />

      {/* Attention + stage health */}
      <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <SessionsAttention items={data?.attention || []} />
        <StageMatrix stages={data?.lifecycle || []} regions={data?.regions || []} />
      </div>

      {/* Incidents + queues */}
      <div className="grid gap-4 xl:grid-cols-2">
        <IncidentsSecurity incidents={data?.incidents || []} />
        <ActionQueues queues={data?.action_queues || []} />
      </div>

      {/* Governance + privileged activity */}
      <div className="grid gap-4 xl:grid-cols-2">
        <GovernanceExposure governance={data?.governance} />
        <PrivilegedActivity activity={data?.privileged_activity || []} />
      </div>

      {/* Upcoming high-impact events */}
      <UpcomingEvents events={data?.upcoming_events || []} />

      {/* Operating rules */}
      <div className={cx("grid gap-x-8 gap-y-2 border-t pt-4 sm:grid-cols-2 lg:grid-cols-3", CONSOLE.divider)}>
        {RULES.map((rule) => (
          <p key={rule} className={cx("text-[11px] leading-relaxed", CONSOLE.faint)}>
            — {rule}
          </p>
        ))}
      </div>

      {/* Elevation reminder: the console states when the caller is acting with elevated
          scope, so a privileged session is never invisible. */}
      {data?.elevation && (
        <p className={cx("text-[11px]", CONSOLE.faint)}>
          Acting as <span className="font-semibold">{data.elevation.scope}</span> · elevation expires in{" "}
          {Math.ceil(data.elevation.seconds_remaining / 60)} min ·{" "}
          <Link to="/admin/audit" className={CONSOLE.link}>
            every action is audited
          </Link>
        </p>
      )}
    </div>
  );
}
