// client/src/components/watch/RegistrationGate.jsx
// Shown in place of the video player when an event has registration_required=true and
// the visitor hasn't registered yet. Anonymous (no login) — POSTs name+email, stores the
// returned access token, and hands control back to EventWatch to refetch /watch.
import { useState } from "react";
import { FiCheckCircle, FiLock, FiMail, FiUser } from "react-icons/fi";
import api, { errMsg } from "../../api";
import { cx } from "../../ui/tokens";

const field =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const labelCls = "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300";
const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

export default function RegistrationGate({ eventId, eventTitle, onRegistered }) {
  const [form, setForm] = useState({ name: "", email: "" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [full, setFull] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.name.trim() && isEmail(form.email);

  const submit = async (e) => {
    e.preventDefault();
    if (!valid || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      const { data } = await api.post(`/events/${eventId}/register`, {
        name: form.name.trim(),
        email: form.email.trim().toLowerCase(),
      });
      localStorage.setItem(`zk_reg_${eventId}`, data.token);
      onRegistered?.();
    } catch (err) {
      if (err?.response?.status === 409) setFull(true);
      else setError(errMsg(err, "Couldn't register — please try again."));
    } finally {
      setSubmitting(false);
    }
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
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Registration required</h2>
          <p className="mb-6 mt-1 text-sm text-slate-500 dark:text-slate-400">
            Register with your name and email to watch {eventTitle || "this event"}.
          </p>
          <form onSubmit={submit} className="space-y-4 text-left">
            <div>
              <label className={labelCls}>Full name</label>
              <div className="relative">
                <FiUser className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  className={cx(field, "pl-9")}
                  value={form.name}
                  onChange={(e) => set("name", e.target.value)}
                  placeholder="Jane Doe"
                  required
                />
              </div>
            </div>
            <div>
              <label className={labelCls}>Email</label>
              <div className="relative">
                <FiMail className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="email"
                  className={cx(field, "pl-9")}
                  value={form.email}
                  onChange={(e) => set("email", e.target.value)}
                  placeholder="jane@company.com"
                  required
                />
              </div>
            </div>
            {error && <p className="text-sm text-rose-600 dark:text-rose-400">{error}</p>}
            <button
              type="submit"
              disabled={!valid || submitting}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <FiCheckCircle /> {submitting ? "Registering…" : "Register"}
            </button>
          </form>
        </div>
      )}
    </div>
  );
}
