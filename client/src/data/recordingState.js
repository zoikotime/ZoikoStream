// What a recording row actually means, as a verdict rather than a raw column.
//
// Its own module (not the component file) so tests and non-component code can import it
// without pulling React in, and so RecordingPanel keeps Fast Refresh — the same reason
// pages/organization/roleConfig.js and data/timezoneSearch.js exist.
//
// The field that matters most and is easiest to miss is `enforced`: it is False when LiveKit
// egress never accepted the job, so the row exists but NO file was ever written. A row can
// be status="stopped" and still have nothing behind it. Treating stopped as "done" is what
// would put a dead Watch link in front of an operator.

/**
 * @returns {{key: "ready"|"processing"|"in_progress"|"failed", label, tone, detail}|null}
 */
export function recordingState(rec) {
  if (!rec) return null;

  // Not captured: either explicitly failed, or finished without egress ever enforcing it.
  if (rec.status === "failed" || (!rec.enforced && rec.status === "stopped")) {
    return {
      key: "failed",
      label: "Not captured",
      tone: "danger",
      // The server's own reason, verbatim. Paraphrasing it would be a guess; the real
      // message names the actual fault (a bad storage credential reads very differently
      // from egress being unavailable).
      detail: rec.error || "LiveKit egress did not produce a file for this recording.",
    };
  }

  if (rec.status === "recording" || rec.status === "paused") {
    return {
      key: "in_progress",
      label: rec.status === "paused" ? "Paused" : "Recording",
      tone: "warning",
      detail: "This recording is still running. It will finish when the broadcast ends.",
    };
  }

  // Captured, but not retrievable yet: egress finalises and uploads after the room closes,
  // so there is a real window where the row is done and the link is not.
  if (rec.status === "stopped" && !rec.url) {
    return {
      key: "processing",
      label: "Processing",
      tone: "info",
      detail: "The capture finished and the file is still being finalised. Refresh shortly.",
    };
  }

  return { key: "ready", label: "Ready", tone: "success", detail: null };
}
