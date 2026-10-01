// client/src/components/watch/InAppBrowserNotice.jsx
// "Open in browser" guidance + Copy Link for embedded in-app browsers (ZST-SPEC-VAP-001 §6.3).
// A dismissible banner above the page, never a blocker: whatever the viewer is already doing
// (watching, registering, waiting) carries on underneath, and the current session is never
// torn down by it.
//
// The copied link is the page's own address with NO query and NO fragment, so a credential
// never lands on the clipboard. An invitation holder is told to reopen their original link
// instead.
import { useState } from "react";
import { FiExternalLink, FiCopy, FiCheck, FiX, FiCompass } from "react-icons/fi";
import { androidBrowserIntent } from "../../utils/inAppBrowser";
import { useViewerLanguage } from "../../pages/watch/viewerLanguage";
import { STATE_ACTION } from "./ViewerStateCard";

export default function InAppBrowserNotice({ platform, privateInvite = false }) {
  const { t } = useViewerLanguage();
  const [dismissed, setDismissed] = useState(false);
  const [copied, setCopied] = useState(false);
  if (dismissed) return null;

  const cleanUrl = `${window.location.origin}${window.location.pathname}`;
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(cleanUrl);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <aside
      role="region"
      aria-label={t("inAppTitle")}
      data-testid="in-app-browser-notice"
      className="border-b border-amber-200 bg-amber-50 px-4 py-3 text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-100"
    >
      <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3">
        <FiCompass aria-hidden="true" className="shrink-0 text-xl" />
        <div className="min-w-0 flex-1 text-sm">
          <p className="font-semibold">{t("inAppTitle")}</p>
          <p>{platform === "android" ? t("inAppAndroid") : t("inAppIos")}</p>
          {privateInvite && <p className="mt-0.5 text-amber-800 dark:text-amber-200">{t("inAppInvite")}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {platform === "android" && (
            <a href={androidBrowserIntent()} className={`${STATE_ACTION} bg-amber-600 text-white hover:bg-amber-500`}>
              <FiExternalLink aria-hidden="true" /> {t("openInBrowser")}
            </a>
          )}
          <button type="button" onClick={copy} className={`${STATE_ACTION} bg-white text-amber-900 ring-1 ring-amber-300 hover:bg-amber-100 dark:bg-transparent dark:text-amber-100 dark:ring-amber-500/40`}>
            {copied ? <FiCheck aria-hidden="true" /> : <FiCopy aria-hidden="true" />}
            <span aria-live="polite">{copied ? t("linkCopied") : t("copyLink")}</span>
          </button>
          <button
            type="button"
            onClick={() => setDismissed(true)}
            aria-label={t("dismiss")}
            className={`${STATE_ACTION} px-0 text-amber-800 hover:bg-amber-100 dark:text-amber-200 dark:hover:bg-amber-500/20`}
          >
            <FiX aria-hidden="true" className="text-lg" />
          </button>
        </div>
      </div>
    </aside>
  );
}
