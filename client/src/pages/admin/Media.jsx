import { useState } from "react";
import { Link } from "react-router-dom";
import { FiRefreshCw, FiFilm, FiExternalLink } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import ConsoleScreen from "../../components/admin/ConsoleScreen";
import HealthDot from "../../components/admin/HealthDot";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import LifecycleRail from "../../components/admin/sections/LifecycleRail";

// Media — the platform's view of the two lifecycle stages that own recorded assets:
// Preserve (capture, retention, holds) and Deliver (playback out to audiences).
//
// SCOPE WARNING, and the reason this page looks the way it does: the media API
// (/media/library, /media/stats) is ORG-SCOPED — routers/media._org resolves the CALLER's
// organization, so a super admin hitting it would see their own tenant's library and nothing
// else. Rendering that here under a platform heading would be a lie by framing. So this page
// reports what the platform-wide payload genuinely knows (stage health, availability, open
// incidents, retention obligations) and links out for per-tenant libraries instead of
// pretending to aggregate them.
const REFRESH_MS = 30_000;
const MEDIA_STAGES = ["preserve", "deliver"];

export default function Media() {
  const center = useApi(() =>
    api
      .get("/admin/command-center", { params: { range: "live", scope: "core_live", include_test: false } })
      .then((r) => ({ ...r.data, fetched_at: Date.now() }))
  );
  useInterval(center.reload, REFRESH_MS);

  const [ageSeconds, setAgeSeconds] = useState(0);
  useInterval(
    () => setAgeSeconds(Math.floor((Date.now() - center.data.fetched_at) / 1000)),
    1000,
    Boolean(center.data)
  );

  const lifecycle = center.data?.lifecycle || [];
  const stages = lifecycle.filter((s) => MEDIA_STAGES.includes(s.stage));
  const governance = center.data?.governance || {};
  const regions = center.data?.regions || [];
  const mediaIncidents = (center.data?.incidents || []).filter((i) => MEDIA_STAGES.includes(i.stage));

  return (
    <ConsoleScreen
      title="Media"
      subtitle="Preserve and Deliver — the health of recorded assets and their playback path across the platform."
      ageSeconds={ageSeconds}
      hasData={Boolean(center.data)}
      loading={center.loading}
      error={center.error}
      endpoint="/admin/command-center"
      onRetry={center.reload}
      actions={
        <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={center.reload} disabled={center.loading}>
          Refresh
        </ConsoleButton>
      }
    >
      <LifecycleRail stages={lifecycle} eyebrow="Media lifecycle — Preserve and Deliver in context" />

      <div className="grid gap-4 xl:grid-cols-2">
        {stages.map((s) => (
          <Panel
            key={s.stage}
            eyebrow={s.stage === "preserve" ? "Capture and retention" : "Playback delivery"}
            title={s.label}
            action={<HealthDot status={s.status} />}
          >
            <StatRow
              label="Availability (worst region)"
              value={s.availability == null ? null : `${s.availability}%`}
              reason="No probe reported for this stage in the window"
              tone={
                s.availability == null
                  ? undefined
                  : s.availability >= 99.9
                    ? "text-green-600 dark:text-green-400"
                    : s.availability >= 99
                      ? "text-amber-600 dark:text-amber-400"
                      : "text-rose-600 dark:text-rose-400"
              }
            />
            <StatRow
              label="Open incidents"
              value={s.open_incidents ?? 0}
              tone={s.open_incidents ? "text-rose-600 dark:text-rose-400" : undefined}
            />

            {/* Per-region breakdown: the headline above is the worst of these, so showing
                them makes the headline checkable rather than asserted. */}
            <div className={cx("mt-3 border-t pt-3", CONSOLE.divider)}>
              <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                By region
              </p>
              <ul className="space-y-1.5">
                {Object.entries(s.regions || {}).map(([region, pct]) => {
                  const label = regions.find((r) => r[0] === region || r.id === region);
                  return (
                    <li key={region} className="flex items-center justify-between gap-3">
                      <span className={cx("truncate text-[12px]", CONSOLE.body)}>
                        {(Array.isArray(label) ? label[1] : label?.label) || region}
                      </span>
                      <span
                        className={cx(
                          "shrink-0 text-[12px] font-semibold",
                          type.mono,
                          pct == null
                            ? CONSOLE.faint
                            : pct >= 99.9
                              ? "text-green-600 dark:text-green-400"
                              : pct >= 98
                                ? "text-amber-600 dark:text-amber-400"
                                : "text-rose-600 dark:text-rose-400"
                        )}
                        title={pct == null ? "No probe reported for this region" : undefined}
                      >
                        {pct == null ? "—" : `${pct}%`}
                      </span>
                    </li>
                  );
                })}
                {Object.keys(s.regions || {}).length === 0 && (
                  <li className={cx("text-[12px]", CONSOLE.faint)}>No region reported.</li>
                )}
              </ul>
            </div>

            {/* The services behind the stage, so a degraded stage is explainable. */}
            {(s.services || []).length > 0 && (
              <div className={cx("mt-3 border-t pt-3", CONSOLE.divider)}>
                <p className={cx("mb-2 text-[10px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>
                  Underlying services
                </p>
                <ul className="space-y-1.5">
                  {s.services.map((svc) => (
                    <li key={svc.id} className="flex items-center justify-between gap-3">
                      <span className={cx("truncate text-[12px]", CONSOLE.body)} title={svc.note}>
                        {svc.name}
                      </span>
                      <HealthDot status={svc.status} />
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </Panel>
        ))}
        {stages.length === 0 && !center.loading && (
          <Panel title="Media stages">
            <p className={cx("py-6 text-center text-[13px]", CONSOLE.faint)}>
              The lifecycle payload reported no Preserve or Deliver stage in this window.
            </p>
          </Panel>
        )}
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_1fr]">
        <Panel title="Retention obligations">
          <StatRow
            label="Active legal holds"
            value={center.data ? governance.legal_holds ?? 0 : null}
            reason="Loading"
            tone={governance.legal_holds ? "text-amber-600 dark:text-amber-400" : undefined}
          />
          <StatRow
            label="Media incidents open"
            value={center.data ? mediaIncidents.length : null}
            reason="Loading"
            tone={mediaIncidents.length ? "text-rose-600 dark:text-rose-400" : undefined}
          />
          <StatRow
            label="Assets under retention"
            value={null}
            reason="Retention is enforced per organization; there is no platform-wide asset count"
          />
          <StatRow
            label="Storage consumed"
            value={null}
            reason="Google Cloud Storage is not integrated in this deployment"
          />
          <StatRow
            label="Transcode backlog"
            value={null}
            reason="Background workers are not integrated"
          />
        </Panel>

        <Panel title="Per-tenant libraries">
          <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>
            The media API is organization-scoped by design — a library read resolves the
            caller&apos;s own tenant. There is no cross-tenant aggregate endpoint, so this console
            does not present one.
          </p>
          <p className={cx("mt-3 text-[13px] leading-[20px]", CONSOLE.muted)}>
            To inspect a specific customer&apos;s assets, open the organization and use its own
            Media &amp; Replay surface.
          </p>
          <div className="mt-4 flex flex-wrap gap-2">
            <ConsoleButton href="/admin/organizations" size="sm" leftIcon={FiExternalLink}>
              Organizations
            </ConsoleButton>
            <ConsoleButton href="/admin/live-events" size="sm" variant="secondary" leftIcon={FiFilm}>
              Live operations
            </ConsoleButton>
          </div>
          <p className={cx("mt-4 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
            Governance actions that touch retention — holds, purge approvals — live on{" "}
            <Link to="/admin/governance" className={cx("font-semibold", CONSOLE.link)}>
              Governance
            </Link>
            .
          </p>
        </Panel>
      </div>
    </ConsoleScreen>
  );
}
