// Viewer-side playback quality (QoE): what one playback session measured, from the player's
// own state. Pure — no React, no LiveKit, no clock of its own (every call takes `now`, a
// performance.now() reading, so time is monotonic and tests can drive it).
//
// hooks/usePlaybackQoe.js feeds it and sends its snapshots to POST /api/playback/qoe;
// server/app/services/playback_qoe.py stores and aggregates them for /organization/playback.
//
// ── THE STATE MACHINE ──────────────────────────────────────────────────────────────────────
// The player is always in exactly one state, derived from five facts in this priority order:
//
//   ended        the stream / file ended (or the live room is gone for good)     not counted
//   paused       the viewer does not want playback (paused it, or autoplay was
//                blocked and they have not clicked yet)                          not counted
//   hidden       the tab is hidden or the player is off-screen                   not counted
//   interrupted  the BROADCASTER's media is gone: the server declared an
//                interruption, or nothing is being published                     not counted
//   seeking      a replay seek is in progress                                    not counted
//   playing      media is being rendered                                         watch time
//   stalled      none of the above and media is NOT flowing, right after playing  rebuffer
//   waiting      the same, at any other moment (startup, resuming)               startup time
//
// So a stall can only begin from `playing`: initial loading is `waiting`, and so is the
// resume after a pause, a hidden tab or a seek. A reconnect while watching is a stall of the
// SAME session (reconnects continue a session; only a new page is a new session).
//
// A stall ends one of three ways:
//   back to playing, or into paused/hidden/seeking   counted (the viewer sat through it)
//   into ended or interrupted                        discarded: the stream stopping, or the
//                                                    broadcaster dropping, explains the gap,
//                                                    and neither is delivery quality
//   the viewer leaves the page mid-stall             counted, in the final snapshot
// Stalls shorter than MIN_STALL_MS are folded back into watch time: they are state flicker,
// not something a viewer saw. A stall counts for at most MAX_STALL_MS (see there).
//
// `intent` is the PLAYER's state, not only the app's play button: a pause from media keys, the
// OS media session, Picture-in-Picture or native-fullscreen controls, or unplugged headphones
// reaches the page only as the media element's own `pause` event (isViewerPause below), and is
// a pause like any other. Reported as "not flowing" with intent left on, it would read as a
// stall for as long as the viewer stayed paused.

export const FAILURE_REASONS = Object.freeze([
  "media_connection_failed",
  "track_subscription_failed",
  "playback_start_failed",
  "decoder_error",
  "network_error",
  "autoplay_blocked",
  "stream_unavailable",
  "access_denied",
  "timeout",
  "unknown",
]);

// How /organization/playback names each reason.
export const FAILURE_LABELS = Object.freeze({
  media_connection_failed: "Media connection failed",
  track_subscription_failed: "Stream subscription failed",
  playback_start_failed: "Playback start failed",
  decoder_error: "Decoder error",
  network_error: "Network error",
  autoplay_blocked: "Autoplay blocked",
  stream_unavailable: "Stream unavailable",
  access_denied: "Access denied",
  timeout: "Connection timed out",
  unknown: "Unknown",
});

export const MIN_STALL_MS = 250;

// The most one stall can count for. Past two minutes nobody is still sitting in front of a
// frozen player: the live player's own reconnect loop has long since given up its fast retries
// (~40 s, hooks/useLiveKitViewer.js) and told the viewer the connection is lost, and a file
// that has not resumed in two minutes is not going to. What is left is a lost `playing` event,
// a tab abandoned on "Reconnecting…", a frozen device — not something a viewer sat through.
// So a stall stops growing at the cap: it still counts as one stall of MAX_STALL_MS when it
// closes (or the viewer leaves in it), and from the cap until media flows again the session
// accrues nothing at all — neither stall nor watch time — so rebuffer_ms <= playback_ms holds
// and one unattended tab cannot dominate an event's rebuffer ratio.
export const MAX_STALL_MS = 120_000;

/** Whether a `pause` event on a media element is the viewer's own pause — the app's button,
 *  media keys, the OS media session, Picture-in-Picture or native-fullscreen controls and
 *  unplugged headphones all arrive the same way — as opposed to the media reaching its end
 *  (the element pauses itself just before `ended`), a `play()` that already undid it (the
 *  element is playing again by the time the event is handled; its own `play` follows), or a
 *  live element whose MediaStream went inactive because its tracks ended (a delivery problem,
 *  which the live player measures from LiveKit's state instead). */
export function isViewerPause(el) {
  if (!el || !el.paused || el.ended) return false;
  const stream = el.srcObject;
  return !(stream && stream.active === false);
}

export function nextState(prev, { ended = false, intent = true, visible = true, interrupted = false,
                                  seeking = false, flowing = false } = {}) {
  if (ended) return "ended";
  if (!intent) return "paused";
  if (!visible) return "hidden";
  if (interrupted) return "interrupted";
  if (seeking) return "seeking";
  if (flowing) return "playing";
  return prev === "playing" || prev === "stalled" ? "stalled" : "waiting";
}

export class PlaybackQoeSession {
  constructor(now) {
    this.state = "waiting";
    this.since = now;
    this.started = false;
    this.startupMs = null;
    this.waitingMs = 0;
    this.playingMs = 0;
    this.rebufferMs = 0;
    this.rebufferCount = 0;
    this.stallStart = null;
    this.failures = new Set();
    this.ended = false;
  }

  // Time spent in the current state since the last reading.
  accrue(now) {
    const dt = Math.max(0, now - this.since);
    if (this.state === "playing") this.playingMs += dt;
    else if (this.state === "waiting" && !this.started) this.waitingMs += dt;
    this.since = now;
  }

  /** Apply the player's current facts. Returns true when this call STARTED playback. */
  update(inputs, now) {
    this.accrue(now);
    const prev = this.state;
    const next = nextState(prev, inputs);
    if (next === prev) return false;
    if (prev === "stalled") this.closeStall(now, next);
    if (next === "stalled") this.stallStart = now;
    if (next === "ended") this.ended = true;
    this.state = next;
    if (next === "playing" && !this.started) {
      this.started = true;
      // Only the time spent actually trying: paused (autoplay blocked), hidden and
      // waiting-for-the-broadcaster time is not startup.
      this.startupMs = Math.round(this.waitingMs);
      return true;
    }
    return false;
  }

  /** What the open stall would add if it ended at `now`: { count, rebufferMs, playingMs }. */
  openStall(now) {
    const ms = Math.max(0, now - this.stallStart);
    // Flicker: folded back into watch time.
    if (ms < MIN_STALL_MS) return { count: 0, rebufferMs: 0, playingMs: ms };
    return { count: 1, rebufferMs: Math.min(ms, MAX_STALL_MS), playingMs: 0 };
  }

  closeStall(now, next) {
    const stall = this.openStall(now);
    this.stallStart = null;
    // The stream ending (the event completed under a viewer mid-reconnect, or the room is gone
    // for good), or the broadcaster dropping, is not a stall — however long it ran.
    if (next === "ended" || next === "interrupted") return;
    this.rebufferCount += stall.count;
    this.rebufferMs += stall.rebufferMs;
    this.playingMs += stall.playingMs;
  }

  /** Record a normalized failure. Returns true the first time this session hits it. */
  fail(reason) {
    if (!reason) return false;
    const known = FAILURE_REASONS.includes(reason) ? reason : "unknown";
    if (this.failures.has(known)) return false;
    this.failures.add(known);
    return true;
  }

  /** Cumulative totals for a beacon. `final` = the viewer is leaving: an open stall counts. */
  snapshot(now, { final = false } = {}) {
    this.accrue(now);
    let playingMs = this.playingMs;
    let rebufferMs = this.rebufferMs;
    let rebufferCount = this.rebufferCount;
    if (final && this.state === "stalled") {
      const stall = this.openStall(now);
      rebufferMs += stall.rebufferMs;
      rebufferCount += stall.count;
      playingMs += stall.playingMs;
    }
    const started = this.started;
    const rebuffer = started ? Math.round(rebufferMs) : 0;
    return {
      started,
      startup_ms: started ? this.startupMs : null,
      // Watch time includes stall time, so rebuffer_ms <= playback_ms always holds.
      playback_ms: started ? Math.round(playingMs + rebufferMs) : 0,
      rebuffer_count: started ? rebufferCount : 0,
      rebuffer_ms: rebuffer,
      failures: [...this.failures],
      ended: this.ended || final,
    };
  }
}

// ── failure normalization ────────────────────────────────────────────────────────────────
// Each maps a REAL error the browser or livekit-client raised to the closed vocabulary the
// server accepts. null = not a playback failure (this page cancelled it, or the user did).
// Never the error's text: it can carry URLs, and a signed URL is a credential.

/** HTMLMediaElement.error.code (MediaError). */
export function mediaErrorReason(code) {
  switch (code) {
    case 1: return null;                    // MEDIA_ERR_ABORTED: the user or the page stopped it
    case 2: return "network_error";         // MEDIA_ERR_NETWORK
    case 3: return "decoder_error";         // MEDIA_ERR_DECODE
    case 4: return "stream_unavailable";    // MEDIA_ERR_SRC_NOT_SUPPORTED (e.g. an expired link)
    default: return "unknown";
  }
}

/** A rejected HTMLMediaElement.play(). */
export function playRejectionReason(error) {
  switch (error?.name) {
    case "NotAllowedError": return "autoplay_blocked";
    // A pause() or a new source interrupted the play() request: the page's own doing.
    case "AbortError": return null;
    case "NotSupportedError": return "stream_unavailable";
    default: return "playback_start_failed";
  }
}

/** A failed livekit-client Room.connect(). Classified by ConnectionError.reasonName (the
 *  ConnectionErrorReason member's name), so this module needs no LiveKit import. */
export function connectErrorReason(error, { online = typeof navigator === "undefined" ? true : navigator.onLine } = {}) {
  if (online === false) return "network_error";
  if (error?.name !== "ConnectionError") return "media_connection_failed";
  switch (error.reasonName) {
    case "NotAllowed": return "access_denied";
    case "Timeout": return "timeout";
    case "Cancelled": return null;          // this page cancelled it (a newer attempt, leaving)
    case "LeaveRequest": return "stream_unavailable";
    default: return "media_connection_failed";
  }
}
