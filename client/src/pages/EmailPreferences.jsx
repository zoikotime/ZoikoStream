import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api, { errMsg } from "../api";
import { BellOff, Check, Loader2, Mail, ShieldCheck } from "lucide-react";
import { Logo } from "../ui";

// EmailPreferences — the marketing preference centre and one-click unsubscribe
// (ZST-EC-001 MKT-000). Serves two routes from one component:
//
//   /preferences            manage topics, and redeem ?confirm=<token> on arrival
//   /unsubscribe?t=<token>  one-click suppression, applied immediately
//
// Unauthenticated by design. Requiring a login to unsubscribe is the thing that makes people
// mark mail as spam instead, and it would put an account credential behind a link that has
// no business being one.
//
// ── WHY THE TWO HANDLES ARE DIFFERENT ────────────────────────────────────────────────────
// The manage handle (?t= on /preferences) reads and writes topics. The unsubscribe handle
// (?t= on /unsubscribe) can do exactly one thing: suppress. They are separate values with
// separate purposes at rest, so a forwarded unsubscribe link cannot be used to read
// somebody's preferences, and neither grants any account access.
//
// ── WHAT UNSUBSCRIBING DOES NOT DO ───────────────────────────────────────────────────────
// It stops marketing. Account, security, billing, privacy and support email are separate
// mandatory channels, and the status page is its own opt-in subscription. The page says so
// explicitly, because "unsubscribe from everything" is what people usually assume.

const TOPIC_BLURB = {
  release_notes: "What we shipped, roughly monthly.",
  feature_announcements: "New features, and what stage they are actually at.",
  developer_education: "A short series on building with the API. Not a drip campaign.",
  live_event_education: "Guides and webinars on running live events.",
};

export default function EmailPreferences({ mode = "preferences" }) {
  const [params, setParams] = useSearchParams();
  const [subscription, setSubscription] = useState(null);
  const [topics, setTopics] = useState([]);
  const [available, setAvailable] = useState([]);
  const [manageToken, setManageToken] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  const [error, setError] = useState(null);

  // Arrival: redeem a confirmation, load a manage handle, or apply an unsubscribe.
  useEffect(() => {
    const confirmToken = params.get("confirm");
    const handle = params.get("t");
    let cancelled = false;

    (async () => {
      try {
        if (mode === "unsubscribe") {
          if (!handle) {
            setError("This unsubscribe link is missing its code. Please use the link from the email.");
            return;
          }
          // Applied on arrival: a one-click unsubscribe that needs a second click is not
          // one-click, and the suppression is committed server-side before we say so.
          const { data } = await api.post(
            `/trust/marketing/unsubscribe?t=${encodeURIComponent(handle)}`);
          if (!cancelled) {
            setSubscription({ status: data.status });
            setNote("You have been unsubscribed. This takes effect immediately.");
          }
          return;
        }

        if (confirmToken) {
          const { data } = await api.post("/trust/marketing/confirm",
                                          { token: confirmToken });
          if (cancelled) return;
          setNote("Your email is confirmed. You'll receive the updates you chose.");
          setManageToken(data.manage_token);
          const prefs = await api.get(
            `/trust/marketing/preferences?t=${encodeURIComponent(data.manage_token)}`);
          if (!cancelled) {
            setSubscription(prefs.data);
            setTopics(prefs.data.topics || []);
            setAvailable(prefs.data.available_topics || []);
          }
          return;
        }

        if (handle) {
          setManageToken(handle);
          const { data } = await api.get(
            `/trust/marketing/preferences?t=${encodeURIComponent(handle)}`);
          if (!cancelled) {
            setSubscription(data);
            setTopics(data.topics || []);
            setAvailable(data.available_topics || []);
          }
        }
      } catch (err) {
        if (!cancelled) setError(errMsg(err, "This link is not valid, or it has expired."));
      } finally {
        if (!cancelled) {
          setLoading(false);
          // Strip the credential from the URL so a bookmarked or shared link carries none.
          const next = new URLSearchParams(params);
          next.delete("confirm");
          next.delete("t");
          setParams(next, { replace: true });
        }
      }
    })();
    return () => { cancelled = true; };
    // Runs once, for whatever handle arrived; the effect then strips it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = (key) =>
    setTopics(topics.includes(key) ? topics.filter((k) => k !== key) : [...topics, key]);

  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const { data } = await api.patch(
        `/trust/marketing/preferences?t=${encodeURIComponent(manageToken)}`, { topics });
      setNote(data.changed
        ? (data.topics.length
          ? "Your preferences have been saved."
          : "You've turned everything off. You will not receive any product updates.")
        : "Those are already your preferences — nothing changed.");
      setSubscription((s) => ({ ...s, status: data.status, topics: data.topics }));
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  const unsubscribed = subscription?.status === "unsubscribed";

  return (
    <main className="min-h-screen bg-slate-50">
      <section className="bg-[#050a14] text-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-6 py-6">
          <Link to="/" aria-label="ZoikoStream home"><Logo height="h-7" /></Link>
          <Link
            to="/status"
            className="inline-flex items-center gap-2 rounded-xl border border-white/15 bg-white/[0.04] px-3.5 py-2 text-[13px] font-medium text-white/85 transition hover:border-white/30 hover:bg-white/[0.08]"
          >
            Status page
          </Link>
        </div>
        <div className="mx-auto max-w-3xl px-6 pb-10 pt-2">
          <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
            Email preferences
          </p>
          <h1 className="mt-3 flex items-start gap-3 text-[1.7rem] font-bold leading-tight tracking-tight sm:text-[2.1rem]">
            {mode === "unsubscribe" ? (
              <BellOff className="mt-1 h-6 w-6 shrink-0 text-white/80" aria-hidden="true" />
            ) : (
              <Mail className="mt-1 h-6 w-6 shrink-0 text-white/80" aria-hidden="true" />
            )}
            {mode === "unsubscribe" ? "Unsubscribed" : "What you receive"}
          </h1>
          <p className="mt-3 max-w-xl text-[14px] leading-relaxed text-white/60">
            Product updates only. Your account, security, billing, privacy and support emails
            are separate channels and are not affected by anything on this page.
          </p>
        </div>
      </section>

      <div className="mx-auto max-w-3xl space-y-6 px-6 py-10">
        {loading && (
          <p className="flex items-center gap-2 text-[14px] text-slate-500">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            Loading…
          </p>
        )}

        {note && (
          <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-emerald-900">
            {note}
          </p>
        )}
        {error && (
          <p className="rounded-xl border border-rose-200 bg-rose-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-rose-900">
            {error}
          </p>
        )}

        {/* Stated on both routes, because this is the assumption people actually make. */}
        {(mode === "unsubscribe" || unsubscribed) && !loading && (
          <section className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
            <h2 className="flex items-center gap-2 text-[15px] font-semibold text-slate-900">
              <ShieldCheck className="h-4 w-4 text-slate-400" aria-hidden="true" />
              What is still active
            </h2>
            <ul className="mt-3 space-y-2 text-[13px] leading-relaxed text-slate-600">
              <li>
                <strong className="font-semibold text-slate-800">Account and security. </strong>
                Sign-in alerts, credential changes and security advisories. These are mandatory
                and cannot be switched off.
              </li>
              <li>
                <strong className="font-semibold text-slate-800">Billing and privacy. </strong>
                Invoices, receipts, refunds and privacy request updates.
              </li>
              <li>
                <strong className="font-semibold text-slate-800">Support. </strong>
                Replies about cases you opened.
              </li>
              <li>
                <strong className="font-semibold text-slate-800">Status updates. </strong>
                A separate subscription — manage it on the{" "}
                <Link to="/status" className="font-semibold underline">status page</Link>.
              </li>
            </ul>
          </section>
        )}

        {mode === "preferences" && !loading && subscription && !unsubscribed && (
          <form onSubmit={save} className="rounded-2xl border border-slate-200 bg-white p-5 sm:p-6">
            <h2 className="text-[16px] font-semibold text-slate-900">
              Preferences for {subscription.email}
            </h2>
            <p className="mt-1 text-[13px] leading-relaxed text-slate-600">
              Each of these is separate. Choosing one does not sign you up for the others, and
              turning them all off unsubscribes you.
            </p>

            <div className="mt-5 space-y-2">
              {available.map((t) => {
                const on = topics.includes(t.key);
                return (
                  <button
                    key={t.key}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle(t.key)}
                    className={`flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left transition ${
                      on
                        ? "border-teal-400 bg-teal-50"
                        : "border-slate-200 bg-white hover:border-slate-300"
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded border ${
                        on ? "border-teal-500 bg-teal-500 text-white" : "border-slate-300"
                      }`}
                    >
                      {on && <Check className="h-3.5 w-3.5" />}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-[14px] font-medium text-slate-900">
                        {t.label}
                      </span>
                      <span className="block text-[13px] leading-relaxed text-slate-600">
                        {TOPIC_BLURB[t.key]}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            <div className="mt-5 flex flex-wrap items-center gap-3">
              <button
                type="submit"
                disabled={busy}
                className="inline-flex items-center gap-2 rounded-xl bg-slate-900 px-4 py-2.5 text-[14px] font-semibold text-white transition hover:bg-slate-800 disabled:opacity-60"
              >
                {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                Save preferences
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => setTopics([])}
                className="inline-flex items-center gap-2 rounded-xl border border-slate-200 px-4 py-2.5 text-[14px] font-medium text-slate-600 transition hover:border-slate-300 disabled:opacity-60"
              >
                <BellOff className="h-4 w-4" aria-hidden="true" />
                Turn everything off
              </button>
            </div>
          </form>
        )}

        {mode === "preferences" && !loading && !subscription && !error && (
          <p className="rounded-2xl border border-slate-200 bg-white px-5 py-8 text-center text-[14px] leading-relaxed text-slate-500">
            Open this page from the link at the bottom of one of our emails to manage what you
            receive.
          </p>
        )}
      </div>
    </main>
  );
}
