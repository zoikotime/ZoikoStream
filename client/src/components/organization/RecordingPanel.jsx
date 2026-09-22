import { FiAlertTriangle, FiDownload, FiLoader, FiLock, FiPlay, FiVideo } from "react-icons/fi";
import Badge from "../../ui/Badge";
import { CONSOLE, cx } from "../../ui/tokens";
import OrganizationEmptyState from "./OrganizationEmptyState";
import { recordingState } from "../../data/recordingState";

// What actually happened to this event's recording.
//
// ── THE BUG THIS REPLACES ────────────────────────────────────────────────────────────────
// The Recording tab was hardcoded markup: <OrganizationEmptyState title="No recording
// available" />, with no fetch behind it. It printed that for every event, forever —
// including events whose file was captured and sitting in storage. A host who recorded a
// broadcast and then ended it had no way to tell "the file is still uploading" from "egress
// never started because the storage credential is wrong" from "nobody pressed Record",
// because all three rendered the same sentence.
//
// So this renders the rows GET /organization/events/{id}/recordings returns, which includes
// attempts that captured NOTHING. The rule it follows: never imply a file that does not
// exist. A row is playable only when the server sent a `url`, which it only does when the
// capture was enforced and a file_url exists. Everything else states its real status, and a
// failure states its real reason.

const fmtSize = (b) => {
  if (typeof b !== "number") return null;
  if (b < 1024 ** 2) return `${Math.round(b / 1024)} KB`;
  if (b < 1024 ** 3) return `${(b / 1024 ** 2).toFixed(1)} MB`;
  return `${(b / 1024 ** 3).toFixed(2)} GB`;
};

const fmtDuration = (s) => {
  if (typeof s !== "number" || s < 0) return null;
  const m = Math.floor(s / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : m >= 1 ? `${m}m` : `${Math.round(s)}s`;
};

function Row({ rec }) {
  const st = recordingState(rec);
  const size = fmtSize(rec.size_bytes);
  const dur = fmtDuration(rec.duration_seconds);
  const facts = [
    rec.role ? rec.role[0].toUpperCase() + rec.role.slice(1) : null,
    rec.quality,
    dur,
    size,
  ].filter(Boolean);

  return (
    <li className="flex flex-col gap-3 px-5 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-start gap-3">
        <span
          className={cx(
            "mt-0.5 grid h-9 w-9 shrink-0 place-items-center rounded-lg",
            st.key === "ready"
              ? "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400"
              : st.key === "failed"
                ? "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400"
                : "bg-slate-100 text-slate-500 dark:bg-white/[0.07] dark:text-neutral-400"
          )}
          aria-hidden="true"
        >
          {st.key === "failed" ? <FiAlertTriangle />
            : st.key === "ready" ? <FiVideo />
              : <FiLoader />}
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={st.tone}>{st.label}</Badge>
            {rec.legal_hold && (
              <Badge tone="neutral">
                <FiLock className="mr-1 inline text-[10px]" aria-hidden="true" />
                Legal hold
              </Badge>
            )}
            {facts.length > 0 && (
              <span className={cx("text-[12px]", CONSOLE.faint)}>{facts.join(" · ")}</span>
            )}
          </div>
          {st.detail && (
            <p className={cx("mt-1 text-[12px] leading-relaxed", CONSOLE.faint)}>{st.detail}</p>
          )}
        </div>
      </div>

      {/* A link ONLY when the server sent one. A row with no file gets no affordance at all
          rather than a button that 404s. */}
      {rec.url && (
        <div className="flex shrink-0 items-center gap-2">
          <a
            href={rec.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-lg bg-violet-600 px-3 py-1.5 text-xs font-medium text-white transition hover:bg-violet-700"
          >
            <FiPlay className="text-[13px]" aria-hidden="true" /> Watch
          </a>
          <a
            href={rec.url}
            download
            className="inline-flex items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800"
          >
            <FiDownload className="text-[13px]" aria-hidden="true" /> Download
          </a>
        </div>
      )}
    </li>
  );
}

export default function RecordingPanel({ event, recordings }) {
  // null means the request FAILED (EventDetails catches and passes null). That is not the
  // same as an empty list, and saying "no recording available" for it would be the original
  // bug in a new costume.
  if (recordings === null || recordings === undefined) {
    return (
      <OrganizationEmptyState
        icon={FiAlertTriangle}
        title="Couldn't load recordings"
        description="The recording list for this event could not be read. Refresh to try again."
      />
    );
  }

  if (recordings.length === 0) {
    return (
      <OrganizationEmptyState
        icon={FiVideo}
        title="No recording available"
        description={
          event?.recording_enabled
            ? "Nothing was recorded for this event. Start a recording from the Producer Console while the event is live."
            : "Recording is disabled for this event."
        }
      />
    );
  }

  const failed = recordings.filter((r) => recordingState(r).key === "failed").length;

  return (
    <div>
      {failed > 0 && (
        // Surfaced at the top because the per-row reason is the thing an operator needs and
        // would otherwise have to go looking for — the old tab hid this completely.
        <div className="flex items-start gap-2.5 border-b border-rose-200 bg-rose-50 px-5 py-3 dark:border-rose-500/25 dark:bg-rose-500/10">
          <FiAlertTriangle className="mt-0.5 shrink-0 text-rose-500" aria-hidden="true" />
          <p className="text-[13px] leading-relaxed text-rose-800 dark:text-rose-200">
            {failed === recordings.length
              ? "This event's recording did not capture a file."
              : `${failed} of ${recordings.length} recording paths did not capture a file.`}{" "}
            The reason is shown below — this usually needs an operator, not a retry.
          </p>
        </div>
      )}
      <ul className="divide-y divide-slate-100 dark:divide-slate-800">
        {recordings.map((r) => <Row key={r.id} rec={r} />)}
      </ul>
    </div>
  );
}
