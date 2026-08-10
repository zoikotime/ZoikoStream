// client/src/components/watch/IdentifyForm.jsx
// Replaces the old "Sign in to chat/ask/vote" prompt. An anonymous visitor doesn't need an
// account to join in — the same self-serve name+email registration that gates a
// registration_required event (RegistrationGate.jsx) also works here, it's just optional
// rather than blocking the video. POSTs to the same /events/:id/register endpoint and hands
// the resulting access token back up so EventWatch can open the live socket with it.
import { useState } from "react";
import { FiMessageCircle, FiUser, FiMail } from "react-icons/fi";
import api, { errMsg } from "../../api";
import { cx } from "../../ui/tokens";

const field =
  "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

export default function IdentifyForm({ eventId, label, onIdentified }) {
  const [form, setForm] = useState({ name: "", email: "" });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
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
      onIdentified(data.token);
    } catch (err) {
      setError(errMsg(err, "Couldn't save your details — please try again."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-2 text-center">
      <FiMessageCircle className="text-2xl text-emerald-500" />
      <p className="text-sm text-slate-500 dark:text-slate-400">{label}</p>
      <form onSubmit={submit} className="w-full max-w-xs space-y-2.5 text-left">
        <div className="relative">
          <FiUser className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            className={cx(field, "pl-8")}
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="Your name"
            required
          />
        </div>
        <div className="relative">
          <FiMail className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
          <input
            type="email"
            className={cx(field, "pl-8")}
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            placeholder="Your email"
            required
          />
        </div>
        {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
        <button
          type="submit"
          disabled={!valid || submitting}
          className="w-full rounded-lg bg-emerald-600 px-3 py-2 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {submitting ? "Joining…" : "Continue"}
        </button>
      </form>
    </div>
  );
}
