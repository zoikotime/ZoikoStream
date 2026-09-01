// Maps the control socket's state to what the viewer should be told, or null when there is
// nothing to say. Exported so the mapping is testable without mounting the whole watch page.
//
// "connecting" deliberately returns null: a notice during the normal first-connect handshake
// would fire on every single page load and train viewers to ignore the banner.
export function controlPlaneNotice(status, closeReason) {
  if (status === "unauthorized") {
    return {
      tone: "error",
      text: closeReason
        ? `Live chat and updates are unavailable — ${closeReason}. Refresh the page, or sign in again.`
        : "Live chat and updates are unavailable for this session. Refresh the page, or sign in again.",
    };
  }
  if (status === "offline") {
    return {
      tone: "error",
      text: "Live connection unavailable — still retrying in the background. Video and audio are unaffected.",
    };
  }
  if (status === "reconnecting") {
    return {
      tone: "warn",
      text: "Reconnecting to live chat and updates… Video and audio are unaffected.",
    };
  }
  return null;
}
