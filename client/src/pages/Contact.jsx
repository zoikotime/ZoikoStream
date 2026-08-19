import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  MessageSquare, CalendarDays, ArrowRight, UserCircle, Globe, Send, Lock,
  Building2, Code, ShieldCheck, FileText, Layers, Handshake, Headset, MoreHorizontal,
  AudioLines,
} from "lucide-react";
import { Logo, Dropdown } from "../ui";

// Contact — where "Talk to an expert" lands. Public and unauthenticated, like the landing
// page it comes from, and it carries the same dark hero over a light body.
//
// ── HOW IT SUBMITS ───────────────────────────────────────────────────────────────────────
// There is no inquiry endpoint on this backend (routers/: admin, auth, commercial, dashboard,
// events, live, organization — none of them accept a contact form), so the form composes a
// mail to the events inbox and hands it to the visitor's mail client. That genuinely delivers
// the inquiry today and adds no server surface.
//
// It is NOT a fake submit: nothing here shows a success state it cannot back up. If this
// should capture leads server-side instead, the only change needed is `submit()` below — a
// POST to a new public route that calls email.py's existing _send(). Everything else stays.
const INBOX = "info@zoikostream.com";
const MAX_MESSAGE = 1500;

// Topics route the inquiry to a team, so they are worded as the reason someone is writing
// rather than as internal department names.
const TOPICS = [
  ["platform", "Platform evaluation & enterprise sales", Building2],
  ["api", "API, SDK & integrations", Code],
  ["events", "Live Events & managed broadcasting", CalendarDays],
  ["security", "Security, privacy & compliance", ShieldCheck],
  ["procurement", "Procurement, licensing & commercial terms", FileText],
  ["architecture", "Technical architecture & implementation", Layers],
  ["partnerships", "Partnerships & strategic opportunities", Handshake],
  ["support", "Existing customer support", Headset],
  ["other", "Other", MoreHorizontal],
];

// Countries come from the platform's own region data rather than a 250-line hardcoded list:
// every two-letter code that Intl can name is a real region, and the names stay correct as
// the world changes without anyone maintaining them here. Computed once at module load.
const COUNTRIES = (() => {
  try {
    const names = new Intl.DisplayNames(["en"], { type: "region" });
    const out = [];
    for (let a = 65; a <= 90; a += 1) {
      for (let b = 65; b <= 90; b += 1) {
        const code = String.fromCharCode(a, b);
        const name = names.of(code);
        if (name && name !== code) out.push([code, name]);
      }
    }
    return out.sort((x, y) => x[1].localeCompare(y[1]));
  } catch {
    // Intl.DisplayNames is ES2021; without it the field falls back to free text below.
    return [];
  }
})();

const SIDE_LINKS = [
  {
    icon: CalendarDays,
    tint: "bg-teal-50 text-teal-600",
    title: "Planning a Live Event?",
    body: "Start your event brief and our team will guide you through every step.",
    topic: "events",
  },
  {
    icon: Code,
    tint: "bg-blue-50 text-blue-600",
    title: "Need technical documentation?",
    body: "Ask for developer docs, API references, SDKs and integration guides.",
    topic: "api",
  },
  {
    icon: Headset,
    tint: "bg-violet-50 text-violet-600",
    title: "Already using ZoikoStream?",
    body: "Get account support, report an issue, or check status.",
    topic: "support",
  },
];

const TRUST = [
  [ShieldCheck, "Enterprise-grade security and privacy controls"],
  [Globe, "Global infrastructure with high reliability"],
  [AudioLines, "Professional live events at any scale"],
  [Headset, "Expert human support when you need it"],
];

const ASSURANCES = [
  [ShieldCheck, "Secure by design", "Built with security and privacy at the core."],
  [Globe, "Global reach", "Deliver to audiences anywhere in the world."],
  [AudioLines, "Professional quality", "HD streaming, captions, recording and replay."],
  [Headset, "Human support", "Real experts from planning to go-live."],
];

const EMPTY = {
  first: "", last: "", email: "", org: "", country: "", topic: "", message: "",
};

const FIELD =
  "h-11 w-full rounded-xl border border-slate-200 bg-white px-3.5 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 hover:border-slate-300 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20";
const LABEL = "mb-1.5 block text-[13px] font-medium text-slate-700";

function Required() {
  return <span className="text-rose-500"> *</span>;
}

export default function Contact() {
  const [form, setForm] = useState(EMPTY);
  const [errors, setErrors] = useState({});
  const topicRef = useRef(null);

  const set = (key) => (value) => {
    setForm((f) => ({ ...f, [key]: value }));
    // Clear a field's error the moment it is edited — re-reading a stale error while typing
    // the fix is the most irritating way a form can behave.
    setErrors((e) => (e[key] ? { ...e, [key]: undefined } : e));
  };

  const topicOptions = useMemo(
    () => TOPICS.map(([value, label, icon]) => ({ value, label, icon })),
    []
  );

  // Jump to the form with the topic already chosen, so a sidebar card is a real shortcut
  // rather than a link to a page that does not exist.
  const pickTopic = (topic) => {
    setForm((f) => ({ ...f, topic }));
    setErrors((e) => ({ ...e, topic: undefined }));
    topicRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const validate = () => {
    const next = {};
    if (!form.first.trim()) next.first = "Required";
    if (!form.last.trim()) next.last = "Required";
    if (!form.email.trim()) next.email = "Required";
    // Deliberately loose: the only thing worth rejecting here is an address that cannot
    // possibly be one. A strict pattern turns away real addresses and gains nothing, since
    // the reply itself is the real validation.
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email.trim())) next.email = "Enter a valid email address";
    if (!form.country) next.country = "Required";
    if (!form.topic) next.topic = "Required";
    if (!form.message.trim()) next.message = "Tell us a little about what you need";
    setErrors(next);
    return Object.keys(next).length === 0;
  };

  const submit = (e) => {
    e.preventDefault();
    if (!validate()) {
      // Send focus to the first problem rather than leaving the operator to hunt for the
      // red text on a two-column form.
      document.querySelector("[aria-invalid='true']")?.focus();
      return;
    }
    const topicLabel = TOPICS.find(([v]) => v === form.topic)?.[1] || form.topic;
    const country = COUNTRIES.find(([code]) => code === form.country)?.[1] || form.country;
    const body = [
      `Name: ${form.first.trim()} ${form.last.trim()}`,
      `Work email: ${form.email.trim()}`,
      `Organization: ${form.org.trim() || "—"}`,
      `Country / region: ${country}`,
      `Topic: ${topicLabel}`,
      "",
      form.message.trim(),
    ].join("\n");
    window.location.href = `mailto:${INBOX}?subject=${encodeURIComponent(
      `[${topicLabel}] Inquiry from ${form.first.trim()} ${form.last.trim()}`
    )}&body=${encodeURIComponent(body)}`;
  };

  return (
    <main className="min-h-screen bg-white">
      {/* ── hero ─────────────────────────────────────────────────────────────── */}
      <section className="relative isolate overflow-hidden bg-[#050a14] text-white">
        <Arcs />

        <div className="absolute left-6 top-6 z-20 lg:left-10 lg:top-7">
          <Logo height="h-7" />
        </div>

        <div className="relative z-10 mx-auto max-w-7xl px-6 pb-12 pt-28 lg:px-10 lg:pb-14 lg:pt-32">
          <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:gap-7">
            <span
              aria-hidden="true"
              className="grid h-16 w-16 shrink-0 place-items-center rounded-2xl border border-teal-400/25 bg-teal-400/10 text-teal-300"
            >
              <MessageSquare className="h-7 w-7" />
            </span>
            <div className="min-w-0">
              <p className="text-[11px] font-semibold uppercase tracking-[0.2em] text-teal-300">
                Contact ZoikoStream
              </p>
              <h1 className="mt-2.5 text-[2rem] font-bold leading-tight tracking-tight sm:text-[2.6rem]">
                Talk to the right expert, faster.
              </h1>
              <p className="mt-3 max-w-xl text-[15px] leading-relaxed text-white/70">
                Tell us what you&rsquo;re trying to accomplish.
                <br className="hidden sm:block" />
                We&rsquo;ll route your inquiry to the ZoikoStream team best equipped to help.
              </p>

              <button
                type="button"
                onClick={() => pickTopic("events")}
                className="group mt-7 inline-flex items-center gap-3 rounded-xl border border-white/15 bg-white/[0.04] px-4 py-3 text-sm transition hover:border-white/30 hover:bg-white/[0.08]"
              >
                <CalendarDays className="h-[18px] w-[18px] shrink-0 text-white/70" aria-hidden="true" />
                <span className="font-medium text-white/85">Planning a Live Event?</span>
                <span className="inline-flex items-center gap-1.5 font-semibold text-teal-300">
                  Start your event brief
                  <ArrowRight
                    className="h-4 w-4 transition-transform duration-200 group-hover:translate-x-1 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0"
                    aria-hidden="true"
                  />
                </span>
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── form + sidebar ───────────────────────────────────────────────────── */}
      <section className="mx-auto grid max-w-7xl gap-6 px-6 py-10 lg:grid-cols-[minmax(0,1fr)_360px] lg:px-10 lg:py-14">
        <form
          onSubmit={submit}
          noValidate
          className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm sm:p-8"
        >
          <h2 className="flex items-center gap-2.5 text-[19px] font-semibold text-slate-900">
            <UserCircle className="h-6 w-6 shrink-0 text-teal-600" aria-hidden="true" />
            Tell us what you need
          </h2>

          <div className="mt-7 grid gap-5 sm:grid-cols-2">
            <Field
              label="First name"
              required
              value={form.first}
              onChange={set("first")}
              error={errors.first}
              placeholder="First name"
              autoComplete="given-name"
            />
            <Field
              label="Last name"
              required
              value={form.last}
              onChange={set("last")}
              error={errors.last}
              placeholder="Last name"
              autoComplete="family-name"
            />
            <Field
              label="Work email"
              required
              type="email"
              value={form.email}
              onChange={set("email")}
              error={errors.email}
              placeholder="name@organization.com"
              autoComplete="email"
            />
            <Field
              label="Organization"
              value={form.org}
              onChange={set("org")}
              placeholder="Organization name"
              autoComplete="organization"
            />

            <div>
              <label className={LABEL} htmlFor="ct-country">
                Country / region
                <Required />
              </label>
              <div className="relative">
                <Globe
                  className="pointer-events-none absolute left-3.5 top-1/2 h-[18px] w-[18px] -translate-y-1/2 text-slate-400"
                  aria-hidden="true"
                />
                <select
                  id="ct-country"
                  value={form.country}
                  onChange={(e) => set("country")(e.target.value)}
                  aria-invalid={errors.country ? true : undefined}
                  className={`${FIELD} cursor-pointer appearance-none pl-11 pr-9 ${
                    errors.country ? "border-rose-400" : ""
                  } ${form.country ? "text-slate-800" : "text-slate-400"}`}
                >
                  <option value="">Select country / region</option>
                  {COUNTRIES.map(([code, name]) => (
                    <option key={code} value={code}>
                      {name}
                    </option>
                  ))}
                </select>
                <Chevron />
              </div>
              <Error>{errors.country}</Error>
            </div>

            <div ref={topicRef} className="scroll-mt-24">
              <label className={LABEL} id="ct-topic-label">
                What can we help with?
                <Required />
              </label>
              <Dropdown
                label="What can we help with?"
                size="lg"
                width="w-[min(24rem,calc(100vw-3rem))]"
                placeholder="Select an option"
                value={form.topic}
                onChange={set("topic")}
                options={topicOptions}
                triggerClassName={errors.topic ? "!border-rose-400" : ""}
              />
              <Error>{errors.topic}</Error>
            </div>
          </div>

          <div className="mt-5">
            <label className={LABEL} htmlFor="ct-message">
              How can we help?
              <Required />
            </label>
            <textarea
              id="ct-message"
              rows={6}
              maxLength={MAX_MESSAGE}
              value={form.message}
              onChange={(e) => set("message")(e.target.value)}
              aria-invalid={errors.message ? true : undefined}
              placeholder="Describe your goals, environment, or the details of your inquiry."
              className={`w-full rounded-xl border bg-white px-3.5 py-3 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 hover:border-slate-300 focus:border-teal-500 focus:ring-2 focus:ring-teal-500/20 ${
                errors.message ? "border-rose-400" : "border-slate-200"
              }`}
            />
            <div className="mt-1.5 flex items-start justify-between gap-4">
              <Error>{errors.message}</Error>
              <span className="shrink-0 text-[11px] tabular-nums text-slate-400">
                {form.message.length} / {MAX_MESSAGE}
              </span>
            </div>
          </div>

          <button
            type="submit"
            className="group mt-6 inline-flex w-full items-center justify-center gap-2.5 rounded-xl bg-gradient-to-r from-teal-500 to-blue-600 px-6 py-3.5 text-[15px] font-semibold text-white shadow-md shadow-blue-600/20 transition-all duration-200 hover:from-teal-400 hover:to-blue-500 hover:shadow-lg hover:shadow-blue-600/30 active:scale-[0.99] motion-reduce:transition-none motion-reduce:active:scale-100"
          >
            <Send
              className="h-[18px] w-[18px] transition-transform duration-200 group-hover:translate-x-0.5 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0"
              aria-hidden="true"
            />
            Send inquiry
          </button>

          {/* The Privacy Notice page went with the rest of the marketing site, so this names
              the document without linking to a 404. Point it at a URL and it becomes a link. */}
          <p className="mt-4 text-[12px] text-slate-500">
            By submitting this form, you acknowledge the{" "}
            <span className="font-medium text-slate-600">ZoikoStream Privacy Notice</span>.
          </p>

          <p className="mt-5 flex items-start gap-3 rounded-xl border border-blue-100 bg-blue-50/70 p-4 text-[13px] leading-relaxed text-slate-600">
            <span
              aria-hidden="true"
              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-white text-blue-600 ring-1 ring-blue-100"
            >
              <Lock className="h-[15px] w-[15px]" />
            </span>
            For your security, please do not include passwords, API keys, credentials, or other
            sensitive production data.
          </p>
        </form>

        <aside className="space-y-6">
          <div>
            <h2 className="text-[17px] font-semibold text-slate-900">Looking for something specific?</h2>
            <span aria-hidden="true" className="mt-2 block h-0.5 w-9 rounded-full bg-teal-500" />

            <ul className="mt-5 divide-y divide-slate-100 rounded-2xl border border-slate-200 bg-white">
              {SIDE_LINKS.map(({ icon: Icon, tint, title, body, topic }) => (
                <li key={title}>
                  <button
                    type="button"
                    onClick={() => pickTopic(topic)}
                    className="group flex w-full items-start gap-3.5 p-5 text-left transition hover:bg-slate-50"
                  >
                    <span
                      aria-hidden="true"
                      className={`grid h-10 w-10 shrink-0 place-items-center rounded-xl ${tint}`}
                    >
                      <Icon className="h-[18px] w-[18px]" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-[14px] font-semibold text-slate-900">{title}</span>
                      <span className="mt-1 block text-[13px] leading-relaxed text-slate-500">{body}</span>
                    </span>
                    <ArrowRight
                      className="mt-1 h-4 w-4 shrink-0 text-slate-300 transition-transform duration-200 group-hover:translate-x-1 group-hover:text-slate-500 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0"
                      aria-hidden="true"
                    />
                  </button>
                </li>
              ))}
            </ul>
          </div>

          <div className="rounded-2xl border border-slate-200 bg-white p-5">
            <h2 className="text-[15px] font-semibold text-slate-900">
              Why organizations trust ZoikoStream
            </h2>
            <ul className="mt-4 divide-y divide-slate-100">
              {TRUST.map(([Icon, label]) => (
                <li key={label} className="flex items-start gap-3 py-3.5 first:pt-0 last:pb-0">
                  <Icon className="mt-0.5 h-[17px] w-[17px] shrink-0 text-teal-600" aria-hidden="true" />
                  <span className="text-[13px] leading-relaxed text-slate-600">{label}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Existing customers reach the console from here — this page has no nav bar. */}
          <p className="text-center text-[13px] text-slate-500">
            Already have an account?{" "}
            <Link to="/login" className="font-semibold text-teal-600 hover:text-teal-700">
              Sign in
            </Link>
          </p>
        </aside>
      </section>

      {/* ── assurances ───────────────────────────────────────────────────────── */}
      <section className="border-t border-slate-200 bg-slate-50">
        <ul className="mx-auto grid max-w-7xl gap-8 px-6 py-10 sm:grid-cols-2 lg:grid-cols-4 lg:px-10">
          {ASSURANCES.map(([Icon, title, body]) => (
            <li key={title} className="flex items-start gap-3.5">
              <span
                aria-hidden="true"
                className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-white text-teal-600 ring-1 ring-slate-200"
              >
                <Icon className="h-[19px] w-[19px]" />
              </span>
              <div className="min-w-0">
                <p className="text-[14px] font-semibold text-slate-900">{title}</p>
                <p className="mt-1 text-[13px] leading-relaxed text-slate-500">{body}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function Field({ label, required, error, value, onChange, ...rest }) {
  const id = `ct-${label.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <div>
      <label className={LABEL} htmlFor={id}>
        {label}
        {required && <Required />}
      </label>
      <input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-invalid={error ? true : undefined}
        className={`${FIELD} ${error ? "border-rose-400" : ""}`}
        {...rest}
      />
      <Error>{error}</Error>
    </div>
  );
}

function Error({ children }) {
  if (!children) return null;
  return <p className="mt-1.5 text-[12px] font-medium text-rose-600">{children}</p>;
}

function Chevron() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 20 20"
      className="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
    >
      <path d="M5 7.5 10 12.5 15 7.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// The design's teal arc sweep in the hero's top-right corner.
function Arcs() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute -right-20 -top-24 h-[26rem] w-[26rem] rounded-full bg-teal-500/10 blur-[110px]" />
      <svg
        className="absolute -right-10 -top-16 h-[24rem] w-[34rem]"
        viewBox="0 0 560 384"
        fill="none"
      >
        {[0, 26, 52, 78, 104, 130].map((offset, i) => (
          <path
            key={offset}
            d={`M${120 + offset} 384 C ${240 + offset} ${300 - offset * 0.4}, ${380 + offset * 0.5} ${150 - offset * 0.3}, ${560} ${40 - offset * 0.5}`}
            stroke="url(#zk-contact-arc)"
            strokeWidth="1.25"
            opacity={0.5 - i * 0.06}
          />
        ))}
        <defs>
          <linearGradient id="zk-contact-arc" x1="0" y1="384" x2="560" y2="0" gradientUnits="userSpaceOnUse">
            <stop stopColor="#2dd4bf" stopOpacity="0" />
            <stop offset="0.5" stopColor="#2dd4bf" stopOpacity="0.9" />
            <stop offset="1" stopColor="#38bdf8" stopOpacity="0.2" />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
}
