import { Link } from "react-router-dom";
import { FiShield, FiUser, FiHeadphones } from "react-icons/fi";
import { Logo } from "../ui";

// Public landing page — the entire signed-out surface. The marketing site (homepage
// sections plus the Platform/Solutions/Pricing/Company pages behind the old nav bar) was
// removed; this hero replaces all of it, and "Plan your live event" is the way into signup.
//
// Deliberately dark regardless of the theme toggle: it is a single art-directed hero, not
// a console screen, so it does not carry light/dark variants.

// Static preview of the checks the console runs before an event goes live — the header
// label says "illustrative" because these are not this visitor's real events.
const READINESS = [
  ["Source feed", "Ready"],
  ["Audience access", "Configured"],
  ["Captions", "Ready"],
  ["Recording & replay", "Configured"],
  ["Event support", "Available"],
];

// Readiness green sits outside the Tailwind palette on purpose: index.css remaps
// emerald -> violet and teal -> pink for the brand, so both named scales are the wrong
// colour here.
const READY = "#34d399";

// Shared CTA geometry; the two buttons differ only in fill.
const CTA =
  "inline-flex items-center justify-center rounded-xl px-8 py-4 text-base font-semibold transition-all duration-200 hover:-translate-y-0.5 active:translate-y-0 motion-reduce:hover:translate-y-0";

export default function Landing() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-[#050a14] text-white">
      <Backdrop />

      <div className="absolute left-6 top-6 z-20 lg:left-10 lg:top-8">
        <Logo height="h-7" />
      </div>

      {/* "Talk to an expert" now opens mail rather than the console, so this is the only
          way back in for someone who already has an account. */}
      <Link
        to="/login"
        className="absolute right-6 top-8 z-20 text-sm font-medium text-white/70 transition hover:text-white lg:right-10 lg:top-10"
      >
        Sign in
      </Link>

      <div className="relative z-10 mx-auto grid min-h-screen max-w-7xl gap-12 px-6 pb-14 pt-28 lg:grid-cols-2 lg:items-center lg:gap-10 lg:px-10 lg:pb-16 lg:pt-32">
        <div className="zk-fade-in max-w-xl">
          {/* Live indicator: blinking dot with an expanding halo, and the pill's own border
              breathing with it. Both animations are disabled under prefers-reduced-motion
              (see index.css) — a permanently blinking element is hostile to read past. */}
          <span className="zk-badge-glow inline-flex items-center gap-2.5 rounded-full border border-white/25 px-5 py-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-white/80">
            <span className="relative grid h-2 w-2 shrink-0 place-items-center" aria-hidden="true">
              <span className="zk-pulse-ring absolute inset-0 rounded-full bg-sky-400" />
              <span className="relative h-2 w-2 rounded-full bg-sky-400 shadow-[0_0_8px_2px_rgba(56,189,248,0.65)]" />
            </span>
            ZoikoStream &bull; Live Events
          </span>

          <h1 className="mt-8 text-[2.75rem] font-bold leading-[1.02] tracking-tight sm:text-6xl">
            Bring every important moment to{" "}
            <span className="bg-gradient-to-r from-sky-400 to-blue-500 bg-clip-text text-transparent">
              everyone
            </span>{" "}
            who matters.
          </h1>

          <p className="mt-7 max-w-lg text-base leading-relaxed text-white/70 sm:text-[17px]">
            Professionally managed live events with secure audience access, captions,
            translation, recording and replay&mdash;built for occasions that need to work
            the first time.
          </p>

          <div className="mt-9 flex flex-col gap-4 sm:flex-row">
            <Link
              to="/signup"
              className={`${CTA} bg-gradient-to-r from-blue-600 to-cyan-400 text-white shadow-lg shadow-blue-950/50 hover:from-blue-500 hover:to-cyan-300`}
            >
              Plan your live event
            </Link>
            {/* Opens the visitor's mail client addressed to the events team's shared
                inbox, so an agent picks it up directly. A subject is prefilled to make
                the enquiry sortable on arrival. */}
            <a
              href="mailto:info@zoikostream.com?subject=Live%20event%20enquiry"
              className={`${CTA} border border-white/20 bg-white/[0.04] text-white hover:border-white/35 hover:bg-white/10`}
            >
              Talk to an expert
            </a>
          </div>

          <Highlights />
        </div>

        <div className="zk-fade-in w-full max-w-md lg:justify-self-end lg:self-end">
          <div className="rounded-2xl border border-white/10 bg-[#0b1320]/90 p-6 shadow-2xl shadow-black/50 backdrop-blur-xl sm:p-7">
            <div className="flex items-baseline justify-between gap-4">
              <h2 className="text-[17px] font-semibold">Live Event Readiness</h2>
              <span className="shrink-0 text-[9px] font-semibold uppercase tracking-[0.14em] text-blue-400">
                Illustrative platform view
              </span>
            </div>

            <dl className="mt-5">
              {READINESS.map(([label, state]) => (
                <div
                  key={label}
                  className="flex items-center justify-between gap-4 border-t border-white/10 py-3 first:border-t-0 first:pt-0"
                >
                  <dt className="text-sm text-white/70">{label}</dt>
                  <dd
                    className="flex items-center gap-2 text-sm font-medium"
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
          </div>
        </div>
      </div>
    </main>
  );
}

// The four assurances under the buttons. Two of the marks are drawn as a ringed glyph
// (accessibility, record) to match the design, so this is a list of nodes rather than
// icon components.
function Highlights() {
  const ring = "grid h-5 w-5 shrink-0 place-items-center rounded-full border border-white/70";
  const items = [
    [<FiShield className="text-[17px]" aria-hidden="true" />, "Secure delivery"],
    [
      <span className={ring} aria-hidden="true">
        <FiUser className="text-[10px]" />
      </span>,
      "Accessible experiences",
    ],
    [
      <span className={ring} aria-hidden="true">
        <span className="h-2 w-2 rounded-full bg-white" />
      </span>,
      "Recording & replay",
    ],
    [<FiHeadphones className="text-[17px]" aria-hidden="true" />, "Expert support"],
  ];

  // All four sit on one line, which needs more width than the headline column provides —
  // the same relationship the design has. From xl up the row sizes to its content and
  // overflows the column into the clear space before the readiness card. Narrower than
  // that there is no clear space to overflow into, so it wraps rather than collide.
  return (
    <ul className="mt-12 flex flex-wrap items-center gap-x-2.5 gap-y-3 text-[13px] text-white/85 xl:w-max xl:flex-nowrap xl:whitespace-nowrap">
      {items.map(([mark, label], i) => (
        <li key={label} className="flex items-center gap-2">
          {i > 0 && <span className="mr-0.5 text-white/30" aria-hidden="true">&bull;</span>}
          {mark}
          {label}
        </li>
      ))}
    </ul>
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

      {/* Vignette: keeps the left third dark enough for body copy to hold contrast. */}
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
