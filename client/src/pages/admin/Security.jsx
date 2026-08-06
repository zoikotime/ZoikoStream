import { useState } from "react";
import { Link } from "react-router-dom";
import { FiRefreshCw, FiShield, FiFileText } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import HealthDot from "../../components/admin/HealthDot";
import IncidentsSecurity from "../../components/admin/sections/IncidentsSecurity";
import PrivilegedActivity from "../../components/admin/sections/PrivilegedActivity";
import { timeAgo } from "../../components/admin/format";

// Trust & Safety — the security posture of the platform: open incidents, who is currently
// acting with elevated scope, and the audit trail that makes both accountable.
//
// Reads /admin/command-center (incidents, privileged_activity, elevation) and the most recent
// /admin/audit-logs page. Both panels already exist as components; this page is the place they
// are the subject rather than a corner of the dashboard.
//
// What this page will NOT show: a "threats blocked" or "attacks mitigated" figure. Nothing in
// this stack observes refused traffic, and a fabricated zero there is the most dangerous number
// a security console can print.
const REFRESH_MS = 30_000;
const AUDIT_ROWS = 8;

export default function Security() {
  const center = useApi(() =>
    api
      .get("/admin/command-center", { params: { range: "live", scope: "core_live", include_test: false } })
      .then((r) => ({ ...r.data, fetched_at: Date.now() }))
  );
  const audit = useApi(() =>
    api.get("/admin/audit-logs", { params: { page: 1, page_size: AUDIT_ROWS } }).then((r) => r.data)
  );
  const health = useApi(() => api.get("/admin/platform-health").then((r) => r.data));

  const reloadAll = () => {
    center.reload();
    audit.reload();
    health.reload();
  };
  useInterval(reloadAll, REFRESH_MS);

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(
    () => setAgeSeconds(Math.floor((Date.now() - center.data.fetched_at) / 1000)),
    1000,
    Boolean(center.data)
  );

  const incidents = center.data?.incidents || [];
  const activity = center.data?.privileged_activity || [];
  const elevation = center.data?.elevation;
  const logs = audit.data?.items || [];
  const authService = (health.data?.services || []).find((s) => s.id === "auth");

  const security = incidents.filter((i) => i.category === "security" || i.kind === "security");

  return (
    <ConsoleScreen
      title="Trust & Safety"
      subtitle="Open incidents, privileged access in effect, and the audit trail behind every high-risk action."
      ageSeconds={ageSeconds}
      hasData={Boolean(center.data)}
      loading={center.loading}
      error={center.error}
      endpoint="/admin/command-center"
      onRetry={reloadAll}
      actions={
        <>
          {elevation ? (
            <Badge tone="warning" dot>
              Elevated: {elevation.scope}
            </Badge>
          ) : (
            <Badge tone="success" dot>
              No elevation active
            </Badge>
          )}
          <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reloadAll} disabled={center.loading}>
            Refresh
          </ConsoleButton>
        </>
      }
    >
      {/* An elevated session is never invisible — it is the first thing on this page. */}
      {elevation && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 dark:border-amber-500/25 dark:bg-amber-500/10">
          <p className="text-[13px] font-semibold text-amber-800 dark:text-amber-200">
            You are acting with elevated scope: {elevation.scope}
          </p>
          <p className="mt-1 text-[12px] text-amber-700 dark:text-amber-300">
            Expires in {Math.ceil(elevation.seconds_remaining / 60)} min. Every action taken under
            elevation is written to the audit log.
          </p>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-2">
        <IncidentsSecurity incidents={incidents} />
        <PrivilegedActivity activity={activity} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <Panel
          title="Recent audited actions"
          description={`The ${AUDIT_ROWS} most recent entries. The full trail is searchable.`}
          action={
            <Link to="/admin/audit" className={cx("text-[12px] font-semibold", CONSOLE.link)}>
              Audit logs →
            </Link>
          }
          flush
        >
          {audit.loading ? (
            <ul className={cx("divide-y", CONSOLE.divideY)} aria-hidden="true">
              {Array.from({ length: 4 }).map((_, i) => (
                <li key={i} className="px-5 py-3">
                  <div className="zk-skeleton h-4 w-48 rounded bg-slate-200 dark:bg-white/[0.07]" />
                  <div className="zk-skeleton mt-2 h-3 w-64 rounded bg-slate-200 dark:bg-white/[0.07]" />
                </li>
              ))}
            </ul>
          ) : logs.length === 0 ? (
            <p className={cx("px-5 py-10 text-center text-[13px]", CONSOLE.faint)}>
              No audited actions recorded yet.
            </p>
          ) : (
            <ul className={cx("divide-y", CONSOLE.divideY)}>
              {logs.map((row) => (
                <li key={row.id} className="flex items-start justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className={cx("truncate text-[13px] font-semibold", type.mono, CONSOLE.heading)}>
                      {row.action}
                    </p>
                    <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
                      {row.actor_name || row.actor_email || "system"}
                      {row.target_type ? ` · ${row.target_type}` : ""}
                      {row.ip ? ` · ${row.ip}` : ""}
                    </p>
                  </div>
                  <span className={cx("shrink-0 text-[11px]", CONSOLE.faint)}>
                    {row.created_at ? timeAgo(row.created_at) : "—"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <div className="space-y-4">
          <Panel title="Posture">
            <StatRow
              label="Open incidents"
              value={center.data ? incidents.length : null}
              reason="Loading"
              tone={incidents.length ? "text-rose-600 dark:text-rose-400" : undefined}
            />
            <StatRow
              label="Security-classified incidents"
              value={center.data ? security.length : null}
              reason="Loading"
              tone={security.length ? "text-rose-600 dark:text-rose-400" : undefined}
            />
            <StatRow
              label="Privileged actions in window"
              value={center.data ? activity.length : null}
              reason="Loading"
            />
            <StatRow
              label="Authentication"
              value={authService ? <HealthDot status={authService.status} /> : null}
              reason="Loading"
            />
          </Panel>

          <Panel title="Not observed here">
            <StatRow
              label="Blocked requests"
              value={null}
              reason="Refused traffic is not recorded — a zero here would be fiction"
            />
            <StatRow
              label="Failed sign-in attempts"
              value={null}
              reason="Authentication failures are not aggregated"
            />
            <StatRow
              label="Open vulnerability findings"
              value={null}
              reason="No scanner feed is integrated (documented gap)"
            />
            <StatRow
              label="Access-review schedule"
              value={null}
              reason="Review cadence is not modelled (documented gap)"
            />
            <p className={cx("mt-3 border-t pt-3 text-[12px] leading-snug", CONSOLE.divider, CONSOLE.faint)}>
              These stay “—” on purpose. On a security console, an unmeasured zero is worse than
              a blank.
            </p>
          </Panel>

          <Panel title="Controls">
            <ul className="space-y-2">
              {[
                [FiShield, "Roles & permissions", "/admin/roles", "Who holds which authority."],
                [FiFileText, "Audit logs", "/admin/audit", "The full, searchable trail."],
                [FiShield, "Governance", "/admin/governance", "Holds, overrides and break-glass review."],
                [FiShield, "Platform settings", "/admin/settings", "Global security configuration."],
              ].map(([Icon, label, to, desc]) => (
                <li key={to}>
                  <Link
                    to={to}
                    className={cx(
                      "flex items-start gap-2.5 rounded-lg px-2 py-2 transition-colors duration-150 motion-reduce:transition-none",
                      "hover:bg-slate-100 dark:hover:bg-white/[0.06]"
                    )}
                  >
                    <Icon className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                    <span className="min-w-0">
                      <span className={cx("block text-[13px] font-semibold", CONSOLE.heading)}>{label}</span>
                      <span className={cx("block text-[12px]", CONSOLE.faint)}>{desc}</span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </Panel>
        </div>
      </div>
    </ConsoleScreen>
  );
}
