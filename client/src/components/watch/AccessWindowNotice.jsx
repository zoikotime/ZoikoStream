// client/src/components/watch/AccessWindowNotice.jsx
// Shown in place of the video player when the visitor is outside the event's scheduled
// start_time/end_time window (see GET /events/{id}/watch's not_started/expired fields).
// Purely informational — the actual access decision (withholding the LiveKit token) is
// made server-side, this just explains why there's nothing to watch yet/anymore.
import { FiClock, FiCheckCircle } from "react-icons/fi";
import { fmtDateTime } from "../../data/events";

export default function AccessWindowNotice({ variant, startTime }) {
  const ended = variant === "expired";
  return (
    <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-2xl border border-slate-200 bg-white p-6 text-center shadow-sm dark:border-slate-800 dark:bg-slate-900 sm:p-10">
      {ended ? (
        <>
          <FiCheckCircle className="text-3xl text-slate-400" />
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">This event has ended</h2>
          <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
            The scheduled access window for this event has closed.
          </p>
        </>
      ) : (
        <>
          <FiClock className="text-3xl text-emerald-500" />
          <h2 className="text-lg font-semibold text-slate-900 dark:text-white">This event hasn't started yet</h2>
          <p className="max-w-sm text-sm text-slate-500 dark:text-slate-400">
            {startTime ? `Starts at ${fmtDateTime(startTime)}.` : "Check back at the scheduled start time."}
          </p>
        </>
      )}
    </div>
  );
}
