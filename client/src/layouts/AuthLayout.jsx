import { Link, Outlet } from "react-router-dom";
import { BarChart3, ChevronDown, Globe, Layers, ShieldCheck } from "lucide-react";
import { Logo } from "../ui";

// Shared shell for every auth page — dark brand panel (left, ~58%) + light form panel
// (right, ~42%) over a full-width footer. The form is rendered via <Outlet /> so Login /
// Create Organization / Forgot Password / Accept Invitation all share the same framing
// without duplicating it.

const FEATURES = [
  [ShieldCheck, "Enterprise-grade security", "SOC 2 Type II · SSO · End-to-end encryption"],
  [Globe, "Global delivery at scale", "Low-latency CDN · 99.99% uptime SLA"],
  [BarChart3, "Real-time analytics", "Real-time insights · Deep engagement metrics"],
];

// Generic marks, not invented company logos — the reference's monochrome trust row.
const TRUST_MARKS = [ShieldCheck, Globe, Layers, BarChart3];

// The reference footer, label for label. `to` is null where no page exists yet — the
// marketing site (Product / Solutions / Resources / Pricing / Company) was removed with the
// old nav bar, so those render as plain text rather than links that 404. Give any of them a
// route here and it becomes a real link with no other change. `menu` draws the caret.
const FOOTER_NAV = [
  { label: "Product", to: null, menu: true },
  { label: "Solutions", to: null, menu: true },
  { label: "Resources", to: null, menu: true },
  { label: "Pricing", to: null },
  { label: "Company", to: null, menu: true },
  { label: "Support", to: "/contact" },
  { label: "Status", to: null },
];

export default function AuthLayout() {
  // Pointer-follow spotlight over the brand panel — writes CSS vars straight to the node,
  // so it costs no re-render. (Kept from the original AuthLayout.)
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
    e.currentTarget.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
  };

  return (
    <div className="flex min-h-screen flex-col bg-white lg:h-screen lg:overflow-hidden dark:bg-slate-950">
      <div className="grid flex-1 lg:min-h-0 lg:grid-cols-[58fr_42fr]">
        {/* ── Brand panel ─────────────────────────────────────────────────────── */}
        <div
          onMouseMove={onMove}
          className="relative hidden flex-col overflow-hidden bg-[#050a14] p-10 text-white lg:flex xl:p-12"
        >
          <StageBackdrop />
          {/* House light that follows the cursor. */}
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0"
            style={{
              background:
                "radial-gradient(420px circle at var(--mx,50%) var(--my,30%), rgba(196,181,253,0.14), transparent 65%)",
            }}
          />

          <Link to="/" className="zk-fade-in relative shrink-0 self-start" aria-label="ZoikoStream home">
            <Logo height="h-10" />
          </Link>

          {/* Marketing copy, not document headings: each auth page keeps its own single
              <h1> ("Welcome back", "Reset Password", …) and this panel is hidden on mobile. */}
          <div className="zk-fade-in relative mt-10 shrink-0 xl:mt-12">
            <p className="max-w-[16ch] text-[2.5rem] font-bold leading-[1.04] tracking-tight xl:text-5xl">
              Stream live events with confidence
              <span className="text-fuchsia-400 [text-shadow:0_0_22px_rgba(232,121,249,0.75)]">.</span>
            </p>
            <p className="mt-6 max-w-[600px] text-[15px] leading-relaxed text-white/70 xl:text-base">
              Host, manage, monetize, and analyze live &amp; hybrid events from one powerful
              platform&mdash;built for organizations that demand performance at scale.
            </p>
          </div>

          {/* Icon beside the title, blurb under both — the reference's card, not a stacked
              tile. */}
          <ul className="zk-fade-in relative mt-8 grid shrink-0 grid-cols-3 gap-3.5 xl:mt-10">
            {FEATURES.map(([Icon, title, blurb]) => (
              <li
                key={title}
                className="group rounded-xl border border-white/10 bg-white/[0.05] p-4 backdrop-blur-md transition duration-200 hover:-translate-y-0.5 hover:border-violet-400/40 hover:bg-white/[0.09] hover:shadow-[0_10px_30px_-12px_rgba(139,92,246,0.6)] motion-reduce:transition-none motion-reduce:hover:translate-y-0"
              >
                <div className="flex items-start gap-3">
                  <Icon
                    aria-hidden="true"
                    className="mt-0.5 h-6 w-6 shrink-0 text-violet-400 transition-colors duration-200 group-hover:text-violet-300"
                  />
                  <p className="text-[13.5px] font-semibold leading-tight">{title}</p>
                </div>
                <p className="mt-2.5 text-[11.5px] leading-relaxed text-white/55">{blurb}</p>
              </li>
            ))}
          </ul>

          {/* Event-control visual across the bottom (decorative only), with the trust row
              reading over it on its own scrim. */}
          <div className="relative mt-8 flex min-h-0 flex-1 items-end">
            <ControlRoomMock />
            <div className="relative w-full pb-1">
              <div
                aria-hidden="true"
                className="pointer-events-none absolute -inset-x-10 -bottom-10 -top-8 bg-gradient-to-t from-[#05070f] via-[#05070f]/85 to-transparent xl:-inset-x-12 xl:-bottom-12"
              />
              <p className="relative text-sm font-medium text-white/80">Trusted by organizations worldwide.</p>
              <ul className="relative mt-4 flex items-center gap-5 text-white/30" aria-hidden="true">
                {TRUST_MARKS.map((Icon, i) => (
                  <li key={Icon.displayName || i} className="flex items-center gap-5">
                    {i > 0 && <span className="h-5 w-px bg-white/15" />}
                    <Icon className="h-6 w-6" />
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        {/* ── Form panel ──────────────────────────────────────────────────────── */}
        <div className="relative flex flex-col bg-slate-50/60 lg:min-h-0 dark:bg-slate-950">
          {/* my-auto on the child (not items-center) so a tall form scrolls from its top
              instead of having its head clipped. */}
          <div className="zk-scroll-thin flex flex-1 justify-center overflow-y-auto px-6 py-6 lg:min-h-0">
            <div className="my-auto w-full max-w-[560px]">
              <Link to="/" className="mb-8 flex justify-center lg:hidden" aria-label="ZoikoStream home">
                <img src="/zoiko-logo.png" alt="ZoikoStream" className="h-10 w-auto" />
              </Link>
              <Outlet />
            </div>
          </div>
        </div>
      </div>

      <footer className="shrink-0 border-t border-slate-200 bg-white px-6 py-4 dark:border-slate-800 dark:bg-slate-950">
        <div className="flex flex-col items-center justify-between gap-3 text-xs text-slate-500 sm:flex-row dark:text-slate-400">
          <p>&copy; 2026 ZoikoStream. All rights reserved.</p>
          <nav aria-label="Footer" className="flex flex-wrap items-center justify-center gap-x-7 gap-y-2">
            {FOOTER_NAV.map(({ label, to, menu }) => {
              const body = (
                <>
                  {label}
                  {menu && <ChevronDown className="h-3.5 w-3.5 opacity-60" aria-hidden="true" />}
                </>
              );
              return to ? (
                <Link
                  key={label}
                  to={to}
                  className="inline-flex items-center gap-1 rounded transition-colors duration-200 hover:text-violet-700 dark:hover:text-violet-400"
                >
                  {body}
                </Link>
              ) : (
                <span key={label} className="inline-flex items-center gap-1">
                  {body}
                </span>
              );
            })}
          </nav>
        </div>
      </footer>
    </div>
  );
}

// Stage lighting rig — every beam hangs off the same truss point, so they read as one fan
// rather than unrelated streaks: [rotation deg, width px, opacity].
const BEAMS = [
  [-54, 52, 0.30], [-43, 26, 0.50], [-33, 74, 0.20], [-24, 30, 0.46], [-14, 58, 0.26],
  [-5, 24, 0.52], [4, 64, 0.22], [13, 30, 0.44], [23, 78, 0.20], [33, 26, 0.42], [45, 56, 0.24],
];
// Moving heads on the truss.
const RIG = [[52, 3], [58, 6], [64, 2], [70, 6], [76, 3], [82, 7], [88, 3], [94, 6]];

// The reference photograph approximated in CSS — no concert still ships with the app, so the
// venue is drawn: a beam fan off the truss, haze, and a crowd line under it.
function StageBackdrop() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0 overflow-hidden">
      <div className="absolute inset-0 bg-[linear-gradient(150deg,#070b1c_0%,#0a0d26_38%,#160f38_74%,#231044_100%)]" />

      {/* Optional photograph. Absent by default, in which case this paints nothing and the
          drawn venue below shows through — the same graceful-degrade the landing hero uses.
          Drop a concert still at client/public/auth-stage.jpg and it takes over instantly. */}
      <div className="absolute inset-0 bg-[url('/auth-stage.jpg')] bg-cover bg-center opacity-80" />

      {/* House glow behind the stage. */}
      <div className="absolute inset-x-0 top-0 h-3/4 bg-[radial-gradient(60%_70%_at_66%_2%,rgba(139,92,246,0.5),transparent_72%)]" />
      <div className="zk-float absolute right-[12%] top-[6%] h-64 w-64 rounded-full bg-fuchsia-500/25 blur-[90px]" />
      <div className="zk-drift absolute right-[40%] top-[20%] h-72 w-72 rounded-full bg-indigo-500/25 blur-[100px]" />

      {/* The fan. Each beam is anchored at the truss and rotated about its own top edge. */}
      {BEAMS.map(([rotate, width, opacity]) => (
        <span
          key={`beam-${rotate}`}
          style={{
            left: "68%",
            top: "-8%",
            width: `${width}px`,
            height: "125%",
            opacity,
            transform: `translateX(-50%) rotate(${rotate}deg)`,
          }}
          className="absolute origin-top rounded-b-[50%] bg-gradient-to-b from-violet-50/90 via-violet-300/25 to-transparent blur-[9px]"
        />
      ))}

      {/* Lamp flares at the truss line. */}
      {RIG.map(([x, y]) => (
        <span
          key={`rig-${x}`}
          style={{ left: `${x}%`, top: `${y}%` }}
          className="absolute h-2 w-2 rounded-full bg-violet-50 shadow-[0_0_18px_7px_rgba(196,181,253,0.55)]"
        />
      ))}

      {/* Haze the beams travel through. */}
      <div className="absolute inset-x-0 top-[8%] h-2/3 bg-[radial-gradient(45%_55%_at_66%_35%,rgba(167,139,250,0.22),transparent_75%)]" />

      {/* Crowd: two rows of heads along a line above the monitor wall, the back row smaller
          and dimmer so the block reads as depth rather than a scallop. */}
      <div
        className="absolute inset-x-0 bottom-[30%] h-14"
        style={{
          backgroundImage: "radial-gradient(circle at 50% 100%, rgba(6,7,20,0.72) 9px, transparent 10px)",
          backgroundSize: "26px 26px",
          backgroundRepeat: "repeat-x",
          backgroundPosition: "bottom",
        }}
      />
      <div
        className="absolute inset-x-0 bottom-[27%] h-16"
        style={{
          backgroundImage: "radial-gradient(circle at 50% 100%, rgba(3,4,12,0.92) 13px, transparent 14px)",
          backgroundSize: "36px 36px",
          backgroundRepeat: "repeat-x",
          backgroundPosition: "bottom",
        }}
      />

      {/* Copy-side vignette: near-black under the text, opening up toward the venue. */}
      <div className="absolute inset-0 bg-[linear-gradient(to_right,#05070f_0%,rgba(5,7,15,0.93)_22%,rgba(5,7,15,0.66)_52%,rgba(5,7,15,0.34)_100%)]" />
      <div className="absolute inset-x-0 bottom-0 h-1/3 bg-gradient-to-t from-[#05070f] to-transparent" />
      <div
        className="absolute inset-0 opacity-[0.06]"
        style={{
          backgroundImage: "radial-gradient(rgba(196,181,253,0.6) 1px, transparent 1px)",
          backgroundSize: "22px 22px",
        }}
      />
    </div>
  );
}

// Faux control-room monitors: [height class, kind]. Decorative — no data, no behaviour.
const PANELS = [
  ["h-[62%]", "rows"], ["h-[80%]", "bars"], ["h-[94%]", "chart"], ["h-full", "tiles"],
  ["h-[86%]", "chart2"], ["h-[68%]", "rows2"],
];
const BAR_HEIGHTS = [38, 62, 45, 80, 55, 92, 48, 70, 60, 85];

// The operator's desk: a monitor wall across the foot of the panel with the silhouette of
// whoever is running the show in front of it, bleeding off the bottom edge.
function ControlRoomMock() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none absolute inset-x-0 bottom-0 -mb-10 -mx-10 h-[78%] xl:-mb-12 xl:-mx-12"
    >
      <div className="absolute inset-x-6 bottom-0 top-0 flex items-end gap-2.5">
        {PANELS.map(([height, kind]) => (
          <div
            key={kind}
            className={`${height} flex-1 overflow-hidden rounded-t-xl border border-b-0 border-white/10 bg-[#080d1c]/95 p-2.5 shadow-[0_-20px_70px_-24px_rgba(124,58,237,0.55)]`}
          >
            <div className="flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-fuchsia-400/80" />
              <span className="h-1 w-8 rounded-full bg-white/20" />
              <span className="ml-auto h-1 w-4 rounded-full bg-white/10" />
            </div>
            <PanelBody kind={kind} />
          </div>
        ))}
      </div>

      {/* Operator, dead flat black so it reads as a silhouette against the screens. */}
      <div className="absolute bottom-0 left-[6%] h-[42%] w-56">
        <span className="absolute bottom-[52%] left-1/2 h-16 w-16 -translate-x-1/2 rounded-full bg-[#02030a]" />
        <span className="absolute inset-x-0 bottom-0 h-[54%] rounded-t-[52%] bg-[#02030a]" />
      </div>
    </div>
  );
}

function PanelBody({ kind }) {
  if (kind.startsWith("bars") || kind.startsWith("chart")) {
    const flip = kind.endsWith("2");
    return (
      <div className="mt-2.5 flex h-14 items-end gap-[3px]">
        {BAR_HEIGHTS.map((h, i) => (
          <span
            key={`${kind}-${i}`}
            style={{ height: `${flip ? 120 - h : h}%` }}
            className="flex-1 rounded-sm bg-gradient-to-t from-violet-600/70 to-fuchsia-400/70"
          />
        ))}
      </div>
    );
  }
  if (kind === "tiles") {
    return (
      <div className="mt-2.5 grid grid-cols-2 gap-1.5">
        {[0, 1, 2, 3].map((i) => (
          <span key={i} className="h-7 rounded border border-white/10 bg-violet-500/12" />
        ))}
      </div>
    );
  }
  return (
    <div className="mt-2.5 space-y-1.5">
      {(kind === "rows2" ? [70, 92, 55, 80, 62] : [90, 65, 78, 50, 84]).map((w, i) => (
        <span key={i} style={{ width: `${w}%` }} className="block h-1.5 rounded-full bg-white/12" />
      ))}
    </div>
  );
}
