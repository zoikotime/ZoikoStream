// client/src/components/watch/RegistrationGate.jsx
// Shown in place of the video player when an event has registration_required=true and
// the visitor hasn't registered yet. Anonymous (no login) — POSTs the NAME ONLY, stores the
// returned access token, and hands control back to EventWatch to refetch /watch.
//
// ── TWO DIFFERENT THINGS, KEPT APART ────────────────────────────────────────────────────
// A viewer who ticked "Remember me" on an earlier event is greeted with "Continue as
// Naveen" instead of an empty form. That shortcut saves them TYPING and nothing else:
//
//   remembered profile   the name, one key for the whole browser (utils/viewerProfile).
//                        Grants no access at all. It only fills in the form.
//   event credential     `zk_reg_<eventId>` in EventWatch — an opaque server-signed token
//                        bound to ONE event. This is what grants access.
//
// So Continue still POSTs a real registration for the CURRENT event and waits for the
// server's answer. Nobody is counted as registered for an event they have not registered
// for, and a token minted for event A is never presented to event B.
import { useState } from "react";
import { FiCheckCircle, FiLock, FiUser } from "react-icons/fi";
import api, { errMsg } from "../../api";
import { cx } from "../../ui/tokens";
import { clearViewerProfile, readViewerProfile, saveViewerProfile } from "../../utils/viewerProfile";

const field =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const labelCls = "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300";

export default function RegistrationGate({ eventId, eventTitle, onRegistered }) {
  // Read once per mount, and the gate mounts fresh for each event. Null in a private window,
  // on a new device, or for anyone who never ticked Remember me — all of which land on the
  // ordinary empty form below.
  const [saved, setSaved] = useState(readViewerProfile);
  const [form, setForm] = useState(() => saved || { name: "" });
  // Unticked by default: keeping a credential on the device past this visit is a choice the
  // viewer makes, not one made for them. A returning viewer has already made it — a saved
  // profile only exists because they ticked this — so it starts on for them, and unticking
  // it is how they withdraw the consent.
  const [remember, setRemember] = useState(Boolean(saved));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [full, setFull] = useState(false);
  // "Not you? Change details" — reveals the editable form with the saved values in it.
  const [editing, setEditing] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const valid = Boolean(form.name.trim());
  // The one-tap path: a profile we trust, and the viewer has not asked to edit it.
  const greeting = saved && !editing ? saved : null;

  const changeDetails = () => {
    setEditing(true);
    setError(null);
    // The remembered details are cleared the moment they say "not you", so an abandoned
    // visit does not leave someone else's name on this device. Registering again below
    // writes a fresh profile if Remember me is still ticked.
    clearViewerProfile();
    setSaved(null);
  };

  // `identity` is passed explicitly rather than read from state: the Continue path submits
  // the SAVED values, and the edit path submits the typed ones.
  const register = async (identity) => {
    if (submitting) return;
    const name = identity.name.trim();
    // Validated on both paths — a corrupt or hand-edited stored profile must not be able to
    // post something the typed form would have rejected.
    if (!name) {
      changeDetails();
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      // ALWAYS a real registration for THIS event, even on Continue. The remembered profile
      // is a name, never a token, so there is nothing here that could carry access over from
      // an event the viewer registered for previously. No `email` key is sent at all — the
      // schema makes it optional and the server mints its own placeholder (see
      // routers/events.register_for_event).
      const { data } = await api.post(`/events/${eventId}/register`, { name });
      // Only after the server accepted it, and only with consent. A rejected or abandoned
      // attempt leaves the remembered profile exactly as it was.
      if (remember) saveViewerProfile({ name });
      else clearViewerProfile();
      // Storage of the CREDENTIAL is EventWatch's job — it owns both the persistent and
      // session-only stores and the Remember me choice decides which. Writing localStorage
      // here as well meant a credential was persisted unconditionally, whatever the viewer
      // chose.
      //
      // `data.token` is an opaque server-signed credential bound to this event. The name and
      // email above are registration DATA; neither is ever sent back as proof of anything.
      onRegistered?.(data.token, remember);
    } catch (err) {
      if (err?.response?.status === 409) setFull(true);
      else setError(errMsg(err, "Couldn't register — please try again."));
    } finally {
      setSubmitting(false);
    }
  };

  const submit = (e) => {
    e.preventDefault();
    if (!valid) return;
    register(form);
  };

  return (
    <div className="flex aspect-video w-full flex-col items-center justify-center rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900 sm:p-10">
      {full ? (
        <div className="flex max-w-sm flex-col items-center gap-2">
          <FiLock className="text-3xl text-slate-400" />
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">This event is full</h2>
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Registration for {eventTitle || "this event"} has reached capacity.
          </p>
        </div>
      ) : (
        <div className="w-full max-w-sm">
          <FiLock className="mx-auto mb-3 text-2xl text-emerald-500" />
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Enter Your Name</h2>
          <p className="mb-6 mt-1 text-sm text-slate-500 dark:text-slate-400">
            {greeting
              ? `Confirm your name to watch ${eventTitle || "this event"}.`
              : "Enter your name to watch the live event."}
          </p>

          {greeting ? (
            // One tap for a returning viewer. Still a full registration for this event —
            // the button says Continue because they have nothing left to type, not because
            // anything is being skipped.
            <div className="space-y-4 text-left">
              <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 dark:border-slate-700 dark:bg-slate-800/60">
                <p className="text-sm font-semibold text-slate-900 dark:text-white">
                  Continue as {greeting.name}
                </p>
              </div>
              {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
              <button
                type="button"
                onClick={() => register(greeting)}
                disabled={submitting}
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                <FiCheckCircle /> {submitting ? "Registering…" : "Continue"}
              </button>
              <button
                type="button"
                onClick={changeDetails}
                disabled={submitting}
                className="w-full text-center text-sm text-slate-500 underline-offset-2 transition hover:text-slate-700 hover:underline disabled:opacity-50 dark:text-slate-400 dark:hover:text-slate-200"
              >
                Not you? Change details
              </button>
            </div>
          ) : (
          <form onSubmit={submit} className="space-y-4 text-left">
            <div>
              <label className={labelCls}>Full name</label>
              <div className="relative">
                <FiUser className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  className={cx(field, "pl-9")}
                  value={form.name}
                  onChange={(e) => set("name", e.target.value)}
                  placeholder="Enter your name"
                  required
                />
              </div>
            </div>
            {/* Directly below the name, above the button. */}
            <label className="flex cursor-pointer items-start gap-2.5 text-left">
              <input
                type="checkbox"
                checked={remember}
                onChange={(e) => setRemember(e.target.checked)}
                className="mt-0.5 h-4 w-4 shrink-0 cursor-pointer rounded border-slate-300 text-emerald-600 focus:ring-emerald-500 dark:border-slate-600 dark:bg-slate-800"
              />
              <span className="text-sm text-slate-700 dark:text-slate-300">
                Remember me for this event
                <span className="block text-xs text-slate-500 dark:text-slate-400">
                  Skip this form next time on this device.
                </span>
              </span>
            </label>
            {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
            <button
              type="submit"
              disabled={!valid || submitting}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <FiCheckCircle /> {submitting ? "Continuing…" : "Continue to Watch"}
            </button>
          </form>
          )}
        </div>
      )}
    </div>
  );
}
