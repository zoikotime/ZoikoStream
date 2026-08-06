import { useState } from "react";
import { FiRefreshCw, FiServer } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import HealthDot from "../../components/admin/HealthDot";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import LifecycleRail from "../../components/admin/sections/LifecycleRail";
import StageMatrix from "../../components/admin/sections/StageMatrix";

// Media Infrastructure — the estate the platform runs on, stage by stage and region by region.
//
// Two real sources, combined: /admin/platform-health (services, each either probed or honestly
// reported as not_configured) and /admin/command-center (the lifecycle rail and the
// stage × region availability matrix, both already built as components).
//
// `not_configured` is NOT an outage and is not counted as one — services.platform_health is
// explicit about that, and this page keeps the distinction visible, because a console that
// paints "Storage: not integrated" red teaches operators to ignore red.
const REFRESH_MS = 30_000;

// Which services are load-bearing today versus declared-but-unintegrated. Derived from the
// status the API reports, not hardcoded — a service that becomes configured moves group by
// itself.
const isPending = (s) => s.status === "not_configured";

export default function Infrastructure() {
  const health = useApi(() => api.get("/admin/platform-health").then((r) => r.data));
  const center = useApi(() =>
    api
      .get("/admin/command-center", { params: { range: "live", scope: "core_live", include_test: false } })
      .then((r) => ({ ...r.data, fetched_at: Date.now() }))
  );

  const reloadAll = () => {
    health.reload();
    center.reload();
  };
  useInterval(reloadAll, REFRESH_MS);

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(
    () => setAgeSeconds(Math.floor((Date.now() - center.data.fetched_at) / 1000)),
    1000,
    Boolean(center.data)
  );

  const services = health.data?.services || [];
  const operating = services.filter((s) => !isPending(s));
  const pending = services.filter(isPending);
  const degraded = operating.filter((s) => s.status !== "ok");

  return (
    <ConsoleScreen
      title="Media Infrastructure"
      subtitle="Ingest, transcode, delivery and the services underneath them — what is probed, what is degraded, and what is declared but not yet integrated."
      ageSeconds={ageSeconds}
      hasData={Boolean(health.data || center.data)}
      loading={health.loading || center.loading}
      error={health.error || center.error}
      endpoint="/admin/platform-health"
      onRetry={reloadAll}
      actions={
        <>
          <HealthDot
            status={health.data?.overall || "neutral"}
            badge
            pulse={health.data?.overall === "ok"}
          />
          <ConsoleButton
            variant="secondary"
            leftIcon={FiRefreshCw}
            onClick={reloadAll}
            disabled={health.loading || center.loading}
          >
            Refresh
          </ConsoleButton>
        </>
      }
    >
      {/* Lifecycle across the estate, and the same stages broken out by region. */}
      <LifecycleRail
        stages={center.data?.lifecycle || []}
        trend={center.data?.kpis?.concurrent_audience?.series || []}
        eyebrow="Media lifecycle — live"
      />

      <div className="grid gap-4 xl:grid-cols-[1fr_1.35fr]">
        <div className="space-y-4">
          <Panel title="Operating services" description="Probed or reporting; a non-ok status here is a real outage." flush>
            <ul className={cx("divide-y", CONSOLE.divideY)}>
              {operating.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{s.name}</p>
                    <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{s.note}</p>
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    {s.latency_ms != null && (
                      <span className={cx("text-[12px]", type.mono, CONSOLE.muted)}>{s.latency_ms} ms</span>
                    )}
                    <HealthDot status={s.status} />
                  </div>
                </li>
              ))}
              {operating.length === 0 && (
                <li className={cx("px-5 py-6 text-center text-[13px]", CONSOLE.faint)}>
                  No service reported a live status.
                </li>
              )}
            </ul>
          </Panel>

          <Panel
            title="Declared, not integrated"
            description="Informational. These are not counted as outages and must not be read as one."
            flush
          >
            <ul className={cx("divide-y", CONSOLE.divideY)}>
              {pending.map((s) => (
                <li key={s.id} className="flex items-center justify-between gap-3 px-5 py-3">
                  <div className="min-w-0">
                    <p className={cx("truncate text-[13px] font-medium", CONSOLE.body)}>{s.name}</p>
                    <p className={cx("truncate text-[11px]", CONSOLE.faint)}>{s.note}</p>
                  </div>
                  <Badge tone="neutral">Not configured</Badge>
                </li>
              ))}
              {pending.length === 0 && (
                <li className={cx("px-5 py-6 text-center text-[13px]", CONSOLE.faint)}>
                  Every declared service is configured.
                </li>
              )}
            </ul>
          </Panel>
        </div>

        <div className="space-y-4">
          <StageMatrix stages={center.data?.lifecycle || []} regions={center.data?.regions || []} />

          <Panel title="Estate summary">
            <StatRow
              label="Services operating"
              value={health.loading ? null : operating.length}
              reason="Loading"
            />
            <StatRow
              label="Services degraded or down"
              value={health.loading ? null : degraded.length}
              reason="Loading"
              tone={degraded.length ? "text-rose-600 dark:text-rose-400" : undefined}
            />
            <StatRow
              label="Awaiting integration"
              value={health.loading ? null : pending.length}
              reason="Loading"
            />
            <StatRow
              label="Regions reporting"
              value={center.data ? (center.data.regions || []).length : null}
              reason="Loading"
            />
            <StatRow
              label="Edge PoP utilisation"
              value={null}
              reason="No CDN is integrated, so there is nothing to measure"
            />
            <StatRow
              label="Transcode queue depth"
              value={null}
              reason="Background workers are not integrated"
            />
          </Panel>

          <Panel title="Reading this page">
            <ul className={cx("space-y-2 text-[13px]", CONSOLE.body)}>
              <li className="flex gap-2.5">
                <FiServer className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                <span>
                  <strong className={CONSOLE.heading}>Operational</strong> means probed this
                  request — the database figure is a live round trip, not a cached verdict.
                </span>
              </li>
              <li className="flex gap-2.5">
                <FiServer className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                <span>
                  <strong className={CONSOLE.heading}>Not configured</strong> means the
                  integration does not exist yet in this deployment. It is not failing.
                </span>
              </li>
              <li className="flex gap-2.5">
                <FiServer className={cx("mt-0.5 shrink-0 text-[14px]", CONSOLE.faint)} aria-hidden="true" />
                <span>
                  A stage with no region row is a stage nothing has reported for in this window,
                  which is different from a stage at zero.
                </span>
              </li>
            </ul>
          </Panel>
        </div>
      </div>
    </ConsoleScreen>
  );
}
