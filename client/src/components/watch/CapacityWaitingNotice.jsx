// client/src/components/watch/CapacityWaitingNotice.jsx
// The capacity-protection waiting state (ZST-SPEC-VAP-001 §6.3: "New sessions wait; active
// sessions are never evicted"). GET /events/{id}/watch answered admission="waiting": the event
// is live and this viewer may watch, but the infrastructure ceiling is reached
// (server/app/services/admission.py). Nobody already watching is affected by this viewer
// waiting, and this viewer is let in automatically the moment there is room.
//
// The countdown is this component's own state, ticked by an interval. The parent remounts it
// (key = attempt) for every retry, so each wait starts clean. The retry itself is jittered
// (the delay is not shown to the second), so a full event is not hit by every waiting browser
// in the same instant.
import { useEffect, useRef, useState } from "react";
import { FiUsers, FiRefreshCw } from "react-icons/fi";
import { useViewerLanguage } from "../../pages/watch/viewerLanguage";
import ViewerStateCard, { STATE_ACTION } from "./ViewerStateCard";

export const MAX_WAIT_SECONDS = 60;

/** Bounded backoff: the server's retry_after, growing 1.5× per attempt, capped at a minute. */
// eslint-disable-next-line react-refresh/only-export-components
export function waitSeconds(retryAfterSeconds, attempt) {
  const base = Math.max(1, Number(retryAfterSeconds) || 10);
  return Math.min(Math.round(base * 1.5 ** Math.max(0, attempt)), MAX_WAIT_SECONDS);
}

export default function CapacityWaitingNotice({ retryAfterSeconds, attempt = 0, onRetry }) {
  const { t } = useViewerLanguage();
  const initial = waitSeconds(retryAfterSeconds, attempt);
  const [remaining, setRemaining] = useState(initial);
  const left = useRef(initial);
  const fired = useRef(false);
  const retry = useRef(onRetry);
  useEffect(() => {
    retry.current = onRetry;
  });

  useEffect(() => {
    let jitterTimer = null;
    const timer = setInterval(() => {
      left.current = Math.max(0, left.current - 1);
      setRemaining(left.current);
      if (left.current === 0 && !fired.current) {
        fired.current = true;
        clearInterval(timer);
        // Up to two seconds of jitter, so waiting browsers do not all retry together.
        jitterTimer = setTimeout(() => retry.current?.(), Math.random() * 2000);
      }
    }, 1000);
    return () => {
      clearInterval(timer);
      if (jitterTimer) clearTimeout(jitterTimer);
    };
  }, []);

  const tryNow = () => {
    if (fired.current) return;
    fired.current = true;
    onRetry?.();
  };

  return (
    <ViewerStateCard
      icon={FiUsers}
      tone="waiting"
      title={t("capacityTitle")}
      testId="state-capacity-waiting"
      actions={
        <button type="button" onClick={tryNow} className={`${STATE_ACTION} bg-emerald-600 text-white hover:bg-emerald-500`}>
          <FiRefreshCw aria-hidden="true" /> {t("tryNow")}
        </button>
      }
    >
      <p className="text-base font-semibold text-slate-900 dark:text-white">{t("capacityBody")}</p>
      {/* Not a live region of its own: the card already announced the state once, and a
          screen reader reading out every second of a countdown is noise. */}
      <p className="text-slate-500 dark:text-slate-400" aria-live="off">{t("capacityRetry", { seconds: remaining })}</p>
    </ViewerStateCard>
  );
}
