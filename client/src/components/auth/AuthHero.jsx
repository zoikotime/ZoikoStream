import { useCallback, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { BarChart3, Globe, ShieldCheck, Video } from "lucide-react";
import { cx } from "../../ui/tokens";
import ProductShowcase from "./ProductShowcase";
import { AUTH_ASSETS } from "./authAssets";

// The brand side of every auth screen, laid over ONE page-wide background (AuthBackdrop) so
// the hero and the sign-in card share a single environment — no split panels (after the
// ZoikoSema reference composition: brand, headline, feature tiles, product, tagline).
//
//   <AuthBackdrop />     full-page concert photograph + restrained lighting, behind everything
//   <AuthHero />         lg+ left column: brand, headline, copy, feature tiles, showcase
//   <AuthHeroCompact />  < lg: brand + headline (+ copy and tiles from md) above the card
//
// The headline and copy never move; only the showcase floats and responds to the pointer.

const prefersReducedMotion = () =>
  typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// Pointer parallax without re-rendering: writes --px/--py (-1..1) onto the column; the
// showcase layers read them. One rAF per frame at most; mouse only; off under reduced motion.
function usePointerParallax() {
  const frame = useRef(0);
  useEffect(() => () => cancelAnimationFrame(frame.current), []);

  const onPointerMove = useCallback((e) => {
    if (e.pointerType !== "mouse" || prefersReducedMotion()) return;
    const node = e.currentTarget;
    const { clientX, clientY } = e;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => {
      const r = node.getBoundingClientRect();
      if (!r.width || !r.height) return;
      node.style.setProperty("--px", (((clientX - r.left) / r.width) * 2 - 1).toFixed(3));
      node.style.setProperty("--py", (((clientY - r.top) / r.height) * 2 - 1).toFixed(3));
    });
  }, []);

  const onPointerLeave = useCallback((e) => {
    cancelAnimationFrame(frame.current);
    e.currentTarget.style.setProperty("--px", "0");
    e.currentTarget.style.setProperty("--py", "0");
  }, []);

  return { onPointerMove, onPointerLeave };
}

// Page-wide environment: the concert photograph, deliberately quiet (low opacity, desaturated
// by a navy wash) so it reads as atmosphere, plus three slow light pools in the brand
// blue / violet / magenta. Fixed to the viewport so it never seams or tiles when the page
// scrolls on small screens.
export function AuthBackdrop() {
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-[#070a1d]">
      <picture>
        <source media="(max-width: 1023px)" srcSet={AUTH_ASSETS.stageSmall} />
        <img
          src={AUTH_ASSETS.stage}
          alt=""
          draggable="false"
          decoding="async"
          className="absolute inset-0 h-full w-full object-cover object-[50%_30%] opacity-45"
        />
      </picture>
      {/* Navy wash: darkest behind the copy, lifting slightly toward the card. */}
      <div className="absolute inset-0 bg-[linear-gradient(105deg,rgba(6,9,27,0.95)_0%,rgba(7,10,30,0.82)_45%,rgba(9,11,36,0.72)_100%)]" />
      <div className="absolute inset-x-0 bottom-0 h-1/2 bg-gradient-to-t from-[#070a1d] to-transparent" />
      {/* Slow light pools. */}
      <div className="zk-drift absolute -right-[10%] -top-[15%] h-[60vh] w-[60vh] rounded-full bg-violet-600/25 blur-[120px]" />
      <div className="zk-glow-pulse absolute -bottom-[20%] left-[5%] h-[55vh] w-[55vh] rounded-full bg-blue-600/20 blur-[120px]" />
      <div className="zk-float absolute left-[42%] top-[30%] h-[40vh] w-[40vh] rounded-full bg-fuchsia-600/15 blur-[120px]" />
      {/* Fine grain so the gradients don't band. */}
      <div
        className="absolute inset-0 opacity-[0.05]"
        style={{ backgroundImage: "radial-gradient(rgba(255,255,255,0.8) 1px, transparent 1px)", backgroundSize: "3px 3px" }}
      />
    </div>
  );
}

function BrandMark() {
  return (
    <div className="flex items-center gap-4">
      <Link
        to="/"
        aria-label="ZoikoStream home"
        className="inline-block rounded-xl bg-white px-3 py-2 shadow-[0_10px_26px_-12px_rgba(0,0,0,0.7)]"
      >
        <img src="/zoiko-logo.png" alt="ZoikoStream" className="block h-[clamp(28px,4vh,38px)] w-auto" />
      </Link>
      <span aria-hidden="true" className="h-8 w-px bg-white/20" />
      <p className="text-[11.5px] font-semibold uppercase tracking-[0.28em] text-white/75">Live events platform</p>
    </div>
  );
}

// Marketing copy, not a document heading: each auth page owns its single <h1>.
function Headline({ className = "" }) {
  return (
    <p className={cx("font-extrabold leading-[1.04] tracking-[-0.035em] text-white", className)}>
      Stream without
      <br />
      <span className="bg-gradient-to-r from-sky-400 to-blue-500 bg-clip-text pr-1 text-transparent">limits.</span>
    </p>
  );
}

const SUPPORT = "Create, host and attend live events with world-class quality, security and reach.";

// Capability statements only (ZST-LE-COM-001 F2/F3/F5) — no numeric service commitments.
const FEATURES = [
  [Video, "Live & on-demand events", "Broadcast live, replay on demand"],
  [Globe, "Global audience reach", "Audiences wherever they are"],
  [ShieldCheck, "Secure streaming", "Protected access for every event"],
  [BarChart3, "Engagement and analytics", "Insight from every session"],
];

// Four evenly divided tiles (reference: icon disc, title, one-line description, hairline
// separators). Real text — this is the hero's list of what the platform does.
function FeatureTiles({ className = "" }) {
  return (
    <ul className={cx("grid grid-cols-2 gap-y-4 xl:grid-cols-4", className)}>
      {FEATURES.map(([Icon, title, blurb]) => (
        <li
          key={title}
          className="group flex flex-col items-center px-3 text-center transition-transform duration-300 hover:-translate-y-1 motion-reduce:transition-none motion-reduce:hover:translate-y-0 xl:border-l xl:border-white/10 xl:first:border-l-0"
        >
          <span className="grid h-11 w-11 place-items-center rounded-full bg-white/[0.07] text-sky-300 ring-1 ring-white/15 transition duration-300 group-hover:bg-white/[0.12] group-hover:text-sky-200 group-hover:shadow-[0_0_24px_-2px_rgba(56,189,248,0.55)]">
            <Icon aria-hidden="true" className="h-5 w-5" />
          </span>
          <span className="mt-2.5 text-[14px] font-semibold leading-tight text-white">{title}</span>
          <span className="mt-1 text-[12.5px] leading-snug text-white/60">{blurb}</span>
        </li>
      ))}
    </ul>
  );
}

// lg+ left column. Fills the viewport height; vertical rhythm from height-based clamps and
// the showcase takes what is left (flex-1 + its own size container), so it fits 720–1080px.
export default function AuthHero({ className = "" }) {
  const parallax = usePointerParallax();
  return (
    <section aria-label="ZoikoStream" {...parallax} className={cx("min-h-0 flex-col text-white", className)}>
      <BrandMark />

      <div className="mt-[clamp(20px,5vh,56px)] shrink-0">
        <Headline className="text-[clamp(2.6rem,min(4.4vw,8vh),4.75rem)]" />
        <p className="mt-[clamp(10px,2vh,20px)] max-w-[34rem] text-[clamp(0.95rem,min(1.15vw,2.1vh),1.15rem)] leading-relaxed text-white/75">
          {SUPPORT}
        </p>
      </div>

      <FeatureTiles className="mt-[clamp(16px,3.6vh,40px)] max-w-[44rem] shrink-0" />

      <ProductShowcase className="mt-[clamp(10px,2.4vh,28px)] min-h-0 w-full max-w-[44rem] flex-1" />

      <p
        aria-hidden="true"
        className="mt-[clamp(8px,1.8vh,20px)] shrink-0 text-[11px] font-semibold uppercase tracking-[0.3em] text-white/55"
      >
        Create · Stream · Engage · Monetize · A global stage
      </p>
    </section>
  );
}

// < lg: brand + headline above the card; copy and feature tiles from md up. No showcase —
// on small screens the form comes first.
export function AuthHeroCompact({ className = "" }) {
  return (
    <section aria-label="ZoikoStream" className={cx("text-white", className)}>
      <BrandMark />
      <Headline className="mt-6 text-[2.4rem] sm:text-5xl" />
      <p className="mt-3 hidden max-w-[32rem] text-base leading-relaxed text-white/75 md:block">{SUPPORT}</p>
      <FeatureTiles className="mt-8 hidden md:grid" />
    </section>
  );
}
