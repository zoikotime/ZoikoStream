// What a recording row actually means, as a verdict rather than a raw column.
//
// Its own module (not the component file) so tests and non-component code can import it
// without pulling React in, and so RecordingPanel keeps Fast Refresh — the same reason
// pages/organization/roleConfig.js and data/timezoneSearch.js exist.
//
// ── THE VERDICT NOW COMES FROM THE SERVER ───────────────────────────────────────────────
// This used to derive the verdict here, from `status` + `enforced` + `url`. Two pages doing
// that independently is what produced the reported bug: the org library insisted a recording
// did not exist while the event's own Recording tab was showing it. Both endpoints now send
// `state`, computed once by crud.event.recording_library_state, and this function's job is
// to turn that one word into copy.
//
// The local derivation is kept only as a fallback for a response from a server that predates
// the field, and it is deliberately the conservative reading — it will say "processing"
// rather than invent a "ready" that has no file behind it.
//
// `eventEnded` matters for exactly one case and it is the reported one. A row stuck at
// status="recording" on an event that has already ended must not say "it will finish when
// the broadcast ends", because the broadcast ended. That sentence is how a stranded row
// looked normal for days.

const READY = { key: "ready", label: "Ready", tone: "success", detail: null };

/**
 * @param {object|null} rec       recording row from the API
 * @param {{eventEnded?: boolean}} [opts]
 * @returns {{key: "ready"|"processing"|"in_progress"|"failed"|"storage_unavailable", label, tone, detail}|null}
 */
export function recordingState(rec, opts = {}) {
  if (!rec) return null;
  const { eventEnded = false } = opts;

  switch (rec.state) {
    case "ready":
      return READY;
    case "processing":
      return {
        key: "processing",
        label: "Processing",
        tone: "info",
        detail: eventEnded
          ? "The broadcast has ended and the recording is still being finalised. It will appear here once the media server confirms the upload."
          : "The capture finished and the file is still being finalised. Refresh shortly.",
      };
    case "storage_unavailable":
      // Terminal, the provider accepted the job, and yet nothing is in the bucket. This is
      // distinct from "failed": the capture may well have run, but the file is not there, so
      // it cannot be offered. Saying so is the whole point — the alternative was a signed URL
      // to a missing object and raw GCS XML in the user's browser.
      return {
        key: "storage_unavailable",
        label: "Storage unavailable",
        tone: "danger",
        detail:
          rec.error ||
          "The recording file is not available in storage, so it cannot be played or downloaded.",
      };
    case "failed":
      return {
        key: "failed",
        label: "Not captured",
        tone: "danger",
        // The server's own reason, verbatim. Paraphrasing it would be a guess; the real
        // message names the actual fault (a bad storage credential reads very differently
        // from egress being unavailable).
        detail: rec.error || "LiveKit egress did not produce a file for this recording.",
      };
    case "in_progress":
      break; // fall through to the shared live/stranded handling below
    default:
      break; // no `state` field — older server; fall back
  }

  // Live, or stranded and indistinguishable from live without the event's own status.
  if (rec.state === "in_progress" || rec.status === "recording" || rec.status === "paused") {
    if (eventEnded) {
      return {
        key: "processing",
        label: "Processing",
        tone: "info",
        detail:
          "The broadcast has ended. This capture has not been confirmed finished by the media server yet — it will resolve automatically, or be reconciled if the completion never arrives.",
      };
    }
    return {
      key: "in_progress",
      label: rec.status === "paused" ? "Paused" : "Recording",
      tone: "warning",
      detail: "This recording is still running. It will finish when the broadcast ends.",
    };
  }

  // ── fallback for a server that does not send `state` ──────────────────────────────────
  if (rec.status === "failed" || (!rec.enforced && rec.status === "stopped")) {
    return {
      key: "failed",
      label: "Not captured",
      tone: "danger",
      detail: rec.error || "LiveKit egress did not produce a file for this recording.",
    };
  }
  if (rec.status === "processing" || (rec.status === "stopped" && !rec.url)) {
    return {
      key: "processing",
      label: "Processing",
      tone: "info",
      detail: "The capture finished and the file is still being finalised. Refresh shortly.",
    };
  }
  return READY;
}

// The one place that decides whether playback/download may be offered. Both the library card
// and the event tab call this rather than testing `url` themselves, so neither can put a
// dead control on screen.
export const isPlayable = (rec) => Boolean(rec?.url) && recordingState(rec)?.key === "ready";
