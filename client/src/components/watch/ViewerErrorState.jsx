// client/src/components/watch/ViewerErrorState.jsx
// The unrecoverable-error state (ZST-SPEC-VAP-001 §6.3: "Retry + Help; plain language").
// Shown only when the page has NOTHING to show: the first load of the event failed for a
// reason other than "not found" or "refused" (network down, server error). A failed
// background poll never lands here; the page keeps showing what it already had.
import { Link } from "react-router-dom";
import { FiAlertTriangle, FiRefreshCw, FiLifeBuoy, FiActivity } from "react-icons/fi";
import { useViewerLanguage } from "../../pages/watch/viewerLanguage";
import { STATE_ACTION } from "./ViewerStateCard";
import AudioAssistButton from "./AudioAssistButton";
import { isCustomDomain, platformHref } from "../../utils/hostMode";

// Help and Status live on the platform. On an organization's custom domain those routes do
// not exist, so the links become absolute platform URLs there (utils/hostMode.js).
function PlatformLink({ to, className, children }) {
  if (isCustomDomain())
    return <a href={platformHref(to)} target="_blank" rel="noreferrer" className={className}>{children}</a>;
  return <Link to={to} target="_blank" rel="noreferrer" className={className}>{children}</Link>;
}

export default function ViewerErrorState({ onRetry }) {
  const { t, lang } = useViewerLanguage();
  return (
    <div className="grid min-h-screen place-items-center bg-slate-50 px-4 dark:bg-slate-950">
      <section role="alert" aria-labelledby="viewer-error-title" data-testid="state-error" className="max-w-md text-center">
        <span className="mx-auto grid h-14 w-14 place-items-center rounded-full bg-amber-50 text-amber-600 dark:bg-amber-500/10 dark:text-amber-300" aria-hidden="true">
          <FiAlertTriangle className="text-2xl" />
        </span>
        <h1 id="viewer-error-title" className="mt-4 text-lg font-semibold text-slate-900 dark:text-white">{t("errorTitle")}</h1>
        <p className="mt-2 text-sm text-slate-600 dark:text-slate-300">{t("errorBody")}</p>
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          <button type="button" onClick={onRetry} className={`${STATE_ACTION} bg-emerald-600 text-white hover:bg-emerald-500`}>
            <FiRefreshCw aria-hidden="true" /> {t("retry")}
          </button>
          <PlatformLink to="/contact" className={`${STATE_ACTION} text-slate-700 ring-1 ring-slate-200 hover:bg-slate-100 dark:text-slate-200 dark:ring-white/15 dark:hover:bg-white/10`}>
            <FiLifeBuoy aria-hidden="true" /> {t("help")}
          </PlatformLink>
          <PlatformLink to="/status" className={`${STATE_ACTION} text-slate-700 hover:bg-slate-100 dark:text-slate-200 dark:hover:bg-white/10`}>
            <FiActivity aria-hidden="true" /> {t("status")}
          </PlatformLink>
          <AudioAssistButton text={`${t("errorTitle")}. ${t("errorBody")}`} lang={lang}
                             label={t("readAloud")} stopLabel={t("stopReading")} />
        </div>
      </section>
    </div>
  );
}
