// The playback-quality state machine (data/playbackQoe.js): what one playback session counts
// as a start, as watch time and as rebuffering — and what it refuses to count.
import { describe, expect, it } from "vitest";
import {
  FAILURE_LABELS,
  FAILURE_REASONS,
  MAX_STALL_MS,
  MIN_STALL_MS,
  PlaybackQoeSession,
  connectErrorReason,
  isViewerPause,
  mediaErrorReason,
  nextState,
  playRejectionReason,
} from "./playbackQoe";

// The facts of a viewer who wants playback, can see the player, and is receiving media.
const PLAYING = { intent: true, visible: true, interrupted: false, seeking: false, flowing: true, ended: false };
const STALLED = { ...PLAYING, flowing: false };
const PAUSED = { ...PLAYING, intent: false };
const HIDDEN = { ...PLAYING, visible: false };

// Drives a session through [factsAt, ms] steps on a synthetic monotonic clock.
function run(steps, { start = 0 } = {}) {
  const s = new PlaybackQoeSession(start);
  let t = start;
  const starts = [];
  for (const [facts, ms] of steps) {
    if (s.update(facts, t)) starts.push(t);
    t += ms;
  }
  return { s, t, starts };
}

describe("a playback start", () => {
  it("is the first moment media plays, counted once however the facts repeat", () => {
    const { s, t, starts } = run([
      [STALLED, 1_500],          // connecting: startup, not rebuffering
      [PLAYING, 10_000],
      [PLAYING, 10_000],         // a re-render with the same facts
      [STALLED, 2_000],          // a reconnect: same session
      [PLAYING, 5_000],
    ]);
    expect(starts).toEqual([1_500]);
    const snap = s.snapshot(t);
    expect(snap).toMatchObject({ started: true, startup_ms: 1_500, rebuffer_count: 1, rebuffer_ms: 2_000 });
    expect(snap.playback_ms).toBe(27_000);
  });

  it("never happens while media is not playing, so a page load alone reports nothing", () => {
    const { s, t } = run([[STALLED, 30_000]]);
    expect(s.snapshot(t)).toEqual({ started: false, startup_ms: null, playback_ms: 0, rebuffer_count: 0,
                                    rebuffer_ms: 0, failures: [], ended: false });
  });

  it("does not count paused (autoplay blocked), hidden or broadcaster-wait time as startup", () => {
    const { s, t } = run([
      [{ ...STALLED, interrupted: true }, 20_000],  // waiting for the host's camera
      [PAUSED, 15_000],                             // autoplay blocked until the click
      [STALLED, 700],
      [HIDDEN, 9_000],
      [STALLED, 300],
      [PLAYING, 1_000],
    ]);
    expect(s.snapshot(t).startup_ms).toBe(1_000);
  });
});

describe("rebuffering", () => {
  it("counts a stall in the middle of playback, by its duration", () => {
    const { s, t } = run([[PLAYING, 60_000], [STALLED, 4_500], [PLAYING, 30_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 1, rebuffer_ms: 4_500, playback_ms: 94_500 });
  });

  it("does not count the initial loading before playback", () => {
    const { s, t } = run([[STALLED, 8_000], [PLAYING, 10_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 0, rebuffer_ms: 0, startup_ms: 8_000 });
  });

  it("does not count a pause, and the pause adds no watch time", () => {
    const { s, t } = run([[PLAYING, 10_000], [PAUSED, 120_000], [PLAYING, 10_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 0, playback_ms: 20_000 });
  });

  it("counts the stall a viewer sat through before pausing, but not the pause", () => {
    const { s, t } = run([[PLAYING, 10_000], [STALLED, 3_000], [PAUSED, 60_000], [PLAYING, 5_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 1, rebuffer_ms: 3_000, playback_ms: 18_000 });
  });

  it("does not count a hidden tab, nor the media catching up after it", () => {
    const { s, t } = run([[PLAYING, 10_000], [HIDDEN, 300_000], [STALLED, 2_000], [PLAYING, 10_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 0, playback_ms: 20_000 });
  });

  it("does not count a replay seek", () => {
    const { s, t } = run([[PLAYING, 10_000], [{ ...STALLED, seeking: true }, 2_000], [STALLED, 400], [PLAYING, 5_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 0 });
  });

  it("discards a stall the broadcaster or the stream's end explains", () => {
    const interrupted = run([[PLAYING, 10_000], [STALLED, 4_000], [{ ...STALLED, interrupted: true }, 60_000], [PLAYING, 1_000]]);
    expect(interrupted.s.snapshot(interrupted.t)).toMatchObject({ rebuffer_count: 0, playback_ms: 11_000 });
    const ended = run([[PLAYING, 10_000], [STALLED, 4_000], [{ ...STALLED, ended: true }, 1_000]]);
    expect(ended.s.snapshot(ended.t)).toMatchObject({ rebuffer_count: 0, ended: true });
  });

  it("folds an imperceptible stall back into watch time", () => {
    const { s, t } = run([[PLAYING, 10_000], [STALLED, MIN_STALL_MS - 1], [PLAYING, 10_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 0, playback_ms: 20_000 + MIN_STALL_MS - 1 });
  });

  it("counts the stall the viewer left in — in the final snapshot only", () => {
    const { s, t } = run([[PLAYING, 10_000], [STALLED, 7_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 0, playback_ms: 10_000 });
    expect(s.snapshot(t, { final: true })).toMatchObject({ rebuffer_count: 1, rebuffer_ms: 7_000,
                                                          playback_ms: 17_000, ended: true });
  });

  it("never reports more stall time than watch time, whatever the sequence", () => {
    let seed = 7;
    const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let trial = 0; trial < 200; trial += 1) {
      const steps = Array.from({ length: 12 }, () => [{
        intent: rand() > 0.15, visible: rand() > 0.15, interrupted: rand() > 0.9,
        seeking: rand() > 0.9, flowing: rand() > 0.4, ended: rand() > 0.97,
      // Mostly seconds, sometimes far past the stall cap.
      }, Math.floor(rand() * (rand() > 0.8 ? 3 * MAX_STALL_MS : 5_000))]);
      const { s, t } = run(steps);
      for (const final of [false, true]) {
        const snap = s.snapshot(t, { final });
        expect(snap.rebuffer_ms).toBeLessThanOrEqual(snap.playback_ms);
        expect(snap.rebuffer_ms).toBeLessThanOrEqual(snap.rebuffer_count * MAX_STALL_MS);
        expect(snap.playback_ms).toBeLessThanOrEqual(t);
        if (snap.rebuffer_ms > 0) expect(snap.rebuffer_count).toBeGreaterThan(0);
        if (!snap.started) expect(snap.playback_ms + snap.rebuffer_count).toBe(0);
      }
    }
  });
});

describe("a pause, whoever made it", () => {
  // The player reports the media element's own pause as intent=false (components/watch/
  // VideoPlayer.jsx), whether it came from the page's button or from outside the page.
  it("is never rebuffering, and a stall it interrupts stops counting at the pause", () => {
    const { s, t } = run([[PLAYING, 10_000], [STALLED, 2_000], [PAUSED, 600_000], [PLAYING, 10_000]]);
    expect(s.snapshot(t, { final: true })).toMatchObject({ rebuffer_count: 1, rebuffer_ms: 2_000,
                                                           playback_ms: 22_000 });
  });

  it("is resumed through `waiting`, never through a fake stall", () => {
    expect(nextState("paused", STALLED)).toBe("waiting");
    const { s, t } = run([[PLAYING, 10_000], [PAUSED, 60_000], [STALLED, 1_500], [PLAYING, 5_000]]);
    expect(s.snapshot(t, { final: true })).toMatchObject({ rebuffer_count: 0, rebuffer_ms: 0,
                                                           playback_ms: 15_000 });
  });

  it("is told apart from the media's end, a play() that undid it, and a live stream going inactive", () => {
    expect(isViewerPause({ paused: true, ended: false })).toBe(true);                        // button, media keys, OS, PiP
    expect(isViewerPause({ paused: true, ended: false, srcObject: { active: true } })).toBe(true); // live, OS pause
    expect(isViewerPause({ paused: true, ended: true })).toBe(false);                        // reached the end
    expect(isViewerPause({ paused: false, ended: false })).toBe(false);                      // already playing again
    expect(isViewerPause({ paused: true, ended: false, srcObject: { active: false } })).toBe(false); // tracks ended
    expect(isViewerPause(null)).toBe(false);
  });
});

describe("the stall cap", () => {
  it("counts one stall for at most MAX_STALL_MS, and accrues nothing past it", () => {
    const { s, t } = run([[PLAYING, 10_000], [STALLED, 10 * MAX_STALL_MS], [PLAYING, 10_000]]);
    expect(s.snapshot(t)).toMatchObject({ rebuffer_count: 1, rebuffer_ms: MAX_STALL_MS,
                                          playback_ms: 20_000 + MAX_STALL_MS });
  });

  it("caps the stall the viewer leaves in, however long the tab sat there", () => {
    const { s, t } = run([[PLAYING, 10_000], [STALLED, MAX_STALL_MS + 1_000]]);
    const atCap = s.snapshot(t, { final: true });
    const hoursLater = s.snapshot(t + 5 * 3_600_000, { final: true });
    expect(atCap).toMatchObject({ rebuffer_count: 1, rebuffer_ms: MAX_STALL_MS, playback_ms: 10_000 + MAX_STALL_MS });
    expect(hoursLater).toEqual(atCap);
    expect(hoursLater.rebuffer_ms).toBeLessThanOrEqual(hoursLater.playback_ms);
  });

  it("does not stop a stall the stream's end explains from being discarded", () => {
    const { s, t } = run([[PLAYING, 10_000], [STALLED, 3 * MAX_STALL_MS], [{ ...STALLED, ended: true }, 1_000]]);
    expect(s.snapshot(t, { final: true })).toMatchObject({ rebuffer_count: 0, rebuffer_ms: 0,
                                                           playback_ms: 10_000, ended: true });
  });
});

describe("the state", () => {
  it("is decided in priority order", () => {
    expect(nextState("playing", { ...STALLED, ended: true, intent: false })).toBe("ended");
    expect(nextState("playing", { ...STALLED, intent: false, visible: false })).toBe("paused");
    expect(nextState("playing", { ...STALLED, visible: false, interrupted: true })).toBe("hidden");
    expect(nextState("playing", { ...STALLED, interrupted: true })).toBe("interrupted");
    expect(nextState("playing", STALLED)).toBe("stalled");
    expect(nextState("paused", STALLED)).toBe("waiting");
  });
});

describe("failures", () => {
  it("are kept once per reason, and anything unrecognised is 'unknown'", () => {
    const s = new PlaybackQoeSession(0);
    expect(s.fail("network_error")).toBe(true);
    expect(s.fail("network_error")).toBe(false);
    expect(s.fail(null)).toBe(false);
    expect(s.fail("Error: GET https://x/y?sig=secret 403")).toBe(true);
    expect(s.snapshot(0).failures).toEqual(["network_error", "unknown"]);
  });

  it("every reason has a label", () => {
    expect(Object.keys(FAILURE_LABELS).sort()).toEqual([...FAILURE_REASONS].sort());
  });

  it("autoplay refusal has its own reason, and a page-cancelled play() is no failure", () => {
    expect(playRejectionReason({ name: "NotAllowedError" })).toBe("autoplay_blocked");
    expect(playRejectionReason({ name: "AbortError" })).toBeNull();
    expect(playRejectionReason({ name: "NotSupportedError" })).toBe("stream_unavailable");
    expect(playRejectionReason(new Error("boom"))).toBe("playback_start_failed");
  });

  it("maps media element errors", () => {
    expect([1, 2, 3, 4, undefined].map(mediaErrorReason))
      .toEqual([null, "network_error", "decoder_error", "stream_unavailable", "unknown"]);
  });

  it("maps LiveKit connection errors by reason name", () => {
    const err = (reasonName) => ({ name: "ConnectionError", reasonName });
    expect(connectErrorReason(err("NotAllowed"), { online: true })).toBe("access_denied");
    expect(connectErrorReason(err("Timeout"), { online: true })).toBe("timeout");
    expect(connectErrorReason(err("Cancelled"), { online: true })).toBeNull();
    expect(connectErrorReason(err("LeaveRequest"), { online: true })).toBe("stream_unavailable");
    expect(connectErrorReason(err("ServerUnreachable"), { online: true })).toBe("media_connection_failed");
    expect(connectErrorReason(new TypeError("fetch failed"), { online: true })).toBe("media_connection_failed");
    expect(connectErrorReason(err("Timeout"), { online: false })).toBe("network_error");
  });
});
