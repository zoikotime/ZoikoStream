import { useMemo } from "react";
import { Link } from "react-router-dom";
import { FiShield, FiGlobe, FiFileText, FiAlertOctagon } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Skeleton from "../../ui/Skeleton";
import Panel from "../../components/admin/Panel";
import HealthDot from "../../components/admin/HealthDot";
import LifecycleRail from "../../components/admin/sections/LifecycleRail";

// Support & Status — this organization's view of platform availability.
//
// Everything here is derived from /organization/overview, the payload the Overview page
// already polls: `lifecycle` carries per-stage status/availability/open-incident counts,
// `service_health` the single headline verdict. No new endpoint, no widened scope — an org
// admin sees platform health filtered to the services their organization touches.
const REFRESH_MS = 60_000;

// Headline copy per verdict. Matched to services/org.service_health's four statuses.
const VERDICT = {
  ok: { pill: "All systems operational", title: "ZoikoStream is operating normally." },
  warn: { pill: "Degraded performance", title: "ZoikoStream is partially degraded." },
  down: { pill: "Service disruption", title: "ZoikoStream is experiencing a disruption." },
  not_configured: { pill: "Not configured", title: "Platform health is not yet reporting." },
};

// Worst-first ordering for picking which open incident to headline. Mirrors ops._RANK.
const SEVERITY_RANK = { down: 3, warn: 2, not_configured: 1, ok: 0 };

// Stage → the service an operator recognises. The status and uptime beside it are live;
// only the name is editorial, because `lifecycle` speaks in stages, not product surfaces.
const SERVICE_NAME = {
  contribute: "Contribution endpoints",
  ingest: "Ingest & live inputs",
  produce: "Transcoding & packaging",
  secure: "Access & playback tokens",
  deliver: "Origin & CDN delivery",
  understand: "Analytics & metering",
  preserve: "Media storage & replay",
  platform: "Management API",
};

// Trust surfaces. Informational by design — the source documents live outside this app, so
// nothing here links to a route that doesn't exist.
const TRUST = [
  { icon: FiShield, title: "Security overview", desc: "Practices, certifications, and reporting.", to: "/organization/settings" },
  { icon: FiGlobe, title: "Data residency", desc: "Where data is processed and stored.", to: "/organization/settings" },
  { icon: FiFileText, title: "Compliance documents", desc: "Request access under NDA." },
  { icon: FiAlertOctagon, title: "Report a vulnerability", desc: "Coordinated disclosure and security.txt." },
];

const th = "px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.1em]";
const td = "px-5 py-3 text-[13px]";

function StatTile({ label, value, tone, title }) {
  return (
    <div className={cx(CONSOLE.panel, "px-4 py-3")}>
      <p className={cx("text-[9px] font-semibold uppercase tracking-[0.14em]", CONSOLE.faint)}>{label}</p>
      <p className={cx("mt-1.5 text-[20px] font-semibold leading-6 tabular-nums", tone || CONSOLE.heading)} title={title}>
        {value}
      </p>
    </div>
  );
}

function StatusSkeleton() {
  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
        <div className="space-y-3">
          <Skeleton variant="line" className="w-40" />
          <Skeleton variant="title" className="w-96" />
          <Skeleton variant="line" className="w-80" />
        </div>
        <div className="grid grid-cols-3 gap-3">
          {Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} variant="block" className="h-[72px]" />)}
        </div>
      </div>
      <Skeleton variant="block" className="h-28" />
      <Skeleton variant="block" className="h-64" />
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton variant="block" className="h-52" />
        <Skeleton variant="block" className="h-52" />
      </div>
    </div>
  );
}

export default function SupportStatus() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data)
  );
  useInterval(reload, REFRESH_MS);

  const stages = useMemo(() => data?.lifecycle || [], [data]);
  const openIncidents = useMemo(
    () => stages.reduce((n, s) => n + (s.open_incidents || 0), 0),
    [stages]
  );
  // The stage carrying an incident, worst first — the page shows the one that matters.
  const incident = useMemo(
    () =>
      stages
        .filter((s) => s.open_incidents > 0)
        .sort((a, b) => (SEVERITY_RANK[b.status] || 0) - (SEVERITY_RANK[a.status] || 0))[0],
    [stages]
  );

  if (loading && !data) return <StatusSkeleton />;

  if (error && !data) {
    return (
      <div className="mx-auto max-w-[1500px]">
        <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 dark:border-rose-500/25 dark:bg-rose-500/10">
          <p className="text-[13px] font-semibold text-rose-700 dark:text-rose-300">Couldn’t load platform status</p>
          <p className="mt-1 text-[12px] text-rose-600 dark:text-rose-400">
            The status feed is unreachable. That does not itself mean the platform is down.
          </p>
          <ConsoleButton variant="secondary" size="sm" className="mt-3" onClick={reload}>
            Try again
          </ConsoleButton>
        </div>
      </div>
    );
  }

  const health = data?.service_health || {};
  const verdict = VERDICT[health.status] || VERDICT.not_configured;
  const region = data?.organization?.region || "Global";

  // Server-stamped, so the page never renders a clock read during render.
  const stamp = data?.generated_at ? new Date(data.generated_at) : null;
  const localTime = stamp?.toLocaleString(undefined, {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
  const utcTime = stamp ? `${stamp.toISOString().slice(11, 16)} UTC` : null;

  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      {/* ── Verdict + posture tiles ────────────────────────────────────────── */}
      <div className="grid gap-5 lg:grid-cols-[1.6fr_1fr] lg:items-start">
        <div className="min-w-0">
          <HealthDot status={health.status || "neutral"} label={verdict.pill} pulse badge />
          <h1 className={cx("mt-3 text-[26px] font-bold leading-tight tracking-tight sm:text-[30px]", CONSOLE.heading)}>
            {verdict.title}
          </h1>
          <p className={cx("mt-1.5 text-[13px]", CONSOLE.muted)}>
            Real-time availability across contribution, delivery, and platform services.
          </p>
          {stamp && (
            <p className={cx("mt-2 text-[11px]", type.mono, CONSOLE.faint)}>
              {localTime} · {utcTime} · updated every 60 seconds
            </p>
          )}
        </div>

        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatTile
            label="90-day uptime"
            value="—"
            tone={CONSOLE.faint}
            title="The availability probe retains 30 days at most — no 90-day window to report"
          />
          <StatTile label="Monitored services" value={stages.length} tone="text-violet-600 dark:text-violet-400" />
          <StatTile
            label="Active incidents"
            value={openIncidents}
            tone={openIncidents ? "text-amber-600 dark:text-amber-400" : CONSOLE.heading}
          />
        </div>
      </div>

      {/* ── Delivery footprint ─────────────────────────────────────────────── */}
      <Panel eyebrow="Global delivery regions" title={`${region} · updated every 60 seconds`}>
        <div className={cx("grid place-items-center rounded-lg px-4 py-10 text-center", CONSOLE.inset)}>
          <p className={cx("text-[13px]", CONSOLE.muted)}>No regional delivery telemetry yet</p>
          <p className={cx("mt-1 max-w-md text-[12px]", CONSOLE.faint)}>
            A per-region availability trend needs an edge-delivery metering pipeline, which this
            deployment does not have. Stage-level availability below is measured.
          </p>
        </div>
      </Panel>

      {/* ── Service health by lifecycle stage ──────────────────────────────── */}
      <section className="space-y-4">
        <h2 className={cx("text-[20px] font-semibold tracking-tight", CONSOLE.heading)}>
          Service health by lifecycle stage
        </h2>

        <LifecycleRail stages={stages} eyebrow="Contribute → Preserve, plus cross-cutting Platform" />

        <Panel flush>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px]">
              <thead>
                <tr className={cx("border-b", CONSOLE.divider)}>
                  <th scope="col" className={cx(th, CONSOLE.faint)}>Service</th>
                  <th scope="col" className={cx(th, CONSOLE.faint)}>Region</th>
                  <th scope="col" className={cx(th, CONSOLE.faint)}>Status</th>
                  {/* Labelled for what it measures: `availability` is the 24h window the
                      probe covers, not the design's 90 days — no 90-day series exists. */}
                  <th scope="col" className={cx(th, "text-right", CONSOLE.faint)}>Uptime (24 h)</th>
                </tr>
              </thead>
              <tbody className={cx("divide-y", CONSOLE.divideY)}>
                {stages.map((s) => (
                  <tr key={s.stage} className="hover:bg-slate-50 dark:hover:bg-white/[0.03]">
                    <td className={cx(td, "font-medium", CONSOLE.heading)}>
                      {SERVICE_NAME[s.stage] || s.label}
                    </td>
                    <td className={cx(td, CONSOLE.muted)}>{region}</td>
                    <td className={td}>
                      <HealthDot status={s.status} />
                    </td>
                    <td className={cx(td, "text-right", type.mono, CONSOLE.body)}>
                      {s.availability == null ? "—" : `${s.availability.toFixed(2)}%`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      </section>

      {/* ── Incident + maintenance ─────────────────────────────────────────── */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Panel
          title="Active incident"
          action={
            incident && (
              <span className={cx("rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider",
                incident.status === "down"
                  ? "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400"
                  : "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400")}
              >
                {incident.status === "down" ? "Major" : "Minor"}
              </span>
            )
          }
        >
          {!incident ? (
            <p className={cx("py-6 text-center text-[13px]", CONSOLE.muted)}>
              No active incidents affecting your services.
            </p>
          ) : (
            <>
              <h3 className={cx("text-[14px] font-semibold leading-snug", CONSOLE.heading)}>
                {incident.detail || `${incident.label} degradation`}
              </h3>
              <p className={cx("mt-1 text-[11px]", type.mono, CONSOLE.faint)}>
                Affects the {incident.label} stage · {incident.open_incidents}{" "}
                {incident.open_incidents === 1 ? "report" : "reports"} open
              </p>
              <p className={cx("mt-3 text-[13px] leading-[20px]", CONSOLE.body)}>
                {incident.availability == null
                  ? "Availability for this stage is not being measured while the incident is open."
                  : `Measured availability for this stage is ${incident.availability.toFixed(2)}% over the last 24 hours.`}
              </p>
              <Link
                to="/organization/dashboard"
                className={cx("mt-3 inline-block rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}
              >
                View affected services →
              </Link>
            </>
          )}
        </Panel>

        <Panel title="Scheduled maintenance">
          {data?.security_support?.maintenance_window ? (
            <div className="flex items-start justify-between gap-3">
              <p className={cx("text-[13px]", CONSOLE.body)}>{data.security_support.maintenance_window}</p>
              <span className="shrink-0 rounded-full bg-blue-100 px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider text-blue-700 dark:bg-blue-500/15 dark:text-blue-400">
                Upcoming
              </span>
            </div>
          ) : (
            <p className={cx("py-6 text-center text-[13px]", CONSOLE.muted)} title="Needs a maintenance calendar (not integrated)">
              No maintenance scheduled.
            </p>
          )}
        </Panel>
      </div>

      {/* ── Trust & evidence ───────────────────────────────────────────────── */}
      <section className="space-y-4">
        <h2 className={cx("text-[20px] font-semibold tracking-tight", CONSOLE.heading)}>Trust &amp; evidence</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {TRUST.map(({ icon: Icon, title, desc, to }) => {
            const body = (
              <>
                <span
                  className={cx("grid h-8 w-8 shrink-0 place-items-center rounded-lg", CONSOLE.inset, CONSOLE.muted)}
                  aria-hidden="true"
                >
                  <Icon className="text-[15px]" />
                </span>
                <div className="min-w-0">
                  <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{title}</p>
                  <p className={cx("mt-0.5 text-[12px] leading-[17px]", CONSOLE.faint)}>{desc}</p>
                </div>
              </>
            );
            const cls = cx("flex items-start gap-3 rounded-xl border p-4", CONSOLE.panel);
            return to ? (
              <Link key={title} to={to} className={cx(cls, CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", focusRing)}>
                {body}
              </Link>
            ) : (
              <div key={title} className={cls}>{body}</div>
            );
          })}
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer className={cx("flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center sm:justify-between", CONSOLE.divider)}>
        <p className={cx("text-[12px]", CONSOLE.faint)}>
          © {new Date().getFullYear()} Zoiko Group. All rights reserved.
        </p>
        <nav className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {/* Only destinations that exist are links; the rest render as text, not 404s. */}
          {["Privacy", "Terms"].map((label) => (
            <span key={label} className={cx("text-[12px]", CONSOLE.faint)}>{label}</span>
          ))}
          <Link to="/organization/settings" className={cx("rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}>
            Trust Center
          </Link>
          <Link to="/organization/dashboard" className={cx("rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}>
            Incident History
          </Link>
          <Link to="/organization/settings" className={cx("rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}>
            Contact
          </Link>
        </nav>
      </footer>
    </div>
  );
}
