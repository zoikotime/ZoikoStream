import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, { errMsg } from "../../api";
import { fmtDateTime, statusMeta } from "../../data/events";
import { WEB_APP_URL } from "../../platform";
import { openExternal } from "../../native/bridge";
import {
  Activity, ChevronRight, ExternalLink, LayoutDashboard, Loader2, Radio, ShieldCheck,
} from "lucide-react";

// MobileHome — the store build's real home for an org admin.
//
// ── WHY THIS PAGE EXISTS ─────────────────────────────────────────────────────────────────
// /events/mine is the honest landing for a CONTRIBUTOR: it lists the broadcasts they were
// put on and opens their console. For an org_admin it is mostly an apology — the codebase
// answer to "where is my console?" is "on the web", and until this page existed the app
// said so with one notice above an empty list. Every figure an admin actually opens the
// app to check — is anything live, what is next, is the audience flowing — comes from
// /organization/overview, which authorizes with get_my_org (any member of the
// organization), so the API already allows a phone to read it. What was missing was a
// screen built at phone width that asks for it.
//
// ── WHAT IT IS NOT ──────────────────────────────────────────────────────────────────────
// Not a port of the console, and not a new route into one. The store build deliberately
// ships no /organization/* pages (the HAS_CONSOLES gate in App.jsx): they are operator
// surfaces built for a desk, and billing hands off to Stripe in a way Play's payments
// policy prohibits inside an app. Everything here is read-only status pulled from ONE
// endpoint the client already calls on the web, and the console itself stays one tap away
// in the device browser — the handoff the payments policy reasoning requires.

const STATUS_CHIP = {
  live: "border-rose-200 bg-rose-50 text-rose-700",
  degraded: "border-amber-200 bg-amber-50 text-amber-800",
  armed: "border-amber-200 bg-amber-50 text-amber-800",
  scheduled: "border-sky-200 bg-sky-50 text-sky-800",
  published: "border-sky-200 bg-sky-50 text-sky-800",
  processing: "border-sky-200 bg-sky-50 text-sky-800",
  replay_ready: "border-emerald-200 bg-emerald-50 text-emerald-700",
  ended: "border-slate-200 bg-slate-100 text-slate-600",
  cancelled: "border-slate-200 bg-slate-100 text-slate-600",
};

// The two counters worth a tap without opening the console. Everything else on the
// overview payload is either desk-scale or duplicated by the web console; a phone wants
// the pulse, not the spreadsheet.
const PULSE = [
  { key: "live", label: "Live now", Icon: Radio, tone: "text-rose-600" },
  { key: "starting_soon", label: "Starting soon", Icon: Activity, tone: "text-sky-600" },
  { key: "current_audience", label: "Watching", Icon: Activity, tone: "text-slate-700" },
];

function Stat({ value }) {
  // The console's own convention (pages/organization/Dashboard): a missing producer is
  // "—", visibly different from a real zero. Never render `undefined` as a number.
  return <span className="text-[22px] font-bold tabular-nums leading-none text-slate-900">{value ?? "—"}</span>;
}

function StatusChip({ status }) {
  const meta = statusMeta(status);
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STATUS_CHIP[meta.label ? status : "ended"] || STATUS_CHIP.ended}`}
    >
      {meta.label}
    </span>
  );
}

export default function MobileHome() {
  const [orgName, setOrgName] = useState(null);
  const [sessions, setSessions] = useState(null);
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      // One call for the pulse, one for the next events. /events is the same envelope the
      // org Events page reads (items + total); status filter left off so an ARMED or
      // LIVE row is not hidden behind an upcoming-only query.
      const [overview, upcoming] = await Promise.allSettled([
        api.get("/organization/overview", { params: { range: "24h" } }).then((r) => r.data),
        api
          .get("/events", {
            params: { date_from: new Date().toISOString(), sort_by: "start_time", order: "asc", page_size: 5 },
          })
          .then((r) => r.data),
      ]);

      if (overview.status === "rejected") throw overview.reason;
      // Supporting figure: a failed /events greys out one section, not the page.
      setEvents(upcoming.status === "fulfilled" ? upcoming.value?.items || [] : []);
      setSessions(overview.value?.sessions || null);
      setOrgName(overview.value?.organization?.name || null);
      setError(null);
    } catch (err) {
      setError(errMsg(err, "Couldn't load your organization's status."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  return (
    <main className="min-h-screen bg-slate-50 pb-10">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-6">
          <Link to="/" aria-label="ZoikoStream home">
            <span className="text-[15px] font-bold tracking-tight">ZoikoStream</span>
          </Link>
          {orgName && (
            <span className="max-w-[50%] truncate text-[13px] font-medium text-white/50">{orgName}</span>
          )}
        </div>
        <div className="mx-auto max-w-3xl px-6 pb-8 pt-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            Your organization
          </p>
          <h1 className="mt-3 text-[1.7rem] font-bold leading-tight tracking-tight">
            {loading ? "Loading&hellip;" : error ? "Organization status" : "At a glance"}
          </h1>
          <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-white/60">
            The live pulse of your organization, on the device in your pocket. Scheduling,
            members, recordings, analytics and billing stay on the{" "}
            <span className="font-semibold text-white/80">web console</span>.
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        {error && (
          <div className="rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4">
            <p className="text-[14px] leading-relaxed text-rose-900">{error}</p>
            <button
              type="button"
              onClick={() => { setLoading(true); setError(null); load(); }}
              className="mt-3 rounded-lg border border-rose-300 bg-white px-3 py-1.5 text-[13px] font-semibold text-rose-700"
            >
              Try again
            </button>
          </div>
        )}

        {loading && (
          <p className="flex items-center gap-2 text-[14px] text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading your organization&rsquo;s status&hellip;
          </p>
        )}

        {!loading && !error && (
          <>
            {/* The pulse. Three figures, one tap deep into the web console for the rest. */}
            <div className="grid grid-cols-3 gap-3">
              {PULSE.map(({ key, label, Icon, tone }) => (
                <div key={key} className="rounded-2xl border border-slate-200 bg-white px-4 py-4">
                  <Icon className={`h-4 w-4 ${tone}`} aria-hidden="true" />
                  <div className="mt-2"><Stat value={sessions ? sessions[key] : undefined} /></div>
                  <p className="mt-1 text-[12px] font-medium text-slate-500">{label}</p>
                </div>
              ))}
            </div>

            {/* Next events. Read-only on purpose: a phone manages nothing. */}
            <section aria-label="Next events">
              <h2 className="text-[15px] font-semibold text-slate-900">Next events</h2>
              {events.length === 0 ? (
                <p className="mt-3 rounded-2xl border border-slate-200 bg-white px-5 py-6 text-center text-[14px] text-slate-600">
                  Nothing scheduled. Create the next event from the web console.
                </p>
              ) : (
                <ul className="mt-3 space-y-3">
                  {events.map((ev) => (
                    <li key={ev.id} className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4">
                      <div className="min-w-0">
                        <p className="truncate text-[15px] font-semibold text-slate-900">{ev.title}</p>
                        <p className="mt-0.5 text-[13px] text-slate-500">{fmtDateTime(ev.start_time)}</p>
                      </div>
                      <StatusChip status={ev.status} />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}

        {/* The handoff, same destination ConsoleOnWebNotice offers. Rendered for any
            signed-in org member who reaches this page — the console behind the link
            authorizes on the web side, so this button does not pre-check roles. */}
        <div className="rounded-2xl border border-slate-200 bg-white px-5 py-4">
          <div className="flex items-start gap-3">
            <LayoutDashboard className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" />
            <div className="min-w-0">
              <h2 className="text-[15px] font-semibold text-slate-900">Your organization console is on the web</h2>
              <p className="mt-1 text-[14px] leading-relaxed text-slate-600">
                Events, recordings, members, analytics and billing are built for a full screen,
                so they live on the web app rather than here.
              </p>
              <button
                type="button"
                onClick={() => openExternal(`${WEB_APP_URL}/organization/dashboard`)}
                className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-[13px] font-semibold text-slate-700 active:bg-slate-100"
              >
                Open in your browser
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
              </button>
            </div>
          </div>
        </div>

        {/* The contributor surface still exists and still matters: this same account may
            also be assigned to run broadcasts. Point at it rather than duplicate it. */}
        <Link
          to="/events/mine"
          className="flex items-center justify-between gap-3 rounded-2xl border border-slate-200 bg-white px-5 py-4"
        >
          <div className="flex items-center gap-3">
            <ShieldCheck className="h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" />
            <div>
              <h2 className="text-[15px] font-semibold text-slate-900">Your events</h2>
              <p className="text-[13px] text-slate-500">Broadcasts you&rsquo;re assigned to run</p>
            </div>
          </div>
          <ChevronRight className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
        </Link>
      </div>
    </main>
  );
}
