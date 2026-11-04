import { Fragment } from "react";
import "@fontsource-variable/inter";
import { Link, Outlet } from "react-router-dom";
import AuthHero, { AuthBackdrop, AuthHeroCompact } from "../components/auth/AuthHero";
import { IS_NATIVE } from "../platform";

// Shared shell for every auth page (Login / Register / Reset Password / Accept Invitation /
// Verify Email). ONE continuous page — a single backdrop behind everything — with the brand
// column on the left and a floating sign-in card on the right (ZoikoSema reference layout).
//
//   lg+    exactly one viewport tall: brand column | card. The card is centred vertically;
//          when its content is taller than the screen (the Register form on a short display)
//          the card scrolls inside itself — the page never does.
//   < lg   an ordinary scrolling page: brand and headline, then the card at full width.
//
// The card wraps <Outlet />, and CreateOrganization renders a position:fixed modal from
// there. So the card must never carry a transform, filter or backdrop-filter (each would trap
// the modal inside it): its surface is a near-opaque white and hover elevation is shadow-only.

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
    <div className="zk-auth-font relative isolate min-h-dvh lg:h-dvh lg:overflow-hidden">
      <AuthBackdrop />

      <div className="mx-auto grid min-h-dvh w-full max-w-[1600px] grid-cols-1 gap-8 px-4 py-6 sm:px-8 sm:py-10 lg:h-dvh lg:min-h-0 lg:grid-cols-[minmax(0,1fr)_minmax(440px,500px)] lg:gap-[clamp(32px,4vw,88px)] lg:px-[clamp(32px,4.5vw,88px)] lg:py-[clamp(10px,1.8vh,40px)]">
        <AuthHero className="hidden lg:flex" />
        <AuthHeroCompact className="mx-auto w-full max-w-[520px] md:max-w-[680px] lg:hidden" />

        <div className="flex min-h-0 items-center justify-center lg:items-center">
          <main className="zk-auth-panel zk-scroll-thin relative flex max-h-full w-full max-w-[520px] flex-col overflow-y-auto rounded-[28px] border border-white/70 bg-white/[0.97] shadow-[0_40px_90px_-30px_rgba(0,0,0,0.75),0_12px_30px_-18px_rgba(46,16,101,0.45)] transition-shadow duration-300 hover:shadow-[0_46px_100px_-28px_rgba(0,0,0,0.8),0_16px_36px_-16px_rgba(91,33,182,0.5)] motion-reduce:transition-none dark:border-white/10 dark:bg-[#0d1020]/[0.97]">
            <Outlet />

            <footer className="mt-auto border-t border-slate-200/80 px-[clamp(20px,3vw,40px)] py-[var(--auth-s2)] dark:border-white/10">
              <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1.5 text-[12.5px] text-slate-500 dark:text-slate-400">
                <nav aria-label="Legal and help" className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  {FOOTER_LINKS.map(({ label, to }, i) => (
                    <Fragment key={label}>
                      {i > 0 && (
                        <span aria-hidden="true" className="text-slate-300 dark:text-slate-600">
                          |
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
                <p>&copy; 2026 Zoiko Tech Inc. All rights reserved.</p>
              </div>
            </footer>
          </main>
        </div>
      </div>
    </div>
  );
}
