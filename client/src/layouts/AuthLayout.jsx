import { useState } from "react";
import { Link, Outlet } from "react-router-dom";
import useInterval from "../hooks/useInterval";
import { Logo } from "../ui";

// Rotates in the brand panel to keep it feeling alive. (Kept from the original AuthPage.)
const TAGLINES = [
  "Stream your events to the world.",
  "One dashboard for every live moment.",
  "Go live in seconds, scale to millions.",
];

// Shared shell for every auth page — left brand panel + right form panel. The form is
// rendered via <Outlet /> so Login / Create Organization / Forgot Password / Accept
// Invitation all share the exact same branding without duplicating it.
export default function AuthLayout() {
  const [tagIdx, setTagIdx] = useState(0);
  useInterval(() => setTagIdx((i) => (i + 1) % TAGLINES.length), 4000);

  // Pointer-follow spotlight — writes CSS vars straight to the node, no re-render.
  const onMove = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    e.currentTarget.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
    e.currentTarget.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Brand panel */}
      <div
        onMouseMove={onMove}
        className="relative hidden flex-col justify-between overflow-hidden bg-slate-950 p-12 text-white lg:flex"
      >
        {/* Premium dark gradient — deep navy → indigo → soft purple, from the logo */}
        <div
          aria-hidden
          className="zk-gradient-pan pointer-events-none absolute inset-0 bg-[linear-gradient(140deg,#111a30_0%,#1f2d49_42%,#26305a_72%,#2f2f5e_100%)]"
        />

        {/* Soft blurred glows — indigo · soft blue · a small pink accent */}
        <div aria-hidden className="pointer-events-none absolute inset-0">
          <div className="zk-drift absolute -left-20 top-4 h-64 w-64 rounded-full bg-indigo-500/20 blur-3xl" />
          <div className="zk-float absolute -right-16 bottom-16 h-80 w-80 rounded-full bg-sky-500/12 blur-3xl" />
          <div
            className="zk-drift absolute bottom-1/4 left-1/3 h-56 w-56 rounded-full bg-pink-500/12 blur-3xl"
            style={{ animationDelay: "-6s" }}
          />
        </div>

        {/* Slow conic flourish (top-right) — navy → indigo → lavender, no neon */}
        <div
          aria-hidden
          className="zk-spin-slow pointer-events-none absolute -right-28 -top-28 h-80 w-80 rounded-full opacity-25 blur-2xl"
          style={{ background: "conic-gradient(from 0deg, #223056, #3a3e77, #574f92, #3a3e77, #223056)" }}
        />

        {/* Fine dot grid for texture */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-[0.08]"
          style={{
            backgroundImage: "radial-gradient(rgba(191,199,255,0.5) 1px, transparent 1px)",
            backgroundSize: "22px 22px",
          }}
        />

        {/* Interactive: soft glow follows the cursor */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 opacity-50"
          style={{
            background:
              "radial-gradient(440px circle at var(--mx,50%) var(--my,25%), rgba(180,190,255,0.10), transparent 65%)",
          }}
        />

        {/* Legibility scrim */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-gradient-to-t from-slate-950/55 via-transparent to-slate-950/20"
        />

        <Link to="/" className="zk-fade-in relative inline-block" aria-label="ZoikoStream home">
          <Logo height="h-10" />
        </Link>

        <div className="relative">
          {/* key remounts on change so the fade-in re-runs — cheap crossfade */}
          <h2 key={tagIdx} className="zk-fade-in min-h-[2.4em] max-w-lg text-4xl font-bold leading-tight drop-shadow-sm">
            {TAGLINES[tagIdx]}
          </h2>
          <p className="zk-fade-in mt-4 max-w-md text-white/85">
            Host, manage and analyze live events from one dashboard.
          </p>
          <div className="zk-fade-in mt-6 inline-flex items-center gap-2 rounded-full border border-white/20 bg-white/10 px-3.5 py-1.5 text-sm font-medium backdrop-blur-md">
            <span className="relative flex h-2.5 w-2.5">
              <span className="zk-pulse-ring absolute inline-flex h-full w-full rounded-full bg-pink-400" />
              <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-pink-400" />
            </span>
            1,204 events streaming now
          </div>
        </div>

        <p className="relative text-sm text-white/70">© 2026 Zoiko Group</p>
      </div>

      {/* Form panel */}
      <div className="flex items-center justify-center bg-slate-50 p-6 dark:bg-slate-950">
        <div className="w-full max-w-md">
          <img src="/zoiko-logo.png" alt="ZoikoStream" className="mx-auto mb-8 h-11 w-auto" />
          <Outlet />
        </div>
      </div>
    </div>
  );
}
