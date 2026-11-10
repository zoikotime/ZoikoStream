import { Link } from "react-router-dom";
import { cx } from "../../ui/tokens";
import AuthTabs from "./AuthTabs";

// The contents of the sign-in card on AuthLayout's lavender panel, as in the approved login
// design: optional tab strip on the card's tinted top edge, then the white inner card with
// the centred logo, the page's single <h1>, a subtitle and the page's own content.
// AuthLayout owns the outer card and its footer. Sizes come from the --auth-* tokens
// (index.css), which are the design's px × --u.
//
//   tabs      show the Login / Register / Reset Password switcher
//   icon      optional node above the title (e.g. a success badge)
//   title     the page's <h1>; omit it when the page renders its own
//   subtitle  one line under the title
//   dense     tighter vertical rhythm (index.css .zk-auth-dense) for a long form
//
// Keep position:fixed UI (modals) as a SIBLING of this component, not a child: zk-fade-in
// leaves a transform on this wrapper, which would become the modal's containing block.
export default function AuthCard({ tabs = false, icon, title, subtitle, dense = false, className = "", children }) {
  return (
    <div className={cx("zk-fade-in flex flex-col", dense && "zk-auth-dense", className)}>
      {tabs && <AuthTabs />}

      <div
        className={cx(
          "mx-[var(--auth-shell)] rounded-[calc(20*var(--u))] border border-[#eceef6] bg-white px-[var(--auth-pad)] pb-[calc(15*var(--u))] shadow-[0_1px_2px_rgba(30,27,75,0.04),0_14px_34px_-26px_rgba(46,16,101,0.3)] dark:border-white/10 dark:bg-[#0d1020]",
          tabs ? "pt-[calc(22*var(--u))]" : "mt-[var(--auth-shell)] pt-[var(--auth-s3)]"
        )}
      >
        <header className={cx("flex flex-col items-center text-center", !title && "mb-[var(--auth-s3)]")}>
          {/* The wordmark's navy lettering needs a light chip only on the dark card. */}
          <Link to="/" aria-label="ZoikoStream home" className="zk-auth-logo rounded-xl dark:bg-white/95 dark:px-3 dark:py-1.5">
            <img src="/zoiko-logo.png" alt="ZoikoStream" className="block h-[var(--auth-logo)] w-auto" />
          </Link>
          {icon && <div className="mt-[var(--auth-s2)]">{icon}</div>}
          {title && (
            <h1 className="mt-[calc(15*var(--u))] text-[length:var(--auth-title)] font-extrabold leading-[1.15] tracking-[-0.025em] text-[#0b0b26] dark:text-white">
              {title}
            </h1>
          )}
          {subtitle && (
            <p className="text-[length:max(15px,calc(18.5*var(--u)))] leading-[1.4] text-[#5a6080] dark:text-slate-400">{subtitle}</p>
          )}
        </header>

        {children}
      </div>
    </div>
  );
}
