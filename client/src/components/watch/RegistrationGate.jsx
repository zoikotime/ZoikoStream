// client/src/components/watch/RegistrationGate.jsx
// Shown on the Viewer Portal in place of the player/chat when the event has
// registration_required set and this browser doesn't have a confirmed email yet
// (see EventWatch.jsx). Submitting doesn't validate anything itself -- the actual
// check happens server-side the first time the token/chat-join calls are made with
// this email (see server/app/services/registration.py).
import { useState } from "react";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

export default function RegistrationGate({ eventTitle, onSubmit }) {
  const [email, setEmail] = useState("");
  const valid = isEmail(email);

  return (
    <div className="mx-auto max-w-md rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Registration required</h2>
      <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
        Enter the email you registered with to watch <span className="font-medium text-slate-700 dark:text-slate-300">{eventTitle}</span>.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) onSubmit(email.trim().toLowerCase());
        }}
        className="mt-4 flex flex-col gap-3"
      >
        <input
          type="email"
          autoFocus
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="jane@company.com"
          className="w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-center text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100"
        />
        <button
          type="submit"
          disabled={!valid}
          className="rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-emerald-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          Continue
        </button>
      </form>
      <p className="mt-3 text-xs text-slate-400">Haven't registered yet? Use the invite link you were sent.</p>
    </div>
  );
}
