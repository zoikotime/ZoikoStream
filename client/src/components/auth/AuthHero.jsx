import { Fragment } from "react";
import { Link } from "react-router-dom";
import { ShieldCheck, Video } from "lucide-react";
import { HiUserGroup } from "react-icons/hi";
import { IoStatsChart } from "react-icons/io5";
import HeroDevices from "./ProductShowcase";
import { AUTH_ASSETS } from "./authAssets";

// The dark brand half of every auth screen (layouts/AuthLayout), reproducing the approved
// ZoikoStream login design: logo, "Stream without limits.", the support line, four feature
// rows, the laptop + phone, and the tagline, over the design's own backdrop (light arcs,
// tilted concert and world-map screens, stage and crowd — authAssets.js).
//
// Every size and position is the design's, in design px of its 880 × 1024 hero, multiplied
// by --hu (index.css, "Auth hero composition"):
//   lg+   --hu = the scale that fits the whole 1536 × 1024 design in the viewport, so at
//         1536 × 1024 this is the design 1:1. The copy is anchored top-left, the devices
//         bottom-right and the tagline bottom-left, so on a screen of a different shape the
//         spare room opens up between them instead of anything overlapping.
//   < lg  the same pieces in normal flow BELOW the sign-in card (the form comes first on
//         small screens), with readable minimum type sizes.
//
// Only real text is readable here (headline, copy, features). The backdrop, the devices and
// the tagline are decoration and hidden from assistive tech. The headline is not an <h1> —
// each auth page owns its single <h1> inside the card.

const SUPPORT = "Create, host and attend live events with world-class quality, security and reach.";

// Capability statements only (ZST-LE-COM-001 F2/F3/F5) — no numeric service commitments.
const FEATURES = [
  [Video, "Live & on-demand events"],
  [HiUserGroup, "Global audience reach"],
  [ShieldCheck, "Secure and reliable streaming"],
  [IoStatsChart, "Engagement and analytics"],
];

const TAGLINE = ["Create", "Stream", "Engage", "Monetize", "A global stage"];

export default function AuthHero({ className = "" }) {
  return (
    <section aria-label="ZoikoStream" className={`zk-hero ${className}`}>
      <img src={AUTH_ASSETS.heroArt} alt="" aria-hidden="true" draggable="false" decoding="async" className="zk-hero-art" />

      <div className="zk-hero-copy">
        <Link to="/" aria-label="ZoikoStream home" className="zk-hero-logo">
          <img src="/zoiko-logo-white.png" alt="ZoikoStream" draggable="false" />
        </Link>
        <p className="zk-hero-eyebrow">Live events platform</p>

        {/* Two lines, the second set larger (as in the design). The space between the spans
            keeps the read-out "Stream without limits." */}
        <p className="zk-hero-headline">
          <span className="block">Stream without</span>{" "}
          <span className="zk-hero-limits">limits.</span>
        </p>
        <p className="zk-hero-support">{SUPPORT}</p>

        <ul className="zk-hero-features">
          {FEATURES.map(([Icon, title]) => (
            <li key={title}>
              <span className="zk-hero-ring">
                <Icon aria-hidden="true" />
              </span>
              {title}
            </li>
          ))}
        </ul>
      </div>

      <HeroDevices className="zk-hero-devices" />

      <p aria-hidden="true" className="zk-hero-tagline">
        {TAGLINE.map((word, i) => (
          <Fragment key={word}>
            {i > 0 && <span className="zk-hero-slash">/</span>}
            <span className={i === 0 ? "zk-hero-first" : undefined}>{word}</span>
          </Fragment>
        ))}
      </p>
    </section>
  );
}
