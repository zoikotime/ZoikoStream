import { useState } from "react";
import { Link } from "react-router-dom";
import { FiRefreshCw, FiFileText, FiUsers, FiSettings, FiDownload } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import { downloadJson } from "../../utils/export";
import { notify } from "../../ui/Toast";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import GovernanceExposure from "../../components/admin/sections/GovernanceExposure";
import ActionQueues from "../../components/admin/sections/ActionQueues";
import PrivilegedActivity from "../../components/admin/sections/PrivilegedActivity";

// Governance — the standing obligations a human has to close, and the queues they close through.
//
// Every figure is an aggregate over governance_records; none of them is a status light. That
// distinction is the whole design: "3 entitlement overrides pending approval" is work, not
// health, and the console must not let the two blur into one green tick.
//
// The three panels here already exist (GovernanceExposure, ActionQueues, PrivilegedActivity);
// this page is where governance is the subject rather than one tile of the Command Center.
const REFRESH_MS = 30_000;

export default function Governance() {
  const center = useApi(() =>
    api
      .get("/admin/command-center", { params: { range: "live", scope: "core_live", include_test: false } })
      .then((r) => ({ ...r.data, fetched_at: Date.now() }))
  );
  const roles = useApi(() => api.get("/admin/roles").then((r) => r.data));
  useInterval(center.reload, REFRESH_MS);

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(
    () => setAgeSeconds(Math.floor((Date.now() - center.data.fetched_at) / 1000)),
    1000,
    Boolean(center.data)
  );

  const g = center.data?.governance || {};
  const queues = center.data?.action_queues || [];
  const activity = center.data?.privileged_activity || [];
  const roleList = Array.isArray(roles.data) ? roles.data : roles.data?.items || [];

  const openWork =
    (g.legal_holds || 0) +
    (g.entitlement_overrides_pending || 0) +
    (g.break_glass_under_review || 0) +
    (g.single_path_overrides_quarter || 0);

  const exportRecord = () => {
    if (!center.data) return;
    // The governance payload IS the record of what the operator was looking at, so it is
    // written verbatim rather than flattened — the same reasoning as the Command Center's
    // snapshot export.
    downloadJson(
      { governance: g, action_queues: queues, privileged_activity: activity },
      `zoikostream-governance-${new Date().toISOString().slice(0, 19)}`
    );
    notify.success("Governance record exported");
  };

  return (
    <ConsoleScreen
      title="Governance"
      subtitle="Legal holds, entitlement overrides, break-glass review and the queues that close them. Counts of work, not health."
      ageSeconds={ageSeconds}
      hasData={Boolean(center.data)}
      loading={center.loading}
      error={center.error}
      endpoint="/admin/command-center"
      onRetry={center.reload}
      actions={
        <>
          <ConsoleButton
            variant="secondary"
            leftIcon={FiDownload}
            onClick={exportRecord}
            disabled={!center.data}
          >
            Export record
          </ConsoleButton>
          <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={center.reload} disabled={center.loading}>
            Refresh
          </ConsoleButton>
        </>
      }
    >
      <div className="grid gap-4 xl:grid-cols-2">
        <GovernanceExposure governance={g} />
        <ActionQueues queues={queues} />
      </div>

      <PrivilegedActivity activity={activity} />

      <div className="grid gap-4 xl:grid-cols-3">
        <Panel title="Open obligations">
          <StatRow
            label="Total items awaiting a human"
            value={center.data ? openWork : null}
            reason="Loading"
            tone={openWork ? "text-amber-600 dark:text-amber-400" : undefined}
          />
          <StatRow label="Legal holds" value={center.data ? g.legal_holds ?? 0 : null} reason="Loading" />
          <StatRow
            label="Entitlement overrides pending"
            value={center.data ? g.entitlement_overrides_pending ?? 0 : null}
            reason="Loading"
          />
          <StatRow
            label="Break-glass grants in 72h review"
            value={center.data ? g.break_glass_under_review ?? 0 : null}
            reason="Loading"
          />
          <StatRow
            label="Single-path overrides this quarter"
            value={center.data ? g.single_path_overrides_quarter ?? 0 : null}
            reason="Loading"
            tone={g.single_path_overrides_quarter ? "text-rose-600 dark:text-rose-400" : undefined}
          />
        </Panel>

        <Panel title="Commercial obligations">
          <StatRow
            label="Usage export on-time"
            value={g.usage_export_on_time_pct == null ? null : `${g.usage_export_on_time_pct}%`}
            reason="No usage export has been recorded"
            tone={
              g.usage_export_on_time_pct == null
                ? undefined
                : g.usage_export_on_time_pct >= 99
                  ? "text-green-600 dark:text-green-400"
                  : "text-amber-600 dark:text-amber-400"
            }
          />
          <StatRow
            label="Exports recorded"
            value={center.data ? g.usage_export_total ?? 0 : null}
            reason="Loading"
          />
          <StatRow
            label="Contracted SLA credits owed"
            value={null}
            reason="SLA credit calculation is not modelled"
          />
          <StatRow
            label="Access-review cadence"
            value={null}
            reason="Review schedule is not modelled (documented gap)"
          />
          <StatRow
            label="Maintenance windows declared"
            value={null}
            reason="Maintenance windows are not modelled (documented gap)"
          />
        </Panel>

        <Panel title="Authority model">
          <StatRow
            label="Roles defined"
            value={roles.loading ? null : roleList.length}
            reason="Loading"
          />
          <p className={cx("mt-3 text-[13px] leading-[20px]", CONSOLE.muted)}>
            High-risk actions require scoped elevation, an impact preview, step-up
            authentication, and an audit entry. None of those four is optional, and none is
            enforced by this page — the API is what refuses.
          </p>
          <ul className="mt-4 space-y-2">
            {[
              [FiUsers, "Roles & permissions", "/admin/roles", "Who holds which authority."],
              [FiFileText, "Audit logs", "/admin/audit", "The record every action lands in."],
              [FiSettings, "Platform settings", "/admin/settings", "Global policy configuration."],
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
    </ConsoleScreen>
  );
}
