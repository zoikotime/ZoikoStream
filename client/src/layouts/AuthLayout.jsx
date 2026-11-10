import { Fragment } from "react";
import "@fontsource-variable/inter";
import { Link, Outlet } from "react-router-dom";
import AuthHero from "../components/auth/AuthHero";
import { IS_NATIVE } from "../platform";

// Shared shell for every auth page (Login / Register / Reset Password / Accept Invitation /
// Verify Email / Google callback), reproducing the approved ZoikoStream login design
// (1536 × 1024): the dark brand hero on the left (880 of 1536) and a light lavender panel on
// the right (656) holding the sign-in card.
//
// Sizes are the design's px × --u (index.css, "Auth design scale"). On lg+ --u is the scale
// that fits the whole design in the viewport — 1 at 1536 × 1024 — so both halves keep the
// design's proportions and relationships at any desktop size. The sign-in panel redefines
// --u for itself so the card fills the panel (index.css .zk-auth-side), and on wide screens
// the card may also widen to 84% of the panel (35.875vw), up to 640 design px.
//
//   lg+   exactly one viewport tall. The hero never scrolls; the right panel scrolls on its
//         own when a form (Register on a short display) is taller than the screen, so the
//         submit button is always reachable.
//   < lg  an ordinary scrolling page: the sign-in card FIRST (the form is why you are here),
//         then the brand hero beneath it.
//
// The sign-in card is first in the DOM on every size, so keyboard and screen-reader users
// reach the form before the marketing; on lg+ the hero is moved to the left column by CSS.
//
// The card wraps <Outlet />, and CreateOrganization renders a position:fixed modal from
// there. So the card and its ancestors must never carry a transform, filter or
// backdrop-filter (each would trap the modal inside them).

// Footer links. `to: null` = no page exists yet, so the label is plain text rather than a
// link that 404s; give it a route here and it becomes a link with no other change.
const FOOTER_LINKS = [
  { label: "Privacy", to: "/privacy" },
  { label: "Terms", to: null },
  { label: "Accessibility", to: null },
  // The Guide is the help centre on the web; the native app does not ship it.
  { label: "Help", to: IS_NATIVE ? "/contact" : "/guide" },
];

export default function AuthLayout() {
  return (
    <div className="zk-auth-root zk-auth-font grid min-h-dvh grid-cols-1 bg-[#f5f9fd] lg:h-dvh lg:grid-cols-[880fr_656fr] lg:overflow-hidden dark:bg-[#080b1a]">
      <div className="zk-auth-side relative isolate flex min-w-0 flex-col lg:col-start-2 lg:row-start-1 lg:min-h-0">
        <div className="zk-scroll-thin flex min-h-0 flex-1 flex-col px-3 py-4 sm:px-8 sm:py-10 lg:overflow-y-auto lg:px-[calc(32*var(--u))] lg:py-[calc(24*var(--u))]">
          <main className="zk-auth-panel m-auto flex w-full max-w-[calc(592*var(--u))] flex-col lg:w-[max(calc(592*var(--u)),min(35.875vw,calc(640*var(--u))))] lg:max-w-full rounded-[calc(24*var(--u))] border border-white bg-[#f8f9fe] shadow-[0_1px_2px_rgba(30,27,75,0.05),0_28px_64px_-34px_rgba(76,29,149,0.35)] dark:border-white/10 dark:bg-white/[0.03] dark:shadow-[0_30px_70px_-30px_rgba(0,0,0,0.8)]">
            <Outlet />

            <footer className="flex min-h-[calc(69*var(--u))] flex-wrap items-center justify-between gap-x-4 gap-y-1.5 py-3 pl-[calc(32*var(--u))] pr-[calc(29*var(--u))] text-[length:max(11px,calc(14*var(--u)))] text-[#5f6684] dark:text-slate-400">
              <nav aria-label="Legal and help" className="flex flex-wrap items-center gap-x-[calc(13*var(--u))] gap-y-1">
                {FOOTER_LINKS.map(({ label, to }, i) => (
                  <Fragment key={label}>
                    {i > 0 && (
                      <span aria-hidden="true" className="text-[#9aa0b8] dark:text-slate-600">
                        &bull;
                      </span>
                    )}
                    {to ? (
                      <Link
                        to={to}
                        className="rounded transition-colors duration-200 hover:text-violet-700 dark:hover:text-violet-300"
                      >
                        {label}
                      </Link>
                    ) : (
                      <span>{label}</span>
                    )}
                  </Fragment>
                ))}
              </nav>
              <p>&copy; 2026 Zoiko Tech Inc.</p>
            </footer>
          </main>
        </div>
      </div>

      <AuthHero className="lg:col-start-1 lg:row-start-1" />
    </div>
  );
}
