import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import toast from "react-hot-toast";
import { FiClock } from "react-icons/fi";
import api, { diagnoseLoadError } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, type } from "../../ui/tokens";
import Skeleton from "../../ui/Skeleton";
import { ConsoleButton } from "../../ui/Button";
import { downloadJson } from "../../utils/export";
import CommandFilters from "../../components/admin/sections/CommandFilters";
import KpiRow from "../../components/admin/sections/KpiRow";
import SessionsAttention from "../../components/admin/sections/SessionsAttention";
import IncidentSummary from "../../components/admin/sections/IncidentSummary";
import UpcomingEvents from "../../components/admin/sections/UpcomingEvents";
import ConsoleFooterLinks from "../../components/admin/sections/ConsoleFooterLinks";

// The console re-reads itself on a timer; an operator should never have to wonder whether
// what they are looking at is current. The "Refreshed Ns ago" line is the receipt.
const REFRESH_MS = 30_000;
const SLO_SECONDS = 600; // beyond this the page says so instead of quietly going stale

// ── WHAT THIS PAGE IS ───────────────────────────────────────────────────────────────────
// An executive overview that answers five questions and stops:
//
//   Is the platform healthy?  Are there live sessions?  Does anything need me right now?
//   Is there an incident?     Is anything high-impact coming up?
//
// It had grown into a second copy of six other consoles. Removed, with where each one
// actually lives:
//
//   Lifecycle rail + stage matrix  -> System Status / Media Infrastructure. 8 stages x 4
//       regions of "100.00%", which services/ops.availability computes as 100 minus recorded
//       incident time — i.e. "nobody filed an incident", rendered as measured uptime. Thirty-two
//       green cells asserting something nothing measured is worse than no cells.
//   Playback quality KPI          -> nothing has ever written a playback_* metric, so the tile
//       was a permanent em dash with an apology attached.
//   Incidents feed                -> IncidentSummary below; System Status / Trust & Safety own
//       the list.
//   Action queues                 -> Support Operations / Trust & Safety / Commerce.
//   Governance exposure           -> Governance / Usage & Entitlements / Commerce.
//   Privileged activity feed      -> Audit, which is its own sidebar entry.
//   Static "operating rules" prose-> described how the system behaves, measured nothing.
//
// The surviving counts are in ConsoleFooterLinks, and only when non-zero. If you are about
// to add a panel here, check first whether it answers one of the five questions; if it
// answers "what is the detail", it belongs on the page that owns the detail.

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
    const diagnosis = diagnoseLoadError(error, "/admin/command-center");

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

      {/* Row 1 — the five measured KPIs */}
      <KpiRow kpis={kpis} age={`${ageSeconds}s`} />

      {/* Row 2 — the only thing on this page an operator acts on directly */}
      <SessionsAttention items={data?.attention || []} />

      {/* Row 3 — is anything burning, and is anything big coming */}
      <div className="grid gap-4 xl:grid-cols-2">
        <IncidentSummary incidents={data?.incidents || []} />
        <UpcomingEvents events={data?.upcoming_events || []} />
      </div>

      <ConsoleFooterLinks
        queues={data?.action_queues || []}
        governance={data?.governance}
        privilegedCount={(data?.privileged_activity || []).length}
      />

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
