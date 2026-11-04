import { useState } from "react";
import { Eye } from "lucide-react";
import { cx } from "../../ui/tokens";
import { AUTH_ASSETS } from "./authAssets";

// The product composition on the auth hero: a laptop showing the real Organization dashboard,
// a phone showing the real public event page, and a compact floating live-player card.
//
// SIZED BY THE SPACE IT IS GIVEN. The wrapper is a CSS size container ([container-type:size])
// and every device is measured in cqh/cqw, so the whole group scales to whatever height the
// hero has left after the copy — this is what lets the login fit one viewport from 720px to
// 1080px tall without the devices ever overlapping the headline.
//
// The device screens are SCREENSHOTS of the actual app (see authAssets.js), not drawn
// placeholders — re-capture them when those screens change materially.
//
// Motion layers, each on its own clock:
//   parallax (outer) reads --px/--py written by AuthHero's pointer handler
//   bob      (inner) zk-auth-bob keyframes, per-layer amplitude/duration/delay
// Reduced motion removes both (index.css + motion-reduce utilities).

const parallax = (depth) => ({
  transform: `translate3d(calc(var(--px, 0) * ${depth}px), calc(var(--py, 0) * ${depth}px), 0)`,
});
const bob = (amp, dur, delay = 0) => ({
  "--zk-bob": `${amp}px`,
  "--zk-bob-dur": `${dur}s`,
  "--zk-bob-delay": `${delay}s`,
});
const LAYER = "absolute transition-transform duration-700 ease-out motion-reduce:transform-none!";

// Laptop width: as tall as ~80% of the box allows (its height is ~0.66 × width), never wider
// than 80% of the box. The phone overlaps the laptop's left edge.
const LAPTOP_W = "min(122cqh,80cqw)";
// The group (phone overhang 19cqh + laptop) is centred in the box, so it lines up under the
// feature tiles instead of drifting to one edge. OFFSET = the group's left/right margin.
const OFFSET = `max(0px, calc((100cqw - ${LAPTOP_W} - 19cqh) / 2))`;

// variant="full": laptop + phone + live card (lg+). variant="compact": laptop + live card.
export default function ProductShowcase({ variant = "full", className = "" }) {
  const full = variant === "full";
  return (
    <div aria-hidden="true" className={cx("relative [container-type:size]", className)}>
      {/* Stage-light glow behind the devices. */}
      <div className="zk-glow-pulse pointer-events-none absolute bottom-0 right-[8%] h-[85%] w-[70%] rounded-full bg-fuchsia-600/30 blur-[80px]" />

      <div className={cx(LAYER, "bottom-[3cqh]")} style={{ ...parallax(10), width: LAPTOP_W, right: full ? OFFSET : "0px" }}>
        <div className="zk-auth-bob" style={bob(-6, 11)}>
          <Laptop src={AUTH_ASSETS.dashboardShot} />
        </div>
      </div>

      {full && (
        <div
          className={cx(LAYER, "bottom-0 z-10 w-[33cqh]")}
          style={{ ...parallax(16), left: OFFSET }}
        >
          <div className="zk-auth-bob" style={bob(-8, 9, -3)}>
            <Phone src={AUTH_ASSETS.eventShot} />
          </div>
        </div>
      )}

      <div
        className={cx(LAYER, "top-0 z-20 w-[max(156px,min(30cqw,50cqh))]")}
        style={{ ...parallax(22), right: full ? `max(0px, calc(${OFFSET} - 4cqw))` : "0px" }}
      >
        <div className="zk-auth-bob" style={bob(-9, 7, -1.5)}>
          <LivePlayerCard />
        </div>
      </div>
    </div>
  );
}

// Screen image with a quiet fallback: if the screenshot is ever missing, the screen shows a
// dark gradient rather than a broken-image glyph.
function Screen({ src, className = "" }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className={cx("relative overflow-hidden bg-[linear-gradient(160deg,#141938,#0a0d1f)]", className)}>
      {!failed && (
        <img
          src={src}
          alt=""
          draggable="false"
          decoding="async"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover object-left-top"
        />
      )}
      {/* Glass glare. */}
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(115deg,rgba(255,255,255,0.12)_0%,rgba(255,255,255,0.03)_28%,transparent_45%)]" />
    </div>
  );
}

function Laptop({ src }) {
  return (
    <div className="relative">
      <div className="relative rounded-t-[12px] rounded-b-[3px] bg-gradient-to-b from-[#2b2f3d] to-[#12141c] p-[1.3%] pb-[2%] shadow-[0_40px_70px_-28px_rgba(0,0,0,0.9)] ring-1 ring-white/15">
        <span className="absolute left-1/2 top-[0.55%] h-1 w-1 -translate-x-1/2 rounded-full bg-white/30" />
        <Screen src={src} className="aspect-[16/10] rounded-[4px]" />
      </div>
      <div className="relative -mx-[6%] h-[2.6cqh] min-h-[7px] rounded-b-[16px] rounded-t-[2px] bg-gradient-to-b from-[#d4d7e1] via-[#9a9fb0] to-[#4d5263] shadow-[0_16px_26px_-10px_rgba(0,0,0,0.8)]">
        <span className="absolute left-1/2 top-0 h-[42%] w-[13%] -translate-x-1/2 rounded-b-lg bg-black/25" />
      </div>
    </div>
  );
}

function Phone({ src }) {
  return (
    <div className="relative rounded-[17%/8%] bg-gradient-to-b from-[#2e3140] to-[#0d0e14] p-[3.4%] shadow-[0_30px_60px_-18px_rgba(0,0,0,0.9)] ring-1 ring-white/20">
      <span className="absolute -left-[1.6%] top-[17%] h-[6%] w-[1.6%] rounded-l bg-[#3a3d4c]" />
      <span className="absolute -left-[1.6%] top-[25%] h-[9%] w-[1.6%] rounded-l bg-[#3a3d4c]" />
      <span className="absolute -right-[1.6%] top-[22%] h-[12%] w-[1.6%] rounded-r bg-[#3a3d4c]" />
      <Screen src={src} className="aspect-[390/844] rounded-[14%/6.6%]" />
      <span className="absolute left-1/2 top-[2.6%] h-[3%] w-[30%] -translate-x-1/2 rounded-full bg-black" />
    </div>
  );
}

// [height %, delay s] — the static heights are the reduced-motion resting state.
const WAVE = [[45, 0], [80, -0.2], [60, -0.5], [100, -0.1], [70, -0.7], [90, -0.3], [50, -0.9]];

function LivePlayerCard() {
  return (
    <div className="rounded-xl border border-white/15 bg-[#0c0f24]/85 p-1.5 shadow-[0_20px_44px_-16px_rgba(0,0,0,0.9)] backdrop-blur-md">
      <div className="relative aspect-video overflow-hidden rounded-lg">
        <img src={AUTH_ASSETS.liveStage} alt="" draggable="false" decoding="async" className="h-full w-full object-cover" />
        <div className="absolute inset-0 bg-gradient-to-t from-black/50 via-transparent to-black/20" />
        <span className="absolute left-1.5 top-1.5 inline-flex items-center gap-1 rounded bg-rose-600 px-1.5 py-px text-[9px] font-bold uppercase tracking-[0.12em] text-white shadow-[0_0_14px_-2px_rgba(244,63,94,0.8)]">
          <span className="zk-live-ping h-1 w-1 rounded-full bg-white" />
          Live
        </span>
        <span className="absolute right-1.5 top-1.5 inline-flex items-center gap-1 rounded bg-black/50 px-1 py-px text-[9px] font-semibold text-white backdrop-blur-sm">
          <Eye className="h-2.5 w-2.5" /> 12.4K
        </span>
      </div>
      <div className="flex items-center justify-between gap-2 px-1 pb-0.5 pt-1.5">
        <div className="min-w-0 leading-tight text-white">
          <p className="truncate text-[11px] font-semibold">Global Product Keynote</p>
          <p className="truncate text-[9.5px] text-white/60">Main stage · Live now</p>
        </div>
        <span className="zk-wave flex h-3.5 shrink-0 items-end gap-[2px]">
          {WAVE.map(([h, d], i) => (
            <span
              key={i}
              style={{ height: `${h}%`, "--zk-wave-delay": `${d}s` }}
              className="w-[2px] rounded-full bg-gradient-to-t from-blue-400 to-violet-300"
            />
          ))}
        </span>
      </div>
    </div>
  );
}
