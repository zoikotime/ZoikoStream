import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import { Link } from "react-router-dom";
import { FiShield, FiGlobe, FiFileText, FiAlertOctagon, FiHeadphones, FiRefreshCw } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, cx, focusRing, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Skeleton from "../../ui/Skeleton";
import Panel from "../../components/admin/Panel";
import HealthDot from "../../components/admin/HealthDot";
import {
  STATUS_DOT, STATUS_LABEL, STATUS_NOTE, activeIncidents, formatMaintenanceWindow, headlineIncident, headlineOf,
  incidentsCell, maintenanceLabel, maintenanceState, monitoredServices, shownServices, staleNotice,
} from "../../data/supportStatus";

// Support & Status — this organization's view of platform availability.
//
// Service health comes from `service_status` on /organization/overview (services/org.
// customer_status), the payload the Overview page already polls: only the services the
// platform can actually check (Live streaming, Recording & replay, Management API), each
// with a status from real probes, recorded incidents and — for recordings — this
// organization's own recent recording starts. Rules for what may be claimed live in
// data/supportStatus.js. Internal states ("not configured", unprobed stages) are not shown
// here; they stay on the Super Admin console.
const REFRESH_MS = 60_000;

// Trust surfaces. Nothing here links to a route that doesn't exist.
const TRUST = [
  // Each opens its own detail page (pages/organization/TrustEvidence). Both used to land on
  // Settings → Security, which is the change-password form and answers neither question.
  { icon: FiShield, title: "Security overview", desc: "Practices, certifications, and reporting.", to: "/organization/support/security" },
  { icon: FiGlobe, title: "Data residency", desc: "Where data is processed and stored.", to: "/organization/support/data-residency" },
  // These two open the Trust Center's own forms (pages/Trust.jsx: POST /trust/evidence/requests
  // and POST /trust/security/reports). They used to render inert, marked "On request", which
  // read as broken buttons although both flows exist. The second promised a security.txt that
  // this deployment does not serve.
  { icon: FiFileText, title: "Compliance documents", desc: "Request access under NDA.", to: "/trust#evidence" },
  { icon: FiAlertOctagon, title: "Report a vulnerability", desc: "Coordinated disclosure through a protected channel.", to: "/trust#report" },
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

  // A payload without service_status (an older API, or a status subsystem the server isolated
  // after it failed) is "unavailable", never green.
  const serviceStatus = data?.service_status;
  const reported = Boolean(serviceStatus);
  const services = shownServices(serviceStatus);
  // The last refresh failed but an older answer is on screen (useApi keeps it): it stays, as
  // the best information there is, but is never headlined as the current state.
  const stale = Boolean(error && data);
  const verdict = headlineOf(serviceStatus, services, { stale });
  const monitored = monitoredServices(services, reported);
  const notice = serviceStatus?.platform_notice || null;
  // `0` only when the feed actually answered — see data/supportStatus.activeIncidents.
  const incidents = activeIncidents(services, reported, notice);
  const incident = headlineIncident(services, notice);
  // Published windows, or which honest empty state — see data/supportStatus.maintenanceState.
  const maintenance = maintenanceState(data?.maintenance);

  // Server-stamped, so the page never renders a clock read during render.
  const stamp = data?.generated_at ? new Date(data.generated_at) : null;
  const localTime = stamp?.toLocaleString(undefined, {
    weekday: "long", year: "numeric", month: "long", day: "numeric",
    hour: "numeric", minute: "2-digit",
  });
  const utcTime = stamp ? `${stamp.toISOString().slice(11, 16)} UTC` : null;

  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      {/* Breadcrumb and eyebrow only: the service verdict below is this page's heading. */}
      <OrganizationPageHeader breadcrumbs={[["Organization", "/organization/manage"], ["Support & status"]]} eyebrow="Organization console / Support & status" />
      {/* ── Verdict + posture tiles ────────────────────────────────────────── */}
      <div className="grid gap-5 lg:grid-cols-[1.6fr_1fr] lg:items-start">
        <div className="min-w-0">
          <HealthDot status={verdict.dot} label={verdict.pill} pulse badge />
          <h1 className={cx("mt-3 text-[26px] font-bold leading-tight tracking-tight sm:text-[30px]", CONSOLE.heading)}>
            {verdict.title}
          </h1>
          <p className={cx("mt-1.5 text-[13px]", CONSOLE.muted)}>
            Current status of the ZoikoStream services your organization uses.
          </p>
          {stamp && (
            <p className={cx("mt-2 text-[11px]", type.mono, CONSOLE.faint)}>
              {stale ? "Last updated " : ""}{localTime} · {utcTime} ·{" "}
              {stale ? "retrying every 60 seconds" : "updated every 60 seconds"}
            </p>
          )}
          {stale && (
            <p
              role="status"
              data-testid="status-stale"
              className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[12px] text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-300"
            >
              {staleNotice(data?.generated_at)}
            </p>
          )}

          {/* Getting help is the reason most people open this page, and it used to be a
              12px word in the bottom-right footer, below three screens of telemetry. The
              footer link stays as secondary navigation; this is the one you can find. */}
          <div className="mt-4 flex flex-wrap items-center gap-2">
            {/* ui/Button turns an internal href into a react-router <Link>, so this is a
                real navigation to the EXISTING /contact page — no second contact system. */}
            <ConsoleButton href="/contact" size="sm" leftIcon={FiHeadphones}>
              Contact Support
            </ConsoleButton>
            <ConsoleButton variant="secondary" size="sm" leftIcon={FiRefreshCw} onClick={reload}>
              Refresh
            </ConsoleButton>
          </div>
        </div>

        {/* No uptime tile: nothing here measures uptime over 90 days (or any window), and a
            tile that could only ever read "—" was a placeholder on a page about trust. */}
        <div className="grid grid-cols-2 gap-3">
          <StatTile
            label="Monitored services"
            value={monitored.text}
            tone="text-violet-600 dark:text-violet-400"
            title={monitored.title}
          />
          <StatTile
            label="Active incidents"
            value={incidents.text}
            tone={incidents.count ? "text-amber-600 dark:text-amber-400" : CONSOLE.heading}
            title={incidents.title}
          />
        </div>
      </div>

      {/* No "delivery region" panel. Organization.region is a label on the account (it defaults
          to "US East" for every organization) and routes nothing: media goes through one
          LiveKit deployment and one storage bucket whatever it says. Presenting it as delivery
          infrastructure claimed something that does not exist. */}

      {/* ── Service health ─────────────────────────────────────────────────── */}
      <section id="service-health" className="space-y-4">
        <h2 className={cx("text-[20px] font-semibold tracking-tight", CONSOLE.heading)}>
          Service health
        </h2>

        <Panel flush>
          <div className={cx("overflow-x-auto", stale && "opacity-60")} aria-describedby={stale ? "status-stale-note" : undefined}>
            {stale && <span id="status-stale-note" className="sr-only">Last known state, not current.</span>}
            <table className="w-full min-w-[420px]">
              <thead>
                <tr className={cx("border-b", CONSOLE.divider)}>
                  <th scope="col" className={cx(th, CONSOLE.faint)}>Service</th>
                  <th scope="col" className={cx(th, CONSOLE.faint)}>Status</th>
                  {/* A count of recorded incidents. It used to be "Incident-free (24 h)", a
                      percentage that read 100.00% whenever the incident log was empty — a
                      figure about the log, presented as one about the service. */}
                  <th
                    scope="col"
                    className={cx(th, "text-right", CONSOLE.faint)}
                    title="Incidents recorded against each service in the last 24 hours."
                  >
                    Incidents (24 h)
                  </th>
                </tr>
              </thead>
              <tbody className={cx("divide-y", CONSOLE.divideY)}>
                {services.length === 0 ? (
                  <tr>
                    <td colSpan={3} className={cx(td, "py-8 text-center", CONSOLE.muted)}>
                      Live service status is temporarily unavailable.
                    </td>
                  </tr>
                ) : (
                  services.map((s) => {
                    const cell = incidentsCell(s.incidents_24h);
                    return (
                      <tr key={s.id} className="hover:bg-slate-50 dark:hover:bg-white/[0.03]">
                        <td className={td}>
                          <span className={cx("block font-medium", CONSOLE.heading)}>{s.name}</span>
                          {s.summary && (
                            <span className={cx("mt-0.5 block text-[12px]", CONSOLE.faint)}>{s.summary}</span>
                          )}
                          {STATUS_NOTE[s.status] && (
                            <span className={cx("mt-0.5 block text-[12px]", CONSOLE.muted)}>{STATUS_NOTE[s.status]}</span>
                          )}
                        </td>
                        <td className={td}>
                          <HealthDot status={STATUS_DOT[s.status]} label={STATUS_LABEL[s.status]} />
                        </td>
                        <td className={cx(td, "text-right", type.mono, CONSOLE.body)} title={cell.title}>
                          {cell.text}
                        </td>
                      </tr>
                    );
                  })
                )}
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
                incident.severity === "major"
                  ? "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400"
                  : "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400")}
              >
                {incident.severity === "major" ? "Major" : "Minor"}
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
                {incident.title}
              </h3>
              <p className={cx("mt-1 text-[11px]", type.mono, CONSOLE.faint)}>
                Affects {incident.affects} · {incident.open}{" "}
                {incident.open === 1 ? "open incident" : "open incidents"}
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
          {maintenance.kind === "upcoming" ? (
            <ul className={cx("divide-y", CONSOLE.divideY)}>
              {maintenance.items.map((w) => {
                const time = formatMaintenanceWindow(w.starts_at, w.ends_at);
                const label = maintenanceLabel(w);
                return (
                  <li key={w.reference || `${w.title}-${w.starts_at}`} className="py-3 first:pt-0 last:pb-0">
                    <div className="flex items-start justify-between gap-3">
                      <h3 className={cx("text-[14px] font-semibold leading-snug", CONSOLE.heading)}>{w.title}</h3>
                      <span
                        className={cx(
                          "shrink-0 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider",
                          label === "Emergency"
                            ? "bg-rose-100 text-rose-700 dark:bg-rose-500/15 dark:text-rose-400"
                            : label === "Scheduled"
                              ? "bg-blue-100 text-blue-700 dark:bg-blue-500/15 dark:text-blue-400"
                              : "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400"
                        )}
                      >
                        {label}
                      </span>
                    </div>
                    {time && (
                      <p className={cx("mt-1 text-[12px]", CONSOLE.body)}>
                        {time.when}
                        {time.duration && <span className={CONSOLE.faint}> · {time.duration}</span>}
                      </p>
                    )}
                    {w.components?.length > 0 && (
                      <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
                        Affects {w.components.join(", ")}
                        {w.regions?.length > 0 && ` · ${w.regions.join(", ")}`}
                      </p>
                    )}
                    {w.impact && <p className={cx("mt-2 text-[13px] leading-[20px]", CONSOLE.body)}>{w.impact}</p>}
                  </li>
                );
              })}
            </ul>
          ) : (
            // Calm, like the incident panel beside it — and true: "No scheduled maintenance"
            // only when the server says an empty schedule is authoritative.
            <p className={cx("py-6 text-center text-[13px]", CONSOLE.muted)}>{maintenance.text}</p>
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
              // `state.from` lets the detail page's Back return through history instead of
              // pushing a second copy of this page.
              <Link
                key={title}
                to={to}
                state={{ from: "/organization/support" }}
                aria-label={`${title}: ${desc}`}
                className={cx(cls, "cursor-pointer", CONSOLE.panelHover, "transition-colors duration-150 motion-reduce:transition-none", focusRing)}
              >
                {body}
              </Link>
            ) : (
              // A card with no destination (none today) renders inert, dimmed and labelled, so
              // the difference from the cards that DO navigate is visible rather than
              // discovered by clicking.
              <div key={title} className={cx(cls, "opacity-60")}>
                {body}
                <span className={cx("ml-auto shrink-0 self-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider", CONSOLE.inset, CONSOLE.faint)}>
                  On request
                </span>
              </div>
            );
          })}
        </div>
      </section>

      {/* ── Footer ─────────────────────────────────────────────────────────── */}
      <footer className={cx("flex flex-col gap-3 border-t pt-5 sm:flex-row sm:items-center sm:justify-between", CONSOLE.divider)}>
        <p className={cx("text-[12px]", CONSOLE.faint)}>
          © {new Date().getFullYear()} Zoiko Group. All rights reserved.
        </p>
        {/* The links are inline-flex items-center because in the Android app the 44px
            touch-target floor (index.css) makes each one 44px tall with its text pinned to the
            top, 13px above the plain-text items beside it. Centering keeps the full target and
            puts the text back on the row. No effect on web, where links are text-height. */}
        <nav className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {/* Only destinations that exist are links; the rest render as text, not 404s. */}
          {["Privacy", "Terms"].map((label) => (
            <span key={label} className={cx("text-[12px]", CONSOLE.faint)}>{label}</span>
          ))}
          {/* The public Trust Center (pages/Trust.jsx), not Settings → Security — that is the
              change-password form, the same wrong destination the trust cards had. */}
          <Link to="/trust" className={cx("inline-flex items-center rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}>
            Trust Center
          </Link>
          <Link to="/organization/dashboard" className={cx("inline-flex items-center rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}>
            Incident History
          </Link>
          {/* The real contact form, not the settings page — this footer link has pointed at
              Settings since before /contact existed. */}
          <Link to="/contact" className={cx("inline-flex items-center rounded text-[12px]", CONSOLE.faint, "hover:text-slate-900 dark:hover:text-white", focusRing)}>
            Contact
          </Link>
        </nav>
      </footer>
    </div>
  );
}
