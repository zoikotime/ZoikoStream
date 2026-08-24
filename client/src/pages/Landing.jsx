import { Link } from "react-router-dom";
import {
  Shield, Users, Cloud, Headset, Calendar, ChevronRight, Radio, Video, Captions,
  ShieldCheck, Lock, Globe, Flame, Church, Gem, GraduationCap, Landmark, Briefcase,
} from "lucide-react";
import { Logo } from "../ui";

// Public landing page — the entire signed-out surface. The marketing site (homepage
// sections plus the Platform/Solutions/Pricing/Company pages behind the old nav bar) was
// removed; this page replaces all of it, and "Plan your live event" is the way into signup.
//
// Deliberately dark regardless of the theme toggle: it is a single art-directed page, not
// a console screen, so it does not carry light/dark variants.
//
// Three bands, in the order a visitor needs them: what we do (hero) → whether it covers my
// occasion (event types) → can I trust you with it (assurances).

// Readiness preview. STATIC — a signed-out visitor has no session to report on, so these are
// illustrative, which the card header says out loud. The one word of honesty is deliberate:
// every other status surface in this product em-dashes what it cannot measure.
const READINESS = [
  [Video, "Source", "Ready"],
  [Users, "Audience access", "Configured"],
  [Captions, "Captions", "Ready"],
  [Cloud, "Recording & replay", "Configured"],
  [Headset, "Event support", "Available"],
];

// The four assurances under the buttons.
const HIGHLIGHTS = [
  [Shield, "Secure delivery"],
  [Users, "Accessible experiences"],
  [Cloud, "Recording & replay"],
  [Headset, "Expert support"],
];

// Occasions this platform is actually built for. These mirror the event categories the
// scheduler offers (see CATEGORIES in pages/organization/CreateEventModal) rather than being
// an invented list, so what the landing page promises is what the console can create.
const OCCASIONS = [
  [Flame, "Memorials", "Honor and remember with dignity"],
  [Church, "Worship Services", "Bring your community together online"],
  [Gem, "Weddings & Celebrations", "Celebrate love with those you invite"],
  [GraduationCap, "Graduations", "Milestones shared worldwide"],
  [Landmark, "Civic Events", "Connect and inform your community"],
  [Users, "Conferences", "Engage audiences anywhere"],
  [Briefcase, "Corporate Events", "Communicate and connect globally"],
];

const TRUST = [
  [Lock, "Enterprise-grade security"],
  [Globe, "Global infrastructure"],
  [ShieldCheck, "Privacy by design"],
  [Headset, "Human support"],
];

// Readiness green sits outside the Tailwind palette on purpose: index.css remaps
// emerald -> violet and teal -> pink for the brand, so both named scales are the wrong
// colour here.
const READY = "#34d399";

export default function Landing() {
  return (
    <main className="bg-[#050a14] text-white">
      {/* ── hero ─────────────────────────────────────────────────────────────── */}
      <section className="relative isolate overflow-hidden">
        <Backdrop />

        <div className="absolute left-6 top-6 z-20 lg:left-10 lg:top-8">
          <Logo height="h-7" />
        </div>

        <div className="relative z-10 mx-auto grid max-w-7xl gap-12 px-6 pb-16 pt-28 lg:grid-cols-2 lg:items-center lg:gap-10 lg:px-10 lg:pb-20 lg:pt-32">
          <div className="zk-fade-in max-w-xl">
            {/* Live indicator: blinking dot with an expanding halo, and the pill's own border
                breathing with it. Both animations are disabled under prefers-reduced-motion
                (see index.css) — a permanently blinking element is hostile to read past. */}
            <span className="zk-badge-glow inline-flex items-center gap-2.5 rounded-full border border-white/25 px-5 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-white/80">
              <span className="relative grid h-2 w-2 shrink-0 place-items-center" aria-hidden="true">
                <span className="zk-pulse-ring absolute inset-0 rounded-full bg-rose-500" />
                <span className="relative h-2 w-2 rounded-full bg-rose-500 shadow-[0_0_8px_2px_rgba(244,63,94,0.6)]" />
              </span>
              ZoikoStream &bull; Live Events
            </span>

            <h1 className="mt-8 text-[2.75rem] font-bold leading-[1.02] tracking-tight sm:text-6xl">
              Bring every{" "}
              <span className="bg-gradient-to-r from-teal-300 via-sky-400 to-violet-500 bg-clip-text text-transparent">
                important moment
              </span>{" "}
              to everyone who matters.
            </h1>

            <p className="mt-7 max-w-lg text-base leading-relaxed text-white/70 sm:text-[17px]">
              Professionally managed live events with secure audience access, captions,
              translation, recording and replay&mdash;built for occasions that need to work
              the first time.
            </p>

            <div className="mt-9 flex flex-col gap-4 sm:flex-row">
              <Link
                to="/signup"
                className="group inline-flex items-center justify-center gap-2.5 rounded-xl bg-gradient-to-r from-teal-400 via-sky-500 to-violet-500 px-7 py-4 text-base font-semibold text-white shadow-lg shadow-violet-950/50 transition-[background-image,box-shadow,transform] duration-200 hover:-translate-y-0.5 hover:shadow-xl hover:shadow-violet-900/50 active:translate-y-0 motion-reduce:transition-none motion-reduce:hover:translate-y-0"
              >
                <Calendar className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                Plan your live event
                <ChevronRight
                  className="h-[18px] w-[18px] shrink-0 transition-transform duration-200 group-hover:translate-x-1 motion-reduce:transition-none motion-reduce:group-hover:translate-x-0"
                  aria-hidden="true"
                />
              </Link>
              {/* Goes to the contact form, which routes the inquiry by topic — a mailto
                  straight from here gave the events team no idea what the visitor wanted. */}
              <Link
                to="/contact"
                className="inline-flex items-center justify-center gap-2.5 rounded-xl border border-white/20 bg-white/[0.04] px-7 py-4 text-base font-semibold text-white transition-all duration-200 hover:-translate-y-0.5 hover:border-white/35 hover:bg-white/10 active:translate-y-0 motion-reduce:transition-none motion-reduce:hover:translate-y-0"
              >
                <Headset className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                Talk to an expert
              </Link>
            </div>

            {/* All four sit on one line, which needs more width than the headline column
                provides — the same relationship the design has. From xl up the row sizes to
                its content and overflows the column into the clear space before the readiness
                card. Narrower than that there is no clear space, so it wraps rather than
                collide. */}
            <ul className="mt-12 flex flex-wrap items-center gap-x-1 gap-y-3 text-[13px] text-white/85 xl:w-max xl:flex-nowrap xl:whitespace-nowrap">
              {HIGHLIGHTS.map(([Icon, label], i) => (
                <li key={label} className="flex items-center gap-2.5">
                  {i > 0 && (
                    <span aria-hidden="true" className="mr-4 h-4 w-px bg-white/20" />
                  )}
                  <Icon className="h-[17px] w-[17px] shrink-0 text-white/70" aria-hidden="true" />
                  {label}
                </li>
              ))}
            </ul>
          </div>

          <div className="zk-fade-in w-full max-w-md lg:justify-self-end">
            <div className="rounded-2xl border border-white/10 bg-[#0b1320]/90 p-6 shadow-2xl shadow-black/50 backdrop-blur-xl sm:p-7">
              <div className="flex items-center gap-2.5">
                <Radio className="h-[18px] w-[18px] shrink-0 text-violet-400" aria-hidden="true" />
                <h2 className="text-[17px] font-semibold">Live Event Readiness</h2>
                <span className="ml-auto shrink-0 text-[10px] font-semibold uppercase tracking-[0.14em] text-white/35">
                  Illustrative
                </span>
              </div>

              <dl className="mt-5">
                {READINESS.map(([Icon, label, state]) => (
                  <div
                    key={label}
                    className="flex items-center justify-between gap-4 border-t border-white/10 py-3 first:border-t-0 first:pt-0"
                  >
                    <dt className="flex min-w-0 items-center gap-2.5 text-sm text-white/75">
                      <Icon className="h-4 w-4 shrink-0 text-white/45" aria-hidden="true" />
                      <span className="truncate">{label}</span>
                    </dt>
                    <dd
                      className="flex shrink-0 items-center gap-2 text-sm font-medium"
                      style={{ color: READY }}
                    >
                      <span
                        className="h-1.5 w-1.5 rounded-full"
                        style={{ backgroundColor: READY }}
                        aria-hidden="true"
                      />
                      {state}
                    </dd>
                  </div>
                ))}
              </dl>

              <p
                className="mt-5 flex items-center gap-2.5 rounded-xl border px-4 py-3 text-sm font-semibold"
                style={{ color: READY, borderColor: "rgba(52,211,153,0.25)", backgroundColor: "rgba(52,211,153,0.06)" }}
              >
                <ShieldCheck className="h-[18px] w-[18px] shrink-0" aria-hidden="true" />
                All systems operational
              </p>
            </div>
          </div>
        </div>
      </section>

      {/* ── occasions ────────────────────────────────────────────────────────── */}
      <section className="relative z-10 mx-auto max-w-7xl px-6 lg:px-10">
        <h2 className="sr-only">Occasions we cover</h2>
        <div className="rounded-2xl border border-white/10 bg-[#080e1a]">
          {/* Seven across only from xl, where the row genuinely fits; the dividers come with
              it, because a border-left on a wrapped grid draws a rule down the middle of a
              row instead of between columns. */}
          <ul className="grid grid-cols-2 gap-y-7 p-6 sm:grid-cols-3 lg:grid-cols-4 lg:p-8 xl:grid-cols-7 xl:gap-y-0">
            {OCCASIONS.map(([Icon, title, blurb]) => (
              <li
                key={title}
                className="group px-2 xl:border-l xl:border-white/10 xl:px-5 xl:first:border-l-0 xl:first:pl-0 xl:last:pr-0"
              >
                <span
                  aria-hidden="true"
                  className="grid h-11 w-11 place-items-center rounded-full border border-white/15 text-sky-300/90 transition-[border-color,color,transform] duration-200 group-hover:-translate-y-0.5 group-hover:border-sky-400/50 group-hover:text-sky-300 motion-reduce:transition-none motion-reduce:group-hover:translate-y-0"
                >
                  <Icon className="h-[19px] w-[19px]" />
                </span>
                <h3 className="mt-4 text-[15px] font-semibold leading-snug">{title}</h3>
                <p className="mt-1.5 text-[13px] leading-relaxed text-white/55">{blurb}</p>
              </li>
            ))}
          </ul>
        </div>
      </section>

      {/* ── assurances ───────────────────────────────────────────────────────── */}
      <section className="relative z-10 mx-auto max-w-7xl px-6 py-8 lg:px-10 lg:py-10">
        <h2 className="sr-only">Why organizations choose ZoikoStream</h2>
        <ul className="flex flex-wrap items-center justify-center gap-x-2 gap-y-4 text-[13px] text-white/65 lg:justify-between">
          {TRUST.map(([Icon, label], i) => (
            <li key={label} className="flex items-center gap-2.5">
              {i > 0 && <span aria-hidden="true" className="mr-4 hidden h-4 w-px bg-white/15 sm:block" />}
              <Icon className="h-[17px] w-[17px] shrink-0 text-white/45" aria-hidden="true" />
              {label}
            </li>
          ))}
          {/* Statement, not a link: the Customers page this would have pointed at was removed
              with the rest of the marketing site, and a chevron that goes nowhere is worse
              than no chevron. */}
          <li className="flex items-center gap-2.5">
            <span aria-hidden="true" className="mr-4 hidden h-4 w-px bg-white/15 sm:block" />
            <span className="font-semibold text-sky-300">Trusted by organizations worldwide</span>
          </li>
        </ul>
      </section>
    </main>
  );
}

// Lighting rig over the venue side: [left%, top%] pairs, front row brighter than the back.
const RIG = [
  [50, 7], [56, 4], [62, 9], [68, 5], [74, 3], [80, 8], [86, 5], [92, 10], [97, 6],
  [53, 16], [59, 13], [65, 18], [71, 14], [77, 17], [83, 12], [89, 18], [95, 15],
];
// Ambient light points drifting over the audience.
const MOTES = [
  [57, 60], [63, 73], [69, 54], [75, 82], [81, 65], [87, 77],
  [93, 58], [66, 89], [79, 92], [90, 86], [54, 47], [97, 70],
];

// The design's photographic stage, approximated in CSS: a lit venue on the right, a dark
// vignette over the text side, and the ribbon sweep across the lower left.
function Backdrop() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* Venue: broad stage wash, the LED wall behind the speaker, and floor spill. */}
      <div className="absolute inset-y-0 right-0 hidden w-3/5 bg-[radial-gradient(70%_65%_at_55%_38%,rgba(37,99,235,0.38),transparent_72%)] lg:block" />
      <div className="absolute right-[22%] top-[18%] hidden h-[22rem] w-[30rem] rounded-[3rem] bg-sky-500/25 blur-[90px] lg:block" />
      <div className="absolute -right-24 bottom-0 hidden h-[26rem] w-[38rem] rounded-full bg-indigo-700/25 blur-[120px] lg:block" />

      {/* Cool fill on the copy side so the headline never sits on flat black. */}
      <div className="absolute -left-40 -top-40 h-[38rem] w-[38rem] rounded-full bg-blue-700/20 blur-[130px]" />
      <div className="absolute inset-x-0 top-0 h-1/2 bg-[radial-gradient(60%_100%_at_50%_0%,rgba(56,132,255,0.14),transparent_70%)]" />

      {/* Rig + motes. */}
      <div className="absolute inset-0 hidden lg:block">
        {RIG.map(([x, y], i) => (
          <span
            key={`rig-${x}-${y}`}
            style={{ left: `${x}%`, top: `${y}%` }}
            className={`absolute h-1.5 w-1.5 rounded-full bg-sky-100 ${
              i % 3 === 0
                ? "opacity-90 shadow-[0_0_14px_5px_rgba(125,211,252,0.5)]"
                : "opacity-60 shadow-[0_0_10px_3px_rgba(125,211,252,0.35)]"
            }`}
          />
        ))}
        {MOTES.map(([x, y]) => (
          <span
            key={`mote-${x}-${y}`}
            style={{ left: `${x}%`, top: `${y}%` }}
            className="absolute h-1 w-1 rounded-full bg-cyan-200/70 shadow-[0_0_8px_3px_rgba(103,232,249,0.3)]"
          />
        ))}
      </div>

      {/* Hero photograph, layered over the CSS venue above. If the file is absent the div
          simply paints nothing and the CSS scene shows through, so a missing image
          degrades instead of breaking the page. */}
      <div className="absolute inset-0 bg-[url('/live-events-hero.jpg')] bg-cover bg-center bg-no-repeat" />

      {/* Vignette: keeps the copy side dark enough for body text to hold contrast over the
          photograph, while letting the venue read from roughly the midpoint out. */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#050a14_0%,rgba(5,10,20,0.92)_55%,rgba(5,10,20,0.78)_100%)] lg:bg-[linear-gradient(to_right,#050a14_0%,#050a14_27%,rgba(5,10,20,0.68)_46%,transparent_68%)]" />
      <div className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-[#050a14] to-transparent" />

      {/* Ribbon sweep, anchored bottom-left and fading out to the right. */}
      <svg
        className="absolute inset-x-0 bottom-0 h-56 w-full"
        viewBox="0 0 1440 220"
        fill="none"
        preserveAspectRatio="none"
      >
        {[0, 14, 28, 42, 56, 70].map((offset, i) => (
          <path
            key={offset}
            d={`M-40 ${210 - offset} C 180 ${150 - offset}, 320 ${96 - offset}, 620 ${74 - offset} S 1080 ${52 - offset}, 1460 ${34 - offset}`}
            stroke="url(#zk-landing-wave)"
            strokeWidth="1.25"
            opacity={0.55 - i * 0.07}
          />
        ))}
        <defs>
          <linearGradient id="zk-landing-wave" x1="0" y1="0" x2="1200" y2="0" gradientUnits="userSpaceOnUse">
            <stop stopColor="#38bdf8" />
            <stop offset="0.4" stopColor="#2563eb" stopOpacity="0.55" />
            <stop offset="1" stopColor="#2563eb" stopOpacity="0" />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
}
