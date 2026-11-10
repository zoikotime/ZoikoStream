import { useState } from "react";
import { cx } from "../../ui/tokens";
import { AUTH_ASSETS } from "./authAssets";

// The laptop and phone of the auth hero, placed as in the approved login design: a large
// laptop toward the bottom right, turned slightly toward the viewer, and a phone in front of
// its lower-left corner.
//
// The group is drawn inside a 720×450 box (design region x 160–880, y 540–990 of the
// 880×1024 hero) and every position below is a percentage of that box, so the composition
// scales as one piece. The box is a CSS size container: lengths that cannot be percentages
// (the perspective distance, radii, bezels) are cqw.
//
// The screens are SCREENSHOTS of the actual app — the live viewer page (see authAssets.js) —
// not drawn UI.
// Everything here is decoration: aria-hidden, never focusable.

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
          className="absolute inset-0 h-full w-full object-cover object-left-top"
        />
      )}
      {/* Glass glare. */}
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(115deg,rgba(255,255,255,0.10)_0%,rgba(255,255,255,0.02)_30%,transparent_46%)]" />
    </div>
  );
}

// Laptop, as in the approved design: a large screen turned slightly toward the viewer on a
// slim silver base — no drawn keyboard (the design shows the laptop at deck height, where
// the keys are not visible).
//
// Lid in design px: left edge x 360, hinge y 925, 504 × 325 before the turn. The turn is
// about the lid's left edge with the eye level at its hinge, so the hinge stays level while
// the nearer right edge stands taller (≈ 1.12×) and ends at x ≈ 860 with its top at y ≈ 565.
// (x 360 keeps the design's gap between the lid's top-left corner and the feature labels.)
// rotateY(-28°) at a perspective of 2161 design px (300cqw of the 720 box).
//
// The base is a child of the turned element, so it shares the screen's exact angle and
// perspective — the two can never drift out of alignment.
function Laptop() {
  return (
    <div
      className="absolute bottom-[14.44%] left-[27.78%] h-[72.22%] w-[70%]"
      style={{ perspective: "300cqw", perspectiveOrigin: "0% 100%" }}
    >
      <div className="relative h-full w-full" style={{ transform: "rotateY(-28deg)", transformOrigin: "0% 100%" }}>
        {/* Lid: thin silver rim, black glass bezel, screen. */}
        <div className="absolute inset-0 rounded-[1.9cqw] bg-gradient-to-b from-[#c3c6d3] via-[#767a8c] to-[#3a3d4b] p-[0.22cqw] shadow-[0_3cqw_6cqw_-2cqw_rgba(0,0,0,0.85)]">
          <div className="relative h-full w-full rounded-[1.7cqw] bg-[#07080d] p-[1.45cqw] pb-[1.6cqw]">
            <span className="absolute left-1/2 top-[0.5cqw] h-[0.45cqw] w-[0.45cqw] -translate-x-1/2 rounded-full bg-[#2a2d3a]" />
            <Screen src={AUTH_ASSETS.watchShot} className="h-full w-full rounded-[0.5cqw]" />
          </div>
        </div>
        {/* Hinge: a dark seam where the lid meets the base. */}
        <div className="absolute inset-x-[1.5%] top-[calc(100%-0.15cqw)] h-[0.55cqw] rounded-b-sm bg-gradient-to-b from-[#1b1d25] to-[#3a3d4b]" />
        {/* Base: slim brushed-silver slab, a little wider than the lid, with a highlight on
            its top edge, the thumb notch, and a soft shadow on the stage floor. */}
        <div className="absolute -left-[4%] -right-[1.6%] top-[calc(100%+0.35cqw)] h-[3.1cqw] rounded-b-[1.4cqw] rounded-t-[0.35cqw] bg-gradient-to-b from-[#f1f2f7] via-[#b9bcca] to-[#6b6f80] shadow-[0_2.2cqw_3.2cqw_-1.2cqw_rgba(0,0,0,0.9)]">
          <span className="absolute inset-x-[1%] top-0 h-px rounded-full bg-white/80" />
          <span className="absolute left-1/2 top-0 h-[34%] w-[13%] -translate-x-1/2 rounded-b-[0.7cqw] bg-gradient-to-b from-[#8f93a3] to-[#b4b7c5]" />
        </div>
      </div>
    </div>
  );
}

// Phone in design px: left edge x 198, y 647–955 (170 × 308 before the turn — the design's
// phone size), in front of the laptop's lower-left corner (it overlaps the left end of the
// base). As in the design, the phone and laptop are turned slightly toward each other: the
// laptop faces left (see Laptop), the phone faces right — rotateY(18°) about its LEFT edge
// at a perspective of 900 design px (125cqw). The left edge keeps its exact height and place;
// the right edge recedes to x ≈ 351 and stands ~5% shorter.
function Phone() {
  return (
    <div
      className="absolute left-[5.28%] top-[23.78%] z-10 h-[68.44%] w-[23.61%] rounded-[3.9cqw] bg-gradient-to-b from-[#8a8ca3] via-[#4b4d5e] to-[#2b2c37] p-[0.32cqw] shadow-[0_3cqw_5cqw_-1.6cqw_rgba(0,0,0,0.9)]"
      style={{ transform: "perspective(125cqw) rotateY(18deg)", transformOrigin: "0% 50%" }}
    >
      <div className="relative h-full w-full rounded-[3.6cqw] bg-[#0b0b10] p-[0.75cqw]">
        <Screen src={AUTH_ASSETS.watchMobileShot} className="h-full w-full rounded-[3cqw]" />
        {/* Notch. */}
        <span className="absolute left-1/2 top-[0.75cqw] h-[2.2cqw] w-[38%] -translate-x-1/2 rounded-b-[1.2cqw] bg-[#0b0b10]" />
      </div>
      {/* Side buttons. */}
      <span className="absolute -left-[1.4%] top-[16%] h-[6%] w-[1.6%] rounded-l bg-[#5a5c6c]" />
      <span className="absolute -left-[1.4%] top-[25%] h-[10%] w-[1.6%] rounded-l bg-[#5a5c6c]" />
      <span className="absolute -right-[1.4%] top-[22%] h-[13%] w-[1.6%] rounded-r bg-[#5a5c6c]" />
    </div>
  );
}

export default function HeroDevices({ className = "" }) {
  return (
    <div aria-hidden="true" className={cx("[container-type:size]", className)}>
      {/* Stage light pooling under the devices. */}
      <div className="pointer-events-none absolute -bottom-[6%] left-[8%] h-[40%] w-[92%] rounded-[50%] bg-violet-600/30 blur-[4cqw]" />
      <Laptop />
      <Phone />
    </div>
  );
}
