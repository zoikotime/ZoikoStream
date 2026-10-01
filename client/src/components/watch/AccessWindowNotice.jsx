// client/src/components/watch/AccessWindowNotice.jsx
// Shown in place of the video player when there is nothing to watch YET or ANY MORE:
//   * "not_started": the pre-event state (ZST-SPEC-VAP-001 §6.3). The event's identity, its
//     start in the viewer's own time AND in UTC, and the one instruction that matters —
//     "Service will begin here automatically." The page polls in the background
//     (EventWatch), so this really does turn into the player by itself.
//   * "expired": the scheduled viewing window closed before the broadcast ran.
// Purely informational: the access decision (withholding the LiveKit token) is made
// server-side by GET /events/{id}/watch. This explains it.
import { FiClock, FiCheckCircle } from "react-icons/fi";
import { formatEventTimes, useViewerLanguage } from "../../pages/watch/viewerLanguage";
import ViewerStateCard from "./ViewerStateCard";

export default function AccessWindowNotice({ variant, startTime, eventTitle }) {
  const { t, lang } = useViewerLanguage();
  if (variant === "expired") {
    return (
      <ViewerStateCard icon={FiCheckCircle} title={t("windowClosedTitle")} testId="state-window-closed">
        <p>{t("windowClosedBody")}</p>
      </ViewerStateCard>
    );
  }
  const times = formatEventTimes(startTime, lang);
  return (
    <ViewerStateCard icon={FiClock} tone="waiting" title={t("preEventTitle")} testId="state-pre-event">
      {eventTitle && <p className="font-medium text-slate-800 dark:text-slate-100">{eventTitle}</p>}
      {times && (
        <dl className="space-y-0.5">
          <div>
            <dt className="sr-only">{t("yourTime")}</dt>
            <dd><time dateTime={startTime}>{times.local}</time></dd>
          </div>
          <div>
            <dt className="sr-only">UTC</dt>
            <dd className="text-slate-500 dark:text-slate-400">{times.utc}</dd>
          </div>
        </dl>
      )}
      <p className="pt-1 text-base font-semibold text-slate-900 dark:text-white">{t("preEventBody")}</p>
    </ViewerStateCard>
  );
}
