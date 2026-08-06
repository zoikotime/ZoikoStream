import { lazy, Suspense } from "react";
import { Link } from "react-router-dom";
import { FiChevronRight } from "react-icons/fi";
import MarketingLayout from "./MarketingLayout";
import { Container, Eyebrow, Heading, Text, SectionFallback } from "../ui";
import { cx } from "../ui/tokens";

// Shell for every public page that isn't the homepage.
//
// The homepage owns its own composition (a dark hero over stacked sections); these pages share
// one structure instead: breadcrumb → title → lead → content → footer. Doing it once means the
// twenty pages below it cannot drift apart in spacing, heading scale, or dark-mode treatment,
// which is exactly how a marketing site ends up looking like three sites.
//
// The footer is the SAME component the homepage uses, lazily loaded here for the same reason.
const Footer = lazy(() => import("../components/home/Footer/Footer"));

// Light hero by default. `tone="dark"` gives the docs and trust pages the darker band the
// homepage uses for its developer section, so those pages read as part of the same system.
const TONES = {
  light: "bg-slate-50 dark:bg-slate-950",
  dark: "bg-slate-950",
};

export default function SitePage({
  eyebrow,
  title,
  lead,
  // [[label, to]] — the last entry is the current page and renders as text.
  crumbs = [],
  actions,
  tone = "light",
  width = "default",
  children,
}) {
  const onDark = tone === "dark";

  return (
    <MarketingLayout>
      {/* pt-16 clears the fixed header; the homepage doesn't need it because its hero
          deliberately sits underneath the transparent bar. */}
      <header className={cx("border-b pt-16", TONES[tone], onDark ? "border-white/10" : "border-slate-200 dark:border-slate-800")}>
        <Container width={width} className="py-12 sm:py-16">
          {crumbs.length > 0 && (
            <nav aria-label="Breadcrumb" className="mb-5">
              <ol className="flex flex-wrap items-center gap-1.5">
                <li>
                  <Link
                    to="/"
                    className={cx(
                      "text-xs font-medium transition-colors",
                      onDark ? "text-white/50 hover:text-white" : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
                    )}
                  >
                    Home
                  </Link>
                </li>
                {crumbs.map(([label, to], i) => {
                  const last = i === crumbs.length - 1;
                  return (
                    <li key={label} className="flex items-center gap-1.5">
                      <FiChevronRight
                        className={cx("text-xs", onDark ? "text-white/30" : "text-slate-400")}
                        aria-hidden="true"
                      />
                      {last || !to ? (
                        <span
                          aria-current={last ? "page" : undefined}
                          className={cx("text-xs font-semibold", onDark ? "text-white" : "text-slate-900 dark:text-white")}
                        >
                          {label}
                        </span>
                      ) : (
                        <Link
                          to={to}
                          className={cx(
                            "text-xs font-medium transition-colors",
                            onDark ? "text-white/50 hover:text-white" : "text-slate-500 hover:text-slate-900 dark:text-slate-400 dark:hover:text-white"
                          )}
                        >
                          {label}
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ol>
            </nav>
          )}

          <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
            <div className="max-w-3xl">
              {eyebrow && <Eyebrow>{eyebrow}</Eyebrow>}
              <Heading level={1} size="h1" className={cx(eyebrow && "mt-4", onDark && "text-white")}>
                {title}
              </Heading>
              {lead && (
                <Text tone="lead" className={cx("mt-4", onDark && "text-white/70")}>
                  {lead}
                </Text>
              )}
            </div>
            {actions && <div className="flex shrink-0 flex-wrap items-center gap-3">{actions}</div>}
          </div>
        </Container>
      </header>

      {children}

      <Suspense fallback={<SectionFallback />}>
        <Footer />
      </Suspense>
    </MarketingLayout>
  );
}
