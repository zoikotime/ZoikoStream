import { useMemo, useState } from "react";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { FiPause, FiPlay, FiRefreshCw } from "react-icons/fi";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import Skeleton from "../../ui/Skeleton";
import { ConsoleButton } from "../../ui/Button";
import { compact } from "../../components/admin/format";
import MetricTile from "../../components/admin/MetricTile";
import { useOrgScope } from "../../components/organization/orgScope";
import LiveStatusBand from "../../components/organization/LiveStatusBand";
import AttentionRequired from "../../components/organization/AttentionRequired";
import ManagedEvents from "../../components/organization/ManagedEvents";
import SessionsTable from "../../components/organization/SessionsTable";
import DeveloperOps from "../../components/organization/DeveloperOps";
import EntitlementBars from "../../components/organization/EntitlementBars";
import SecuritySupport from "../../components/organization/SecuritySupport";

const REFRESH_MS = 30_000;

const HEALTH_TONE = {
  ok: "text-green-600 dark:text-green-400",
  warn: "text-amber-600 dark:text-amber-400",
  down: "text-rose-600 dark:text-rose-400",
  not_configured: CONSOLE.faint,
};

function OverviewSkeleton() {
  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      <div className="space-y-3">
        <Skeleton variant="title" className="w-72" />
        <Skeleton variant="line" className="w-96" />
      </div>
      <Skeleton variant="block" className="h-28" />
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} variant="block" className="h-40" />
        ))}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <Skeleton variant="block" className="h-56" />
        <Skeleton variant="block" className="h-56" />
      </div>
    </div>
  );
}

export default function OrganizationDashboard() {
  // Opt into the topbar's workspace + range controls; they scope the fetch below.
  const { filters, state: consoleState } = useOrgScope({ scoped: true });

  const { data, loading, error, reload } = useApi(() =>
    api
      .get("/organization/overview", {
        params: {
          range: filters.range,
          workspace: filters.workspace || undefined,
        },
      })
      .then((r) => ({ ...r.data, fetched_at: Date.now() }))
  );

  // Refetch when the scope changes. `filters` is the only dependency that alters the query.
  const scopeKey = `${filters.range}|${filters.workspace || ""}`;
  const [lastScope, setLastScope] = useState(scopeKey);
  if (scopeKey !== lastScope) {
    setLastScope(scopeKey);
    reload();
  }

  // Auto-refresh is pausable: reading a figure off an ops dashboard while it reloads under
  // you is the one thing this page shouldn't do to you. The timer is the only thing that
  // stops — the manual button still works while paused.
  const [autoRefresh, setAutoRefresh] = useState(true);
  useInterval(reload, REFRESH_MS, autoRefresh);

  // Freshness ticks locally; the clock is read inside the interval, never during render.
  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(
    () => setAgeSeconds(Math.floor((Date.now() - data.fetched_at) / 1000)),
    1000,
    Boolean(data)
  );
  const ageLabel = useMemo(
    () => (ageSeconds < 1 ? "just now" : `${ageSeconds} sec ago`),
    [ageSeconds]
  );
  // Counts down to the next automatic refresh, so "is this stale?" has a visible answer.
  const nextIn = Math.max(0, Math.ceil(REFRESH_MS / 1000 - ageSeconds));

  if (loading && !data) return <OverviewSkeleton />;

  if (error && !data) {
    const status = error?.response?.status;
    const diagnosis =
      status === 404
        ? "The API responded but doesn’t have /organization/overview — the server is running an older build. Restart it to pick up the current code."
        : status === 401 || status === 403
        ? "Your session isn’t authorised for this organization. Sign in again."
        : status
        ? `The API returned ${status}: ${errMsg(error)}`
        : "The API is unreachable — check that the server is running and that VITE_API_URL points at it.";
    return (
      <div className="mx-auto max-w-[1500px]">
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 dark:border-rose-500/25 dark:bg-rose-500/10">
          <p className="text-[13px] font-semibold text-rose-700 dark:text-rose-300">
            Couldn’t load the organization overview
          </p>
          <p className="mt-1 text-[12px] text-rose-600 dark:text-rose-400">{diagnosis}</p>
          <ConsoleButton variant="secondary" size="sm" className="mt-3" onClick={reload}>
            Try again
          </ConsoleButton>
        </div>
      </div>
    );
  }

  const org = data?.organization || consoleState?.organization || {};
  const workspace = data?.workspace || consoleState?.workspace;
  const sessions = data?.sessions || {};
  const media = data?.media_assets || {};
  const ent = data?.entitlements || {};
  const apiPosture = data?.api || {};
  const health = data?.service_health || {};
  const age = `${ageSeconds}s`;

  return (
    <div className="mx-auto max-w-[1500px] space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className={cx("text-[26px] font-bold leading-tight tracking-tight sm:text-[30px]", CONSOLE.heading)}>
            Organization Overview
          </h1>
          <p className={cx("mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]", CONSOLE.muted)}>
            <span className="font-medium">{org.name || "Organization"}</span>
            <span className={CONSOLE.faint}>·</span>
            <span>{workspace?.label || "production"} workspace</span>
            <span className={CONSOLE.faint}>·</span>
            <span
              className={cx(
                type.mono,
                autoRefresh ? "text-green-600 dark:text-green-400" : CONSOLE.faint
              )}
            >
              <span className={cx("mr-1", loading && "animate-pulse motion-reduce:animate-none")}>●</span>
              Refreshed {ageLabel}
              {autoRefresh && <span className={CONSOLE.faint}> · next in {nextIn}s</span>}
            </span>
          </p>
        </div>

        {/* Refresh controls. Both act on the fetch this page already makes — same endpoint,
            same params, nothing new asked of the API. */}
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={() => setAutoRefresh((a) => !a)}
            aria-pressed={autoRefresh}
            title={autoRefresh ? `Auto-refreshing every ${REFRESH_MS / 1000}s` : "Auto-refresh paused"}
            className={cx(
              "inline-flex h-9 items-center gap-2 rounded-lg border px-3 text-[13px] font-medium transition-colors duration-150 motion-reduce:transition-none",
              focusRing,
              autoRefresh
                ? "border-violet-300 bg-violet-50 text-violet-700 dark:border-violet-500/40 dark:bg-violet-500/10 dark:text-violet-300"
                : CONSOLE.control
            )}
          >
            {autoRefresh ? <FiPause className="text-[13px]" aria-hidden="true" /> : <FiPlay className="text-[13px]" aria-hidden="true" />}
            {autoRefresh ? "Live" : "Paused"}
          </button>
          <ConsoleButton
            variant="secondary"
            size="md"
            onClick={reload}
            loading={loading}
            leftIcon={FiRefreshCw}
          >
            Refresh
          </ConsoleButton>
        </div>
      </div>

      {/* What is happening in this org right now. This slot held the media lifecycle rail —
          platform stage availability, which is (a) the super admin's question, (b) already
          answered twice more on this screen by the topbar health pill and the Service health
          tile, and (c) still available to org admins on Support & Status, where the same
          component is mounted. Availability also reads a flat 100.00% whenever no Incident row
          exists, so the rail could never tell this org anything about itself. */}
      <LiveStatusBand sessions={sessions} attention={data?.attention || []} age={ageLabel} />

      {/* KPI row — same tile component as the platform console. */}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <MetricTile
          label="Active sessions"
          value={sessions.active != null ? sessions.active.toLocaleString() : null}
          note={
            sessions.active
              ? `${sessions.live || 0} live · ${sessions.paused || 0} paused`
              : `${sessions.starting_soon || 0} starting in 30 min`
          }
          color="#8b5cf6"
          to="/organization/sessions"
          linkLabel="Streaming Sessions"
          age={age}
        />

        <MetricTile
          label="API request success"
          value={apiPosture.success_rate != null ? apiPosture.success_rate.toFixed(2) : null}
          unit={apiPosture.success_rate != null ? "%" : null}
          note={apiPosture.p95_ms != null ? `p95 ${apiPosture.p95_ms}ms` : apiPosture.note}
          color="#22c55e"
          to="/organization/developers"
          linkLabel="Developer Platform"
          age={age}
        />

        <MetricTile
          label="Ready media assets"
          value={media.ready != null ? media.ready.toLocaleString() : null}
          note={
            media.total
              ? [
                  media.processing ? `${media.processing} processing` : null,
                  media.not_captured ? `${media.not_captured} not captured` : null,
                ].filter(Boolean).join(" · ") || "all captured"
              : "No recordings yet"
          }
          color="#3b82f6"
          to="/organization/recordings"
          linkLabel="Media & Replay"
          age={age}
        />

        <MetricTile
          label="Peak audience"
          value={sessions.peak_audience != null ? compact(sessions.peak_audience) : null}
          note={
            sessions.current_audience != null
              ? `${compact(sessions.current_audience)} watching now`
              : "No audience recorded yet"
          }
          // The only tile with a real series behind it: concurrent audience over the selected
          // window, bucketed server-side. The other five have no producer, so they keep the
          // faint baseline rather than an invented curve.
          series={data?.trends?.audience || []}
          color="#22d3ee"
          to="/organization/analytics"
          linkLabel="Analytics"
          age={age}
        />

        <MetricTile
          label="Current usage"
          value={ent.highest_percent != null ? ent.highest_percent.toFixed(0) : null}
          unit={ent.highest_percent != null ? "%" : null}
          tone={
            ent.highest_percent >= 100
              ? "text-rose-600 dark:text-rose-400"
              : ent.highest_percent >= 80
              ? "text-amber-600 dark:text-amber-400"
              : undefined
          }
          note={
            ent.highest_percent != null
              ? `of ${ent.plan || "plan"} entitlement`
              : "No plan limits set"
          }
          color="#f59e0b"
          to="/organization/billing"
          linkLabel="Usage & Entitlements"
          age={age}
        />

        <MetricTile
          label="Service health"
          value={health.label || null}
          tone={HEALTH_TONE[health.status]}
          note={health.cause || "All services in use are operational"}
          color="#22c55e"
          to="/organization/support"
          linkLabel="Support & Status"
          age={age}
        />
      </div>

      {/* Attention + managed events */}
      <div className="grid gap-4 xl:grid-cols-2">
        <AttentionRequired items={data?.attention || []} />
        <ManagedEvents events={data?.upcoming_events || []} />
      </div>

      {/* Sessions + developer operations */}
      <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <SessionsTable sessions={sessions} />
        <DeveloperOps ops={data?.developer_ops} />
      </div>

      <EntitlementBars entitlements={ent} />
      <SecuritySupport posture={data?.security_support} />
    </div>
  );
}
