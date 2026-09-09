import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api, { errMsg } from "../api";
import {
  Activity, AlertTriangle, Bell, BellOff, CalendarClock, Check, ChevronDown,
  CircleAlert, CircleCheck, Clock, History, Loader2, Mail, PencilLine, Settings2,
  ShieldAlert, Wrench,
} from "lucide-react";
import { Logo } from "../ui";

// Status — the public status page, and the ONLY thing every STS-001..006 email links to
// (email.py's status_page_url() is `{base}/status`). Deliberately unauthenticated: a status
// page you have to log in to see is useless during the outage that stops you logging in.
//
// ── WHAT THIS RENDERS ────────────────────────────────────────────────────────────────────
// Everything comes from GET /api/status, which serves PUBLISHED records only. The internal
// platform_ops.Incident is not reachable from here and neither is any admin object — no
// root-cause drafts, no monitoring data, no commander, no customer identities. This file
// therefore cannot leak them: it never asks for them.
//
// ── WHY THE HISTORY IS RENDERED IN FULL ──────────────────────────────────────────────────
// Published incident history is append-only on the server. A correction does not overwrite
// what it corrects — it appends a new version pointing back at the old one — so this page
// shows every version, with corrections labelled and the statement they correct still
// visible. Hiding the superseded text here would undo the guarantee the backend makes.
//
// ── SUBSCRIPTIONS ────────────────────────────────────────────────────────────────────────
// Subscribing is double opt-in: POST /subscribe always answers 202 (it must not become an
// address-enumeration oracle) and nothing is sent until the address proves control of the
// inbox. The confirmation link lands back here as ?confirm=<token>, the manage link as
// ?t=<handle>; both are redeemed on arrival and then stripped from the URL, so a shared or
// bookmarked link carries no credential.

const IMPACT_TONE = {
  none: { dot: "bg-emerald-500", text: "text-emerald-700", Icon: CircleCheck },
  degraded: { dot: "bg-amber-500", text: "text-amber-700", Icon: CircleAlert },
  partial_outage: { dot: "bg-orange-500", text: "text-orange-700", Icon: AlertTriangle },
  major_outage: { dot: "bg-rose-600", text: "text-rose-700", Icon: ShieldAlert },
};

const STATUS_CHIP = {
  investigating: "bg-rose-50 border-rose-200 text-rose-800",
  identified: "bg-orange-50 border-orange-200 text-orange-800",
  monitoring: "bg-amber-50 border-amber-200 text-amber-800",
  resolved: "bg-emerald-50 border-emerald-200 text-emerald-700",
};

const tone = (impact) => IMPACT_TONE[impact] || IMPACT_TONE.none;

// Every timestamp the API sends is UTC (the field names say so, and the server normalizes
// them). Rendering in the reader's own timezone is the kindness; the UTC value stays in the
// title attribute so a customer and an engineer comparing notes are never arguing about
// which clock a time was in.
const fmt = (iso) => {
  if (!iso) return null;
  try {
    return new Date(iso).toLocaleString(undefined, {
      day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit",
    });
  } catch {
    return iso;
  }
};

const utc = (iso) => {
  if (!iso) return "";
  try {
    return `${new Date(iso).toISOString().slice(0, 16).replace("T", " ")} UTC`;
  } catch {
    return iso;
  }
};

function Stamp({ iso, prefix }) {
  if (!iso) return null;
  return (
    <time dateTime={iso} title={utc(iso)} className="whitespace-nowrap">
      {prefix ? `${prefix} ` : ""}
      {fmt(iso)}
    </time>
  );
}

function Chip({ className = "", children }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide ${className}`}>
      {children}
    </span>
  );
}

// ── incident ──────────────────────────────────────────────────────────────────────────────

function Incident({ incident, defaultOpen }) {
  const [open, setOpen] = useState(Boolean(defaultOpen));
  const t = tone(incident.impact);
  // Newest first for reading; the server numbers published versions from 1 upward.
  const history = useMemo(
    () => [...(incident.history || [])].sort((a, b) => b.version - a.version),
    [incident.history],
  );
  const corrected = useMemo(
    () => new Set(history.map((h) => h.corrects_version).filter(Boolean)),
    [history],
  );

  return (
    <article className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
      <header className="flex flex-wrap items-start gap-x-4 gap-y-3 border-b border-slate-100 px-5 py-4">
        <span aria-hidden="true" className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${t.dot}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[15px] font-semibold text-slate-900">{incident.title}</h3>
            <Chip className={STATUS_CHIP[incident.status] || STATUS_CHIP.investigating}>
              {incident.status}
            </Chip>
            {/* Resolved → Reopened stays visible: it is part of the published record. */}
            {incident.reopened_at && (
              <Chip className="border-slate-200 bg-slate-50 text-slate-600">Reopened</Chip>
            )}
          </div>
          <p className="mt-1 text-[13px] text-slate-500">
            <span className="font-mono text-slate-400">{incident.reference}</span>
            {" · "}
            {incident.impact_label}
            {incident.components?.length ? ` · ${incident.components.join(", ")}` : ""}
            {incident.regions?.length ? ` · ${incident.regions.join(", ")}` : ""}
          </p>
        </div>
        <div className="text-right text-[12px] text-slate-500">
          <Stamp iso={incident.started_at} prefix="Started" />
          {incident.resolved_at && (
            <div className="text-emerald-700">
              <Stamp iso={incident.resolved_at} prefix="Resolved" />
            </div>
          )}
        </div>
      </header>

      <div className="px-5 py-4">
        {incident.current_update && (
          <p className="whitespace-pre-line text-[14px] leading-relaxed text-slate-700">
            {incident.current_update}
          </p>
        )}
        {incident.customer_action && (
          <p className="mt-3 rounded-xl border border-sky-200 bg-sky-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-sky-900">
            <strong className="font-semibold">What you can do: </strong>
            {incident.customer_action}
          </p>
        )}
        {incident.residual_work && incident.residual_summary && (
          <p className="mt-3 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-slate-700">
            <strong className="font-semibold">Remaining work: </strong>
            {incident.residual_summary}
          </p>
        )}

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-slate-500">
          {/* Shown only when a next update was actually promised — never invented here. */}
          {incident.next_update_at && (
            <span className="inline-flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5" aria-hidden="true" />
              <Stamp iso={incident.next_update_at} prefix="Next update by" />
            </span>
          )}
          {incident.review_published && (
            <span className="inline-flex items-center gap-1.5 text-slate-600">
              <History className="h-3.5 w-3.5" aria-hidden="true" />
              Post-incident review published in the history below
            </span>
          )}
        </div>

        {history.length > 0 && (
          <>
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              aria-expanded={open}
              className="mt-4 inline-flex items-center gap-1.5 text-[13px] font-semibold text-slate-600 transition hover:text-slate-900"
            >
              <ChevronDown className={`h-4 w-4 transition ${open ? "rotate-180" : ""}`} aria-hidden="true" />
              {open ? "Hide" : "Show"} full history ({history.length})
            </button>

            {open && (
              <ol className="mt-3 space-y-3 border-l border-slate-200 pl-4">
                {history.map((u) => (
                  <li key={u.version} className="relative">
                    <span
                      aria-hidden="true"
                      className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-slate-300"
                    />
                    <div className="flex flex-wrap items-center gap-2 text-[12px]">
                      <span className="font-semibold uppercase tracking-wide text-slate-500">
                        {u.status}
                      </span>
                      <span className="text-slate-400">
                        <Stamp iso={u.published_at} />
                      </span>
                      {u.type === "correction" && (
                        <Chip className="border-violet-200 bg-violet-50 text-violet-800">
                          <PencilLine className="h-3 w-3" aria-hidden="true" />
                          Corrects v{u.corrects_version}
                        </Chip>
                      )}
                      {u.type === "review" && (
                        <Chip className="border-slate-200 bg-slate-100 text-slate-700">Review</Chip>
                      )}
                      {/* A corrected statement stays published, and stays labelled. */}
                      {corrected.has(u.version) && (
                        <Chip className="border-amber-200 bg-amber-50 text-amber-800">
                          Later corrected
                        </Chip>
                      )}
                    </div>
                    <p className="mt-1 whitespace-pre-line text-[13px] leading-relaxed text-slate-700">
                      {u.body}
                    </p>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </div>
    </article>
  );
}

// ── maintenance ───────────────────────────────────────────────────────────────────────────

function Maintenance({ window: w }) {
  const emergency = w.kind === "emergency";
  return (
    <article className={`rounded-2xl border px-5 py-4 ${emergency ? "border-rose-200 bg-rose-50/50" : "border-slate-200 bg-white"}`}>
      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <span
          aria-hidden="true"
          className={`mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg ${emergency ? "bg-rose-100 text-rose-700" : "bg-slate-100 text-slate-600"}`}
        >
          {emergency ? <ShieldAlert className="h-4 w-4" /> : <Wrench className="h-4 w-4" />}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[15px] font-semibold text-slate-900">{w.title}</h3>
            {/* Emergency work is never dressed up as planned maintenance. */}
            <Chip className={emergency
              ? "border-rose-200 bg-rose-100 text-rose-800"
              : "border-slate-200 bg-slate-100 text-slate-600"}>
              {emergency ? "Emergency" : "Scheduled"}
            </Chip>
            <Chip className="border-slate-200 bg-slate-50 text-slate-600">
              {w.status.replace("_", " ")}
            </Chip>
          </div>
          <p className="mt-1 text-[13px] text-slate-500">
            <span className="font-mono text-slate-400">{w.reference}</span>
            {w.components?.length ? ` · ${w.components.join(", ")}` : ""}
            {w.regions?.length ? ` · ${w.regions.join(", ")}` : ""}
          </p>
          <p className="mt-2 text-[14px] leading-relaxed text-slate-700">{w.impact_summary}</p>
          {w.remaining_work && (
            <p className="mt-2 text-[13px] leading-relaxed text-slate-600">
              <strong className="font-semibold">Remaining work: </strong>{w.remaining_work}
            </p>
          )}
        </div>
        <dl className="w-full shrink-0 space-y-1 text-[12px] text-slate-500 sm:w-56">
          <div className="flex justify-between gap-3">
            <dt>Starts</dt>
            <dd className="text-slate-700"><Stamp iso={w.starts_at_utc} /></dd>
          </div>
          <div className="flex justify-between gap-3">
            <dt>Ends</dt>
            <dd className="text-slate-700"><Stamp iso={w.ends_at_utc} /></dd>
          </div>
          {/* A revised window shows what it was, so nobody has to trust their memory. */}
          {w.previous_starts_at_utc && (
            <div className="flex justify-between gap-3 text-slate-400">
              <dt>Previously</dt>
              <dd className="line-through"><Stamp iso={w.previous_starts_at_utc} /></dd>
            </div>
          )}
          {/* "Started" is a recorded fact, not the clock reaching the planned time. */}
          {w.started_at && (
            <div className="flex justify-between gap-3">
              <dt>Started</dt>
              <dd className="text-slate-700"><Stamp iso={w.started_at} /></dd>
            </div>
          )}
          {w.completed_at && (
            <div className="flex justify-between gap-3">
              <dt>Completed</dt>
              <dd className="text-emerald-700"><Stamp iso={w.completed_at} /></dd>
            </div>
          )}
          {w.canceled_at && (
            <div className="flex justify-between gap-3">
              <dt>Cancelled</dt>
              <dd className="text-slate-700"><Stamp iso={w.canceled_at} /></dd>
            </div>
          )}
        </dl>
      </div>
    </article>
  );
}

// ── subscription ──────────────────────────────────────────────────────────────────────────

function Picker({ label, options, value, setValue }) {
  const toggle = (key) =>
    setValue(value.includes(key) ? value.filter((k) => k !== key) : [...value, key]);
  return (
    <fieldset className="mt-4">
      <legend className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </legend>
      <p className="mt-1 text-[12px] text-slate-500">
        Select none to be notified about everything.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        {options.map((o) => {
          const on = value.includes(o.key);
          return (
            <button
              key={o.key}
              type="button"
              aria-pressed={on}
              onClick={() => toggle(o.key)}
              className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[13px] transition ${
                on
                  ? "border-teal-400 bg-teal-50 font-medium text-teal-900"
                  : "border-slate-200 bg-white text-slate-600 hover:border-slate-300"
              }`}
            >
              {on && <Check className="h-3.5 w-3.5" aria-hidden="true" />}
              {o.label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

function Subscribe({ components, regions, manage, onManaged }) {
  const [email, setEmail] = useState("");
  // Seeded from the subscription being managed rather than synced in an effect: the parent
  // keys this component on the manage handle, so a new handle remounts it and reseeds.
  const [picked, setPicked] = useState(() => manage?.components || []);
  const [places, setPlaces] = useState(() => manage?.regions || []);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      if (manage) {
        const { data } = await api.patch(
          `/status/preferences?t=${encodeURIComponent(manage.token)}`,
          { components: picked, regions: places },
        );
        setNote(data.changed
          ? "Your preferences have been updated."
          : "Those are already your preferences — nothing changed.");
        onManaged?.();
      } else {
        await api.post("/status/subscribe", { email, components: picked, regions: places });
        // 202 whatever the address's history is: this response must not reveal whether an
        // address is already subscribed.
        setNote("Check your inbox. We've sent a link to confirm this address — nothing else is sent until you do.");
        setEmail("");
      }
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const stopAll = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/status/unsubscribe?t=${encodeURIComponent(manage.token)}`);
      setNote("You've been unsubscribed from status updates. Account, security, billing and privacy emails are a separate channel and are unaffected.");
      onManaged?.();
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section id="subscribe" className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
      <div className="flex items-start gap-3">
        <span aria-hidden="true" className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-900 text-white">
          {manage ? <Settings2 className="h-4 w-4" /> : <Bell className="h-4 w-4" />}
        </span>
        <div className="min-w-0">
          <h2 className="text-[17px] font-semibold text-slate-900">
            {manage ? "Manage your status subscription" : "Subscribe to status updates"}
          </h2>
          <p className="mt-1 text-[13px] leading-relaxed text-slate-600">
            {manage
              ? `Preferences for ${manage.email}. Status email is a separate channel — changing it never affects account, security, billing or privacy mail.`
              : "Incident and maintenance notices for the parts of the platform you care about. Nothing else — this is not a marketing list."}
          </p>
        </div>
      </div>

      <form onSubmit={submit} className="mt-5">
        {!manage && (
          <label className="block">
            <span className="text-[12px] font-semibold uppercase tracking-wide text-slate-500">
              Email address
            </span>
            <div className="mt-2 flex items-center gap-2 rounded-xl border border-slate-200 px-3 py-2.5 focus-within:border-slate-400">
              <Mail className="h-4 w-4 shrink-0 text-slate-400" aria-hidden="true" />
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                className="w-full bg-transparent text-[14px] text-slate-900 outline-none placeholder:text-slate-400"
              />
            </div>
          </label>
        )}

        <Picker label="Components" options={components} value={picked} setValue={setPicked} />
        <Picker label="Regions" options={regions} value={places} setValue={setPlaces} />

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={busy}
            className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
          >
            {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {manage ? "Save preferences" : "Subscribe"}
          </button>
          {manage && (
            <button
              type="button"
              onClick={stopAll}
              disabled={busy}
              className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-[14px] font-medium text-slate-600 transition hover:border-slate-300 disabled:opacity-60"
            >
              <BellOff className="h-4 w-4" aria-hidden="true" />
              Unsubscribe from status email
            </button>
          )}
        </div>

        {note && (
          <p className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-emerald-900">
            {note}
          </p>
        )}
        {error && (
          <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-rose-900">
            {error}
          </p>
        )}
      </form>
    </section>
  );
}

// ── page ──────────────────────────────────────────────────────────────────────────────────

export default function Status() {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [manage, setManage] = useState(null);
  const [banner, setBanner] = useState(null);

  const load = useCallback(async () => {
    try {
      const { data: payload } = await api.get("/status");
      setData(payload);
      setFailed(false);
    } catch {
      // If even this page cannot load, say so plainly rather than rendering a reassuring
      // green "all systems operational" built from nothing.
      setFailed(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // The first read has to happen from here: there is no other trigger, and the page has
    // nothing to render until the published status arrives.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    // Long enough not to hammer the API from a page people leave open during an outage.
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, [load]);

  // Redeem a confirmation or manage token on arrival, then strip it from the URL so a
  // bookmarked or shared link carries no credential.
  useEffect(() => {
    const confirmToken = params.get("confirm");
    const manageToken = params.get("t");
    if (!confirmToken && !manageToken) return undefined;

    let cancelled = false;
    (async () => {
      try {
        if (confirmToken) {
          const { data: out } = await api.post("/status/subscribe/confirm", { token: confirmToken });
          if (cancelled) return;
          setBanner({
            ok: true,
            text: "Your email is confirmed. You'll receive status updates for your selected components.",
          });
          if (out.manage_token) {
            const { data: prefs } = await api.get(
              `/status/preferences?t=${encodeURIComponent(out.manage_token)}`);
            if (!cancelled) setManage({ ...prefs, token: out.manage_token });
          }
        } else {
          const { data: prefs } = await api.get(
            `/status/preferences?t=${encodeURIComponent(manageToken)}`);
          if (!cancelled) setManage({ ...prefs, token: manageToken });
        }
      } catch (err) {
        if (!cancelled) setBanner({ ok: false, text: errMsg(err, "That link is no longer valid.") });
      } finally {
        if (!cancelled) {
          const next = new URLSearchParams(params);
          next.delete("confirm");
          next.delete("t");
          setParams(next, { replace: true });
        }
      }
    })();
    return () => { cancelled = true; };
    // Runs once, for the token present on arrival; the effect then strips it from the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const overall = tone(data?.overall);
  const OverallIcon = overall.Icon;

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-4 px-6 py-6 lg:px-8">
          <Link to="/" aria-label="ZoikoStream home">
            <Logo height="h-7" />
          </Link>
          <a
            href="#subscribe"
            className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
          >
            <Bell className="h-4 w-4" aria-hidden="true" />
            Get notified
          </a>
        </div>
        <div className="mx-auto max-w-5xl px-6 pb-12 pt-4 lg:px-8">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            ZoikoStream status
          </p>
          {loading ? (
            <h1 className="mt-3 flex items-center gap-3 text-[1.9rem] font-bold tracking-tight sm:text-[2.4rem]">
              <Loader2 className="h-7 w-7 animate-spin text-white/50" aria-hidden="true" />
              Checking&hellip;
            </h1>
          ) : failed ? (
            <h1 className="mt-3 text-[1.9rem] font-bold tracking-tight sm:text-[2.4rem]">
              We can&rsquo;t load the status page right now
            </h1>
          ) : (
            <h1 className="mt-3 flex items-start gap-3 text-[1.9rem] font-bold leading-tight tracking-tight sm:text-[2.4rem]">
              <OverallIcon className="mt-1 h-7 w-7 shrink-0 text-white/80" aria-hidden="true" />
              {data.overall === "none" ? "All systems operational" : data.overall_label}
            </h1>
          )}
          <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-white/60">
            {failed
              ? "This page could not reach the status API. That itself may be a sign of a wider problem — please try again shortly."
              : "Published service status, incident history and planned maintenance. Times are shown in your local timezone; hover any timestamp for UTC."}
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-5xl space-y-8 px-6 py-10 lg:px-8">
        {banner && (
          <p className={`rounded-xl border px-3.5 py-2.5 text-[13px] leading-relaxed ${
            banner.ok
              ? "border-emerald-200 bg-emerald-50 text-emerald-900"
              : "border-rose-200 bg-rose-50 text-rose-900"
          }`}>
            {banner.text}
          </p>
        )}

        {!loading && !failed && (
          <>
            <section>
              <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                <Activity className="h-4 w-4" aria-hidden="true" />
                Components
              </h2>
              <ul className="mt-3 divide-y divide-slate-100 overflow-hidden rounded-2xl border border-slate-200 bg-white">
                {data.components.map((c) => {
                  const ct = tone(c.impact);
                  return (
                    <li key={c.key} className="flex items-center justify-between gap-4 px-5 py-3.5">
                      <span className="text-[14px] font-medium text-slate-800">{c.label}</span>
                      <span className={`inline-flex items-center gap-2 text-[13px] font-medium ${ct.text}`}>
                        <span aria-hidden="true" className={`h-2 w-2 rounded-full ${ct.dot}`} />
                        {c.impact_label}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>

            {data.active_incidents.length > 0 && (
              <section>
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                  <AlertTriangle className="h-4 w-4" aria-hidden="true" />
                  Active incidents
                </h2>
                <div className="mt-3 space-y-4">
                  {data.active_incidents.map((i) => (
                    <Incident key={i.reference} incident={i} defaultOpen />
                  ))}
                </div>
              </section>
            )}

            {data.scheduled_maintenance.length > 0 && (
              <section>
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                  <CalendarClock className="h-4 w-4" aria-hidden="true" />
                  Scheduled maintenance
                </h2>
                <div className="mt-3 space-y-4">
                  {data.scheduled_maintenance.map((w) => (
                    <Maintenance key={w.reference} window={w} />
                  ))}
                </div>
              </section>
            )}

            <Subscribe
              key={manage?.token || "new"}
              components={data.components.map((c) => ({ key: c.key, label: c.label }))}
              regions={data.regions.map((r) => ({ key: r.key, label: r.label }))}
              manage={manage}
              onManaged={load}
            />

            {data.incident_history.length > 0 && (
              <section>
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                  <History className="h-4 w-4" aria-hidden="true" />
                  Past incidents
                </h2>
                <div className="mt-3 space-y-4">
                  {data.incident_history.map((i) => (
                    <Incident key={i.reference} incident={i} />
                  ))}
                </div>
              </section>
            )}

            {data.recent_maintenance.length > 0 && (
              <section>
                <h2 className="flex items-center gap-2 text-[13px] font-semibold uppercase tracking-wide text-slate-500">
                  <Wrench className="h-4 w-4" aria-hidden="true" />
                  Recent maintenance
                </h2>
                <div className="mt-3 space-y-4">
                  {data.recent_maintenance.map((w) => (
                    <Maintenance key={w.reference} window={w} />
                  ))}
                </div>
              </section>
            )}

            {data.active_incidents.length === 0
              && data.scheduled_maintenance.length === 0
              && data.incident_history.length === 0 && (
                <p className="rounded-2xl border border-slate-200 bg-white px-5 py-8 text-center text-[14px] text-slate-500">
                  No incidents have been published.
                </p>
              )}
          </>
        )}
      </div>
    </main>
  );
}
