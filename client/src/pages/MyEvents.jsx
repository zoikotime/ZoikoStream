import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, { errMsg } from "../api";
import { CalendarClock, Loader2, Mic, Radio, ShieldCheck, Users } from "lucide-react";
import { Logo } from "../ui";

// MyEvents — "which events am I actually on?"
//
// The landing page for a host, moderator or speaker PERSONA account. Those account roles say
// what someone does; they do not say which event they run. Before this page existed, the only
// mapping was host -> /host/dashboard with no event, and the Producer Console filled the gap
// by silently attaching to an arbitrary event from the organization — which is how a login
// ended in a read-only console for somebody else's broadcast.
//
// Everything here comes from GET /api/events/assignments/mine, which returns EventAssignment
// rows for the signed-in user and nothing else. The list is what the server says it is; no
// account role is expanded into it, and nothing is read from storage.

const CONSOLE = {
  host: { path: "/host/dashboard", label: "Open Producer Console", Icon: Radio },
  moderator: { path: "/moderator/dashboard", label: "Open moderation console", Icon: Users },
  speaker: { path: "/speaker/backstage", label: "Open backstage", Icon: Mic },
};

const ROLE_LABEL = { host: "Host", moderator: "Moderator", speaker: "Speaker" };

const STATUS_TONE = {
  live: "border-rose-200 bg-rose-50 text-rose-700",
  degraded: "border-amber-200 bg-amber-50 text-amber-800",
  armed: "border-amber-200 bg-amber-50 text-amber-800",
  scheduled: "border-sky-200 bg-sky-50 text-sky-800",
  published: "border-sky-200 bg-sky-50 text-sky-800",
  ended: "border-slate-200 bg-slate-100 text-slate-600",
  cancelled: "border-slate-200 bg-slate-100 text-slate-600",
};

const fmt = (iso) => {
  if (!iso) return "Not scheduled";
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short", day: "numeric", month: "short", hour: "numeric", minute: "2-digit",
    });
  } catch {
    return iso;
  }
};

export default function MyEvents() {
  const [items, setItems] = useState([]);
  const [accountRole, setAccountRole] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data } = await api.get("/events/assignments/mine");
      setItems(data.items || []);
      setAccountRole(data.account_role || null);
      setError(null);
    } catch (err) {
      setError(errMsg(err, "Couldn't load your events."));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
  }, [load]);

  // There is deliberately NO auto-open here, not even for a single assignment.
  //
  // An earlier version navigated straight into the console when exactly one assignment
  // existed. That looked like a convenience and was in fact the bug wearing a different hat:
  // a past assignment still became the user's landing page, and a member who asked for
  // /organization/dashboard could be carried out of it by an effect they never triggered.
  // Entering a Producer Console is always a click now.

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-4 px-6 py-6">
          <Link to="/" aria-label="ZoikoStream home"><Logo height="h-7" /></Link>
        </div>
        <div className="mx-auto max-w-4xl px-6 pb-10 pt-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            Your events
          </p>
          <h1 className="mt-3 flex items-start gap-3 text-[1.7rem] font-bold leading-tight tracking-tight sm:text-[2.1rem]">
            <CalendarClock className="mt-1 h-6 w-6 shrink-0 text-white/80" aria-hidden="true" />
            Events you&rsquo;re assigned to
          </h1>
          <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-white/60">
            Pick an event to open its console. You will only see events an organization admin
            has put you on.
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-4xl px-6 py-10">
        {loading && (
          <p className="flex items-center gap-2 text-[14px] text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading your events&hellip;
          </p>
        )}

        {error && (
          <p className="rounded-2xl border border-rose-200 bg-rose-50 px-5 py-4 text-[14px] leading-relaxed text-rose-900">
            {error}
          </p>
        )}

        {!loading && !error && items.length === 0 && (
          <div className="rounded-2xl border border-slate-200 bg-white px-6 py-10 text-center">
            <ShieldCheck className="mx-auto h-8 w-8 text-slate-300" aria-hidden="true" />
            <h2 className="mt-3 text-[16px] font-semibold text-slate-900">
              You&rsquo;re not assigned to any events yet
            </h2>
            {/* Says what to do about it rather than leaving a dead end — the previous
                behaviour dropped people into a console they could not use and never
                explained why. */}
            <p className="mx-auto mt-2 max-w-md text-[14px] leading-relaxed text-slate-600">
              {accountRole === "host"
                ? "Your account can run broadcasts, but an organization admin has to assign you to a specific event before its Producer Console will open."
                : "An organization admin assigns contributors to each event. Once you are added to one, it will appear here."}
            </p>
          </div>
        )}

        {!loading && !error && items.length > 0 && (
          <ul className="space-y-3">
            {items.map((item) => {
              const primary = item.roles?.find((r) => CONSOLE[r]);
              const entry = primary ? CONSOLE[primary] : null;
              const Icon = entry?.Icon || CalendarClock;
              return (
                <li
                  key={item.event_id}
                  className="flex flex-wrap items-center justify-between gap-4 rounded-2xl border border-slate-200 bg-white px-5 py-4"
                >
                  <div className="flex min-w-0 items-start gap-3">
                    <span aria-hidden="true" className="mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-600">
                      <Icon className="h-4 w-4" />
                    </span>
                    <div className="min-w-0">
                      <p className="text-[15px] font-semibold text-slate-900">{item.title}</p>
                      <p className="mt-0.5 text-[13px] text-slate-500">{fmt(item.start_time)}</p>
                      <div className="mt-1.5 flex flex-wrap gap-1.5">
                        <span className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STATUS_TONE[item.status] || STATUS_TONE.scheduled}`}>
                          {item.status}
                        </span>
                        {(item.roles || []).map((role) => (
                          <span
                            key={role}
                            className="inline-flex items-center rounded-full border border-slate-200 bg-slate-50 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-slate-600"
                          >
                            {ROLE_LABEL[role] || role}
                          </span>
                        ))}
                      </div>
                    </div>
                  </div>
                  {entry && (
                    // The event id travels in the URL, so the console never has to guess.
                    <Link
                      to={`${entry.path}?event=${encodeURIComponent(item.event_id)}`}
                      className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-slate-800"
                    >
                      <entry.Icon className="h-4 w-4" aria-hidden="true" />
                      {entry.label}
                    </Link>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </main>
  );
}
