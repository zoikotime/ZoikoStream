import { useState } from "react";
import { useParams } from "react-router-dom";
import {
  FiCalendar,
  FiClock,
  FiGlobe,
  FiCheckCircle,
  FiArrowRight,
} from "react-icons/fi";
import { cx } from "../ui/tokens";
import Button from "../ui/Button";
import { getEvent, fmtDate } from "../data/events";

// Literal gradient per accent — Tailwind JIT can't compile interpolated class names.
const BANNER = {
  violet: "from-violet-600 via-indigo-700 to-slate-900",
  emerald: "from-emerald-600 via-teal-700 to-slate-900",
  blue: "from-blue-600 via-sky-700 to-slate-900",
  amber: "from-amber-500 via-orange-600 to-slate-900",
  indigo: "from-indigo-600 via-violet-700 to-slate-900",
  rose: "from-rose-600 via-pink-700 to-slate-900",
};

// ponytail: dummy agenda — shared across events until each carries its own.
const AGENDA = [
  { time: "10:00", title: "Doors open & networking" },
  { time: "10:30", title: "Opening keynote" },
  { time: "11:15", title: "Panel: where the industry is heading" },
  { time: "12:00", title: "Live demo & product deep-dive" },
  { time: "12:45", title: "Audience Q&A" },
  { time: "13:15", title: "Closing remarks" },
];

const field =
  "w-full rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm outline-none transition placeholder:text-slate-400 focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-100";
const labelCls = "mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300";
const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

function RegistrationForm({ event }) {
  const [form, setForm] = useState({ name: "", email: "", phone: "", company: "" });
  const [done, setDone] = useState(false);
  const set = (k, v) => setForm((f) => ({ ...f, [k]: v }));
  const valid = form.name.trim() && isEmail(form.email);

  if (done)
    return (
      <div className="flex flex-col items-center gap-3 py-8 text-center">
        <FiCheckCircle className="text-4xl text-emerald-500" />
        <h3 className="text-lg font-semibold text-slate-900 dark:text-white">You're registered!</h3>
        <p className="text-sm text-slate-500 dark:text-slate-400">
          A confirmation has been sent to <span className="font-medium text-slate-700 dark:text-slate-200">{form.email}</span>.
        </p>
      </div>
    );

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        // ponytail: no backend — show the confirmation state. Wire to POST /register later.
        console.log("register", { eventId: event.id, ...form });
        setDone(true);
      }}
      className="space-y-4"
    >
      <div>
        <label className={labelCls}>Full Name</label>
        <input className={field} value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="Jane Doe" required />
      </div>
      <div>
        <label className={labelCls}>Email</label>
        <input type="email" className={field} value={form.email} onChange={(e) => set("email", e.target.value)} placeholder="jane@company.com" required />
      </div>
      <div>
        <label className={labelCls}>Phone</label>
        <input type="tel" className={field} value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="+1 555 000 1234" />
      </div>
      <div>
        <label className={labelCls}>Company</label>
        <input className={field} value={form.company} onChange={(e) => set("company", e.target.value)} placeholder="Acme Inc." />
      </div>
      <Button type="submit" className="w-full" disabled={!valid}>Register</Button>
      <p className="text-center text-xs text-slate-400">Free to attend · No credit card required</p>
    </form>
  );
}

export default function EventRegistration() {
  const { id } = useParams();
  const event = getEvent(id);

  if (!event)
    return (
      <div className="grid min-h-screen place-items-center bg-slate-50 dark:bg-slate-950">
        <p className="text-sm text-slate-500 dark:text-slate-400">This event could not be found.</p>
      </div>
    );

  const tz = event.timezone.split("/").pop().replace("_", " ");

  return (
    <div className="min-h-screen bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-200">
      {/* Brand bar */}
      <header className="border-b border-slate-200 bg-white/80 backdrop-blur dark:border-slate-800 dark:bg-slate-900/80">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-4 py-4 sm:px-6">
          <span className="text-lg font-bold tracking-tight text-slate-900 dark:text-white">
            Zoiko<span className="text-emerald-500">Stream</span>
          </span>
          <a href="#register" className="text-sm font-medium text-emerald-600 hover:text-emerald-500 dark:text-emerald-400">Register →</a>
        </div>
      </header>

      {/* Banner */}
      <div className={cx("relative overflow-hidden bg-gradient-to-br", BANNER[event.accent] || BANNER.emerald)}>
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,rgba(255,255,255,0.15),transparent_50%)]" />
        <div className="relative mx-auto max-w-6xl px-4 py-14 sm:px-6 sm:py-20">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-white/15 px-3 py-1 text-xs font-semibold text-white backdrop-blur">
            {event.category}
          </span>
          <h1 className="mt-4 max-w-3xl text-3xl font-bold tracking-tight text-white sm:text-5xl">{event.name}</h1>
          <p className="mt-4 max-w-2xl text-base text-white/85 sm:text-lg">{event.description}</p>
          <div className="mt-6 flex flex-wrap gap-x-6 gap-y-3 text-sm text-white/90">
            <span className="inline-flex items-center gap-2"><FiCalendar /> {fmtDate(event.date)}</span>
            <span className="inline-flex items-center gap-2"><FiClock /> {event.start}–{event.end}</span>
            <span className="inline-flex items-center gap-2"><FiGlobe /> {tz}</span>
          </div>
          <Button href="#register" className="mt-8">Register Now <FiArrowRight /></Button>
        </div>
      </div>

      {/* Content */}
      <main className="mx-auto grid max-w-6xl grid-cols-1 gap-8 px-4 py-12 sm:px-6 lg:grid-cols-3">
        {/* Left: details */}
        <div className="space-y-10 lg:col-span-2">
          <section>
            <h2 className="mb-3 text-xl font-semibold text-slate-900 dark:text-white">About this event</h2>
            <p className="leading-relaxed text-slate-600 dark:text-slate-300">{event.description}</p>
          </section>

          {event.speakers.length > 0 && (
            <section>
              <h2 className="mb-4 text-xl font-semibold text-slate-900 dark:text-white">Speakers</h2>
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {event.speakers.map((name) => (
                  <div key={name} className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900">
                    <span className="grid h-12 w-12 shrink-0 place-items-center rounded-full bg-gradient-to-br from-emerald-500 to-teal-600 text-sm font-semibold text-white">
                      {name.split(" ").map((w) => w[0]).join("")}
                    </span>
                    <div>
                      <p className="font-medium text-slate-800 dark:text-slate-100">{name}</p>
                      <p className="text-xs text-slate-500 dark:text-slate-400">Speaker</p>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          <section>
            <h2 className="mb-4 text-xl font-semibold text-slate-900 dark:text-white">Agenda</h2>
            <ol className="relative space-y-5 border-l border-slate-200 pl-6 dark:border-slate-800">
              {AGENDA.map((a) => (
                <li key={a.time} className="relative">
                  <span className="absolute -left-[27px] top-1 h-3 w-3 rounded-full border-2 border-white bg-emerald-500 dark:border-slate-950" />
                  <p className="text-xs font-semibold uppercase tracking-wide text-emerald-600 dark:text-emerald-400">{a.time}</p>
                  <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{a.title}</p>
                </li>
              ))}
            </ol>
          </section>
        </div>

        {/* Right: sticky registration form */}
        <div id="register" className="lg:col-span-1">
          <div className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm lg:sticky lg:top-8 dark:border-slate-800 dark:bg-slate-900">
            <h2 className="text-lg font-semibold text-slate-900 dark:text-white">Register for this event</h2>
            <p className="mb-5 mt-1 text-sm text-slate-500 dark:text-slate-400">Save your spot — it only takes a minute.</p>
            <RegistrationForm event={event} />
          </div>
        </div>
      </main>

      <footer className="border-t border-slate-200 py-6 text-center text-xs text-slate-400 dark:border-slate-800">
        © 2024 ZoikoStream. All rights reserved.
      </footer>
    </div>
  );
}
