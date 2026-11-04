import { Link } from "react-router-dom";
import { cx } from "../../ui/tokens";
import AuthTabs from "./AuthTabs";

// The contents of the sign-in card AuthLayout floats over the page: optional tab strip
// across the card's top edge, then a padded body with the centred logo, the page's single
// <h1>, a subtitle and the page's own content. AuthLayout owns the card surface and footer.
//
//   tabs      show the Login / Register / Reset Password switcher
//   icon      optional node above the title (e.g. a success badge)
//   title     the page's <h1>; omit it when the page renders its own
//   subtitle  one line under the title
//   dense     tighter vertical rhythm on desktop (index.css .zk-auth-dense) for a long form,
//             so its fields and submit button fit the viewport without the card scrolling
//
// Keep position:fixed UI (modals) as a SIBLING of this component, not a child: zk-fade-in
// leaves a transform on this wrapper, which would become the modal's containing block.
export default function AuthCard({ tabs = false, icon, title, subtitle, dense = false, className = "", children }) {
  return (
    <div className={cx("zk-fade-in flex flex-col", dense && "zk-auth-dense", className)}>
      {tabs && <AuthTabs />}

      <div className="px-[clamp(20px,3vw,40px)] pb-[var(--auth-s3)] pt-[var(--auth-s3)]">
        <header className={cx("flex flex-col items-center text-center", !title && "mb-[var(--auth-s3)]")}>
          {/* The wordmark's navy lettering needs a light chip only on the dark card. */}
          <Link to="/" aria-label="ZoikoStream home" className="zk-auth-logo rounded-xl dark:bg-white/95 dark:px-3 dark:py-1.5">
            <img src="/zoiko-logo.png" alt="ZoikoStream" className="block h-[var(--auth-logo)] w-auto" />
          </Link>
          {icon && <div className="mt-[var(--auth-s2)]">{icon}</div>}
          {title && (
            <h1 className="mt-[var(--auth-s1)] text-[length:var(--auth-title)] font-bold leading-tight tracking-[-0.025em] text-slate-950 dark:text-white">
              {title}
            </h1>
          )}
          {subtitle && (
            <p className="mt-1.5 text-[15px] leading-snug text-slate-500 dark:text-slate-400">{subtitle}</p>
          )}
        </header>

        {children}
      </div>
    </div>
  );
}
