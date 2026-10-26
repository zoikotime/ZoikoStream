import { Link } from "react-router-dom";
import {
  FiAlertTriangle, FiArrowRight, FiCalendar, FiChevronRight, FiClock, FiFilm, FiFolder, FiPlay, FiPlus,
  FiRadio, FiRefreshCw, FiShield,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import useInterval from "../../hooks/useInterval";
import { CONSOLE, actionStops, cx, focusRing } from "../../ui/tokens";
import Skeleton from "../../ui/Skeleton";
import Badge from "../../ui/Badge";
import { ConsoleButton } from "../../ui/Button";
import { useAuth } from "../../auth/AuthContext";
import { displayStatusOf, fmtDate, statusMeta } from "../../data/events";
import { isAdminRole } from "../../components/Dashboard/orgNav";
import { useOrgScope } from "../../components/organization/orgScope";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import { DarkCard, IconChip, KpiRow, KpiTile, Panel, PanelLink } from "../../components/organization/console/ConsoleUI";
import AnalyticsOverview from "../../components/organization/dashboard/AnalyticsOverview";
import { greetingName } from "../../components/organization/dashboard/dashboardConfig";
import useDashboardData from "../../components/organization/dashboard/useDashboardData";
import { fmtZoned, fmtZonedShort } from "../../data/eventTime";

// How often the live figures re-poll. Only the sessions/counts request is on this timer —
// the analytics aggregate is not (see useDashboardData).
const REFRESH_MS = 30_000;

function DashboardSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="line" className="w-80" />
      </div>
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} variant="block" className="h-[110px]" />
        ))}
      </div>
      <div className="grid gap-5 xl:grid-cols-2">
        <Skeleton variant="block" className="h-72" />
        <Skeleton variant="block" className="h-72" />
      </div>
    </div>
  );
}

const SEVERITY_TONE = { critical: "rose", warning: "amber", warn: "amber", info: "violet" };

// The priority action queue (ORG-01): the attention items the server ranked, blockers first.
// Empty says so in words; it is never hidden.
function PriorityQueue({ items, isAdmin, onRefresh, refreshing }) {
  return (
    <Panel
      title="Priority action queue"
      action={
        <span className="inline-flex items-center gap-2">
          <span className={cx("text-[12px] font-semibold", CONSOLE.link)}>{items.length} item{items.length === 1 ? "" : "s"}</span>
          {/* The live figures re-poll on their own; this asks now. Here rather than in the page
              header, which the ORG-01 frame keeps to the month and Create Event. */}
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="Refresh live figures"
            title="Refresh live figures"
            className={cx("grid h-8 w-8 place-items-center rounded-lg text-[14px] transition-colors disabled:opacity-50", CONSOLE.muted, "hover:bg-slate-100 dark:hover:bg-white/10", focusRing)}
          >
            <FiRefreshCw aria-hidden="true" className={refreshing ? "animate-spin motion-reduce:animate-none" : undefined} />
          </button>
        </span>
      }
    >
      {items.length === 0 ? (
        <div className="py-6 text-center">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>Nothing needs attention</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>Credentials, entitlements, events and invitations are all in good standing.</p>
        </div>
      ) : (
        <ul className={cx("divide-y border-t", CONSOLE.divider, CONSOLE.divideY)}>
          {items.map((it) => (
            <li key={it.id} className="flex items-center gap-3 py-3">
              <IconChip icon={it.severity === "critical" ? FiAlertTriangle : it.kind === "readiness" ? FiClock : FiShield} tone={SEVERITY_TONE[it.severity] || "violet"} size="sm" />
              <div className="min-w-0 flex-1">
                <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{it.title}</p>
                <p className={cx("truncate text-[12px]", CONSOLE.faint)}>{it.detail}</p>
              </div>
              {isAdmin && it.to && (
                <Link to={it.to} className={cx("shrink-0 whitespace-nowrap rounded text-[12px] font-semibold", CONSOLE.link, focusRing)}>
                  {it.action || "Review"} →
                </Link>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// The ORG-01 hero's orbit lines and glow — pure decoration, hidden from assistive technology.
function Orbits() {
  return (
    <svg aria-hidden="true" viewBox="0 0 420 260" className="pointer-events-none absolute -right-10 top-1/2 h-[120%] -translate-y-1/2 opacity-70">
      <defs>
        <radialGradient id="zk-orbit-glow" cx="55%" cy="45%" r="55%">
          <stop offset="0%" stopColor="#c026d3" stopOpacity="0.45" />
          <stop offset="60%" stopColor="#7c3aed" stopOpacity="0.18" />
          <stop offset="100%" stopColor="#16213f" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="230" cy="130" rx="190" ry="120" fill="url(#zk-orbit-glow)" />
      {[0, 1, 2, 3].map((i) => (
        <ellipse key={i} cx="250" cy="130" rx={130 - i * 22} ry={58 - i * 10} fill="none"
          stroke="#c4b5fd" strokeOpacity={0.28 - i * 0.04} strokeWidth="1.2" transform="rotate(-22 250 130)" />
      ))}
    </svg>
  );
}

// ORG-01's hero: the next event that needs preparing, with how many readiness items are open.
function NextPriority({ event, readiness, isAdmin }) {
  if (!event) {
    return (
      <DarkCard className="flex min-h-[15rem] flex-col justify-center">
        <p className="text-[11px] font-bold uppercase tracking-[0.16em] text-white/60">Next priority event</p>
        <p className="mt-2 text-[22px] font-bold">Nothing scheduled in the next 30 days</p>
        <p className="mt-1 text-[13px] text-white/70">
          {isAdmin ? "Create an event to start preparing it." : "You have not been assigned any events."}
        </p>
        {isAdmin && (
          <Link to="/organization/events?create=true" className={cx("mt-5 inline-flex w-fit items-center gap-2 rounded-xl bg-gradient-to-r px-4 py-2.5 text-[13px] font-semibold", actionStops, focusRing)}>
            <FiPlus aria-hidden="true" /> Create event
          </Link>
        )}
      </DarkCard>
    );
  }
  const st = statusMeta(displayStatusOf(event));
  const open = readiness?.failing?.length ?? null;
  return (
    <DarkCard className="flex min-h-[14rem] flex-col justify-center bg-none! bg-[#16213f]!">
      <Orbits />
      {open != null && (
        <span className={cx("mb-3 inline-flex w-fit items-center gap-2 rounded-full border px-3 py-1 text-[11px] font-bold uppercase tracking-[0.12em]",
          open ? "border-amber-300/40 bg-amber-400/10 text-amber-200" : "border-emerald-300/40 bg-emerald-400/10 text-emerald-200")}>
          {open ? <FiAlertTriangle aria-hidden="true" /> : <FiShield aria-hidden="true" />}
          {open ? `${open} readiness item${open === 1 ? "" : "s"}` : "Readiness checks pass"}
        </span>
      )}
      <p className="text-[24px] font-bold leading-tight tracking-tight">{event.title || "Untitled event"}</p>
      <p className="mt-1 text-[13px] text-white/75">
        Next priority event · {fmtZoned(event.start_time, event.timezone)}
      </p>
      <p className="mt-3 flex flex-wrap items-center gap-4 text-[12.5px] text-white/80">
        <span className="inline-flex items-center gap-1.5"><FiCalendar aria-hidden="true" /> {st.label}</span>
        {readiness?.readiness_state && (
          <span className="inline-flex items-center gap-1.5"><FiShield aria-hidden="true" /> Readiness {readiness.readiness_state.replace(/_/g, " ")}</span>
        )}
      </p>
      {isAdmin && (
        <Link
          to={`/organization/events/${event.id}`}
          className={cx("mt-5 inline-flex w-fit items-center gap-2 rounded-lg bg-gradient-to-r px-3.5 py-2 text-[12.5px] font-semibold shadow-[0_10px_22px_-10px_rgba(217,70,239,0.8)]", actionStops, focusRing)}
        >
          Open Event Workspace <FiChevronRight aria-hidden="true" />
        </Link>
      )}
    </DarkCard>
  );
}

const PIPELINE_ACTION = { live: "Monitor", degraded: "Monitor", ended: "Replay" };

function Pipeline({ rows, total, isAdmin }) {
  const more = total != null && total > rows.filter((r) => r.kind === "upcoming").length
    ? total - rows.filter((r) => r.kind === "upcoming").length : 0;
  return (
    <Panel title="Event pipeline" action={isAdmin && <PanelLink to="/organization/events">View all events</PanelLink>} flush>
      {rows.length === 0 ? (
        <div className="border-t border-slate-100 px-5 py-8 text-center dark:border-white/[0.08]">
          <p className={cx("text-[13px] font-medium", CONSOLE.body)}>No upcoming events</p>
          <p className={cx("mt-1 text-[12px]", CONSOLE.faint)}>
            {isAdmin ? "Schedule an event to see it here." : "You have not been assigned any events."}
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto border-t border-slate-100 dark:border-white/[0.08]">
          <table className="w-full min-w-[560px] text-left">
            <thead className={cx("bg-slate-50/70 text-[11px] uppercase tracking-wider dark:bg-white/[0.03]", CONSOLE.muted)}>
              <tr>
                <th scope="col" className="px-5 py-2.5 font-semibold">Event</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Schedule</th>
                <th scope="col" className="px-3 py-2.5 font-semibold">Status</th>
                <th scope="col" className="px-5 py-2.5 font-semibold"><span className="sr-only">Open</span></th>
              </tr>
            </thead>
            <tbody className={cx("divide-y", CONSOLE.divideY)}>
              {rows.map(({ event: e, kind }) => {
                const st = statusMeta(displayStatusOf(e));
                const action = PIPELINE_ACTION[e.status] || "Open";
                const to = kind === "ended" ? `/organization/events/${e.id}/replay` : kind === "live" ? `/organization/events/${e.id}/live` : `/organization/events/${e.id}`;
                return (
                  <tr key={e.id} data-kind={kind}>
                    <td className="px-5 py-2.5">
                      <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{e.title || "Untitled event"}</p>
                      <p className={cx("text-[11.5px]", CONSOLE.faint)}>
                        {kind === "live" ? "Live event" : kind === "ended" ? "Recently ended" : e.expected_audience != null ? (
                          <><span>{e.expected_audience.toLocaleString()}</span> <span>Expected</span></>
                        ) : e.visibility === "private" ? "Private event" : "Planned session"}
                      </p>
                    </td>
                    <td className={cx("whitespace-nowrap px-3 py-2.5 text-[12.5px]", CONSOLE.body)}>{e.start_time ? fmtZonedShort(e.start_time, e.timezone) : "Date not set"}</td>
                    <td className="px-3 py-2.5"><Badge tone={st.tone} dot>{st.label}</Badge></td>
                    <td className="px-5 py-2.5 text-right">
                      {isAdmin && (
                        <Link to={to} className={cx("inline-flex items-center gap-1 rounded text-[12.5px] font-semibold", CONSOLE.link, focusRing)}>
                          {action} <FiChevronRight aria-hidden="true" />
                        </Link>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {more > 0 && (
            <p className={cx("border-t px-5 py-2.5 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              {isAdmin ? <Link to="/organization/events?status=upcoming" className={CONSOLE.link}>{more} more upcoming events</Link> : `${more} more upcoming events`}
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}

// The 700 step: at 12px the 600s measure 3.2–4.0:1 on white (amber, emerald, sky), under AA.
const MEDIA_STATE = {
  ready: ["Ready", "text-emerald-700 dark:text-emerald-300"],
  processing: ["Processing", "text-sky-700 dark:text-sky-300"],
  storage_unavailable: ["Storage unavailable", "text-amber-700 dark:text-amber-300"],
  failed: ["Failed", "text-rose-700 dark:text-rose-300"],
};

function MediaPanel({ isAdmin }) {
  const media = useApi(() =>
    Promise.all([
      api.get("/organization/recordings").then((r) => r.data),
      api.get("/organization/recordings/summary").then((r) => r.data),
    ]).then(([items, summary]) => ({ items: Array.isArray(items) ? items : [], summary: summary || {} }))
  );
  const items = media.data?.items || [];
  const s = media.data?.summary || {};
  return (
    <Panel title="Media & recordings" action={isAdmin && <PanelLink to="/organization/recordings">Open library</PanelLink>}>
      {media.error ? (
        <p className={cx("text-sm", CONSOLE.muted)}>Media status couldn&rsquo;t be read.</p>
      ) : !media.data ? (
        <p className={cx("text-sm", CONSOLE.faint)}>Loading…</p>
      ) : (
        <ul className={cx("divide-y border-t", CONSOLE.divider, CONSOLE.divideY)}>
          {items.slice(0, 2).map((r) => {
            const [label, cls] = MEDIA_STATE[r.state] || ["Recorded", CONSOLE.muted];
            return (
              <li key={r.id} className="flex items-center gap-3 py-2.5">
                <IconChip icon={FiPlay} tone="violet" size="sm" />
                <div className="min-w-0 flex-1">
                  <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{r.title || "Untitled event"}</p>
                  <p className={cx("truncate text-[12px]", CONSOLE.faint)}>{r.started_at ? `Recorded · ${fmtDate(r.started_at)}` : "Recorded"}</p>
                </div>
                <span className={cx("text-[12px] font-semibold", cls)}>{label}</span>
              </li>
            );
          })}
          {s.in_progress > 0 && (
            <li className="flex items-center gap-3 py-2.5">
              <IconChip icon={FiFilm} tone="amber" size="sm" />
              <div className="min-w-0 flex-1">
                <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{s.in_progress} recording{s.in_progress === 1 ? "" : "s"} in progress</p>
                <p className={cx("text-[12px]", CONSOLE.faint)}>Capture ongoing; asset not ready</p>
              </div>
              <span className="text-[12px] font-semibold text-amber-700 dark:text-amber-300">Recording</span>
            </li>
          )}
          <li className="flex items-center gap-3 py-2.5">
            <IconChip icon={FiFolder} tone="slate" size="sm" />
            <div className="min-w-0 flex-1">
              <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>Event media library</p>
              <p className={cx("text-[12px]", CONSOLE.faint)}>
                {s.library_total != null ? `${s.library_total} captured asset${s.library_total === 1 ? "" : "s"}` : "—"}
                {s.failed_attempts ? ` · ${s.failed_attempts} attempt${s.failed_attempts === 1 ? "" : "s"} not captured` : ""}
              </p>
            </div>
            {isAdmin && <PanelLink to="/organization/recordings">Open</PanelLink>}
          </li>
        </ul>
      )}
      <div className={cx(CONSOLE.inset, "mt-3 flex items-center justify-between gap-3 px-4 py-3")}>
        <div>
          <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>Operational visibility, by role</p>
          <p className={cx("text-[12px]", CONSOLE.faint)}>Permissions and plan checks are enforced server-side</p>
        </div>
        <FiShield aria-hidden="true" className={cx("text-[20px]", CONSOLE.muted)} />
      </div>
    </Panel>
  );
}

/**
 * The organization's home screen (ZST-UX-NAV-001 §8; ZST-ENG-DASH-001 ORG-01): upcoming, live,
 * what needs action and what is replay-ready; the next priority event and the action queue;
 * the event pipeline and media. Every figure comes from the API; a figure that could not be
 * read is "—" with its reason, never 0.
 */
export default function OrganizationDashboard() {
  const { user } = useAuth();
  const { state } = useOrgScope();
  // Any member may open the Overview; only an admin may open the pages its cards lead to, so
  // for everyone else the cards are read-only figures rather than links that bounce.
  const isAdmin = isAdminRole(state?.user?.role || user?.role);
  const linkTo = (to) => (isAdmin ? to : undefined);
  const { core, analytics, range, setRange } = useDashboardData();

  // Quietly current, with no countdown chrome.
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
      <div className="rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 dark:border-rose-500/25 dark:bg-rose-500/10">
        <p className="text-[13px] font-semibold text-rose-700 dark:text-rose-300">Couldn’t load your dashboard</p>
        <p className="mt-1 text-[12px] text-rose-600 dark:text-rose-400">{diagnosis}</p>
        <ConsoleButton variant="secondary" size="sm" className="mt-3" onClick={core.reload}>
          Try again
        </ConsoleButton>
      </div>
    );
  }

  const overview = core.data?.overview || {};
  const sessions = overview.sessions || {};
  const attention = overview.attention || [];

  // `?? null` matters: a rejected /events request and a genuine count of zero are different
  // answers, and the KPI tile renders them differently ("—" plus a reason vs. "0").
  const upcomingTotal = core.data?.upcoming?.total ?? null;
  const upcomingItems = core.data?.upcoming?.items || [];
  const liveCount = sessions.live ?? null;
  const startingSoon = sessions.starting_soon || 0;
  const critical = attention.filter((a) => a.severity === "critical").length;
  // Replay-ready recordings: captured and playable (services/org media summary). Absent from
  // an older server build, which reads as "—", never as 0.
  const media = overview.media_assets;
  const replayReady = media?.ready ?? null;

  // The pipeline: on air first, then what is coming, then what just ended — each once.
  const seen = new Set();
  const pipeline = [
    ...(core.data?.onAir?.items || []).map((e) => ({ event: e, kind: "live" })),
    ...upcomingItems.map((e) => ({ event: e, kind: "upcoming" })),
    ...(core.data?.recent?.items || []).map((e) => ({ event: e, kind: "ended" })),
  ].filter(({ event: e }) => (seen.has(e.id) ? false : (seen.add(e.id), true)));

  const next = upcomingItems[0] || null;
  const nextReadiness = next ? (overview.upcoming_events || []).find((u) => u.id === next.id) : null;
  const name = greetingName(user);
  const month = new Date();
  const monthKey = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, "0")}`;

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        eyebrow="Organization console / Overview"
        title="Organization overview"
        subtitle={`${name ? `Welcome back, ${name}. ` : ""}Your events, production priorities and media operations — in one place.`}
        actions={
          <>
            {isAdmin && (
              <>
                <ConsoleButton variant="secondary" size="sm" leftIcon={FiCalendar} href={`/organization/events?view=calendar&month=${monthKey}`}>
                  {month.toLocaleDateString("en-US", { month: "short", year: "numeric" })}
                </ConsoleButton>
                <ConsoleButton size="sm" leftIcon={FiPlus} href="/organization/events?create=true">Create Event</ConsoleButton>
              </>
            )}
          </>
        }
      />

      <KpiRow>
        <KpiTile
          label="Upcoming events"
          value={upcomingTotal != null ? upcomingTotal.toLocaleString() : null}
          caption={
            upcomingTotal == null
              ? "Couldn’t load events"
              : upcomingItems.length
              ? `Next 30 days · next on ${fmtDate(upcomingItems[0].start_time)}`
              : "Nothing in the next 30 days"
          }
          icon={FiCalendar}
          tone="violet"
          to={linkTo("/organization/events?view=calendar")}
        />
        <KpiTile
          label="Live now"
          value={liveCount != null ? liveCount.toLocaleString() : null}
          caption={liveCount ? "On air now" : startingSoon ? `${startingSoon} starting within 30 min` : "Nothing broadcasting"}
          icon={FiRadio}
          tone="pink"
          to={linkTo("/organization/events?status=on_air")}
        />
        <KpiTile
          label="Needs Action"
          value={attention.length.toLocaleString()}
          caption={attention.length ? (critical ? `${critical} critical · blockers first` : "Readiness, access and account items") : "Nothing needs attention"}
          icon={FiAlertTriangle}
          tone="sky"
        />
        <KpiTile
          label="Replay ready"
          value={replayReady != null ? replayReady.toLocaleString() : null}
          caption={
            replayReady == null
              ? "Media status unavailable"
              : media.processing
              ? `${media.processing} still processing`
              : replayReady
              ? "Captured and playable"
              : "No recording ready yet"
          }
          icon={FiPlay}
          tone="green"
          to={linkTo("/organization/recordings")}
        />
      </KpiRow>

      {/* Priority before analytics (ZST-UX-NAV-001 §8.1: "the action queue ranks production
          blockers and imminent events ahead of generic analytics"). */}
      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <NextPriority event={next} readiness={nextReadiness} isAdmin={isAdmin} />
        <section aria-label="Requires attention">
          <PriorityQueue items={attention} isAdmin={isAdmin} onRefresh={core.reload} refreshing={core.loading} />
        </section>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
        <Pipeline rows={pipeline} total={upcomingTotal} isAdmin={isAdmin} />
        <MediaPanel isAdmin={isAdmin} />
      </div>

      <AnalyticsOverview
        data={analytics.data}
        loading={analytics.loading}
        error={analytics.error}
        range={range}
        onRange={setRange}
        onRetry={analytics.reload}
      />

      {isAdmin && (
        <p className={cx("text-[12px]", CONSOLE.faint)}>
          Looking for sessions, usage, credentials or platform status? Broadcast sessions live in each
          event&rsquo;s Live Control, reached from{" "}
          <Link to="/organization/events" className={CONSOLE.link}>Events</Link>; usage in{" "}
          <Link to="/organization/billing" className={CONSOLE.link}>Plan, usage &amp; billing</Link>; credentials in{" "}
          <Link to="/organization/manage" className={CONSOLE.link}>Organization &rsaquo; Integrations</Link>; and platform status in{" "}
          <Link to="/organization/support" className={CONSOLE.link}>Support</Link>.{" "}
          <FiArrowRight aria-hidden="true" className="inline" />
        </p>
      )}
    </div>
  );
}
