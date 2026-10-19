// The real VideoPlayer reporting its own playback quality (hooks/usePlaybackQoe.js), live and
// replay, from the facts it actually has: LiveKit's connection/track state for live, the
// <video> element's media events for replay. LiveKit itself is a controllable stand-in.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const lk = {};
let hookArgs = null;
const resetLiveKit = () => Object.assign(lk, {
  connected: true, reconnecting: false, hasVideo: true, hasAudio: true, error: null,
  streamPaused: false, stopped: false, hasVideoPublication: true,
});

vi.mock("../../hooks/useLiveKitViewer", async (importOriginal) => {
  const mediaRef = { current: null };
  const { resolveQuality } = await importOriginal();
  return {
    resolveQuality,
    default: (args) => {
      hookArgs = args;
      return {
        mediaRef, ...lk,
        videoLayers: [], quality: "auto", selectQuality: vi.fn(),
        micOn: false, micError: null, toggleMic: vi.fn(), enableMic: vi.fn(), micLive: false,
      };
    },
  };
});
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), info: vi.fn() } }));

import VideoPlayer from "./VideoPlayer";
import { ThemeProvider } from "../../theme/ThemeContext";
import { MAX_STALL_MS } from "../../data/playbackQoe";

const LIVE_EVENT = { id: "e1", name: "Town hall", status: "Live", host: "Ada", accent: "violet" };
const LIVE_WATCH = { id: "e1", status: "live", livekit_token: "lk", livekit_url: "wss://lk.test", room: "r1",
                     qoe_token: "live-session-token-0001" };
const ENDED_EVENT = { ...LIVE_EVENT, status: "Completed" };
const REPLAY_WATCH = { id: "e1", status: "ended", recording_url: "https://signed.example/replay.mp4",
                       replay_state: "available", qoe_token: "replay-session-token-01" };

let clock = 0;
const tick = (ms) => act(() => {
  clock += ms;
  vi.advanceTimersByTime(ms);
});
const realPlay = HTMLMediaElement.prototype.play;

beforeEach(() => {
  clock = 0;
  resetLiveKit();
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 204 }));
});
afterEach(() => {
  HTMLMediaElement.prototype.play = realPlay;
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const beacons = () => fetch.mock.calls.map(([, init]) => JSON.parse(init.body));
const player = (event, watch) => (
  <ThemeProvider>
    <VideoPlayer event={event} viewers={3} watch={watch} />
  </ThemeProvider>
);
function show(event = LIVE_EVENT, watch = LIVE_WATCH) {
  const utils = render(player(event, watch));
  return { ...utils, again: () => utils.rerender(player(event, watch)) };
}
const liveVideo = (container) => container.querySelector("video");

describe("live playback quality", () => {
  it("starts on the first frame playing — a connected room and subscribed tracks are not playback", () => {
    const { container, again } = show();
    tick(900);
    again();
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.playing(liveVideo(container));
    expect(beacons()).toEqual([expect.objectContaining({ token: LIVE_WATCH.qoe_token, seq: 1, started: true,
                                                         startup_ms: 900 })]);
    again();
    again();
    expect(beacons()).toHaveLength(1);
  });

  // Updated with the reconnect fix: this used to resume watch time the instant `Reconnected`
  // landed, with no `playing` from the element. In a browser the reconnect re-attaches the
  // video (useLiveKitViewer's Reconnected handler), and the wait for that first frame is part
  // of the stall — it now counts as such, and playback resumes on the element's `playing`.
  it("counts a reconnect (up to the re-attached video's first frame) and an SFU pause as stalls of the same session", () => {
    const { container, again, unmount } = show();
    fireEvent.playing(liveVideo(container));
    tick(10_000);
    lk.reconnecting = true;            // LiveKit RoomEvent.Reconnecting
    again();
    tick(3_000);
    lk.reconnecting = false;           // Reconnected: the same session carries on...
    again();
    tick(500);                         // ...once the re-attached video plays again
    fireEvent.playing(liveVideo(container));
    tick(9_500);
    lk.streamPaused = true;            // the SFU stopped forwarding the watched video
    again();
    tick(2_000);
    lk.streamPaused = false;
    again();
    tick(5_000);
    unmount();
    const last = beacons().at(-1);
    expect(last).toMatchObject({ token: LIVE_WATCH.qoe_token, rebuffer_count: 2, rebuffer_ms: 5_500,
                                 playback_ms: 30_000, ended: true });
    expect(beacons().filter((b) => b.started && b.seq === 1)).toHaveLength(1);
  });

  it("treats the live element paused from outside the page (media keys, PiP) as a pause, not watch time or a stall", () => {
    const { container, again, unmount } = show();
    const video = liveVideo(container);
    fireEvent.playing(video);
    tick(10_000);
    fireEvent.pause(video);            // OS media key / the PiP window's pause button
    expect(screen.queryAllByRole("button", { name: "Pause" })).toHaveLength(0);   // the button follows
    tick(60_000);
    lk.reconnecting = true;            // a reconnect while paused is nobody's stall
    again();
    tick(5_000);
    lk.reconnecting = false;
    again();
    tick(1_000);
    fireEvent.play(video);             // resumed from the OS / the PiP window
    expect(screen.getAllByRole("button", { name: "Pause" }).length).toBeGreaterThan(0);
    tick(400);                         // resuming: waiting for a frame, not a stall
    fireEvent.playing(video);
    tick(10_000);
    unmount();
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 0, rebuffer_ms: 0, playback_ms: 20_000 });
  });

  it("does not count a stall the stream's end explains: the event completing mid-reconnect", () => {
    const { container, again, rerender } = show();
    fireEvent.playing(liveVideo(container));
    tick(10_000);
    lk.reconnecting = true;
    again();
    tick(30_000);
    rerender(player(ENDED_EVENT, LIVE_WATCH));            // the event completed under the viewer
    expect(beacons().at(-1)).toMatchObject({ ended: true, rebuffer_count: 0, rebuffer_ms: 0,
                                             playback_ms: 10_000 });
  });

  it("does not count a stall the stream's end explains: the room gone for good", () => {
    const { container, again, unmount } = show();
    fireEvent.playing(liveVideo(container));
    tick(10_000);
    lk.reconnecting = true;
    again();
    tick(20_000);
    Object.assign(lk, { reconnecting: false, connected: false, stopped: true });   // fatal disconnect
    again();
    tick(60_000);
    unmount();
    expect(beacons().at(-1)).toMatchObject({ ended: true, rebuffer_count: 0, playback_ms: 10_000 });
  });

  it("caps a stall nobody is watching any more (a tab left on 'Reconnecting…')", () => {
    const { container, again, unmount } = show();
    fireEvent.playing(liveVideo(container));
    tick(10_000);
    lk.reconnecting = true;
    again();
    tick(3_600_000);
    unmount();
    const last = beacons().at(-1);
    expect(last).toMatchObject({ rebuffer_count: 1, rebuffer_ms: MAX_STALL_MS, playback_ms: 10_000 + MAX_STALL_MS });
    expect(last.rebuffer_ms).toBeLessThanOrEqual(last.playback_ms);
  });

  it("flushes on a request that outlives the page when the tab is hidden", () => {
    let visibility = "visible";
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
    try {
      const { container } = show();
      fireEvent.playing(liveVideo(container));
      tick(10_000);
      act(() => {
        visibility = "hidden";
        document.dispatchEvent(new Event("visibilitychange"));
      });
      const [, init] = fetch.mock.calls.at(-1);
      expect(init.keepalive).toBe(true);
      expect(JSON.parse(init.body)).toMatchObject({ playback_ms: 10_000, rebuffer_count: 0 });
    } finally {
      delete document.visibilityState;
    }
  });

  it("does not count the broadcaster's media going away, nor a user pause", () => {
    const { container, again, unmount } = show();
    fireEvent.playing(liveVideo(container));
    tick(10_000);
    lk.hasVideo = false;               // the host stopped publishing
    lk.hasAudio = false;
    again();
    tick(60_000);
    lk.hasVideo = true;
    lk.hasAudio = true;
    again();
    tick(5_000);
    fireEvent.click(screen.getAllByRole("button", { name: "Pause" })[0]);
    tick(120_000);
    unmount();
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 0, playback_ms: 15_000 });
  });

  it("reports an autoplay refusal as its own reason", async () => {
    HTMLMediaElement.prototype.play = () => Promise.reject(Object.assign(new Error("blocked"), { name: "NotAllowedError" }));
    show();
    await act(async () => { await Promise.resolve(); });
    tick(1_000);
    expect(beacons()).toEqual([expect.objectContaining({ started: false, failures: ["autoplay_blocked"] })]);
  });

  it("hears LiveKit's own failures through the hook", () => {
    show();
    act(() => hookArgs.onFailure("track_subscription_failed"));
    tick(1_000);
    expect(beacons().at(-1).failures).toEqual(["track_subscription_failed"]);
  });

  it("is never measured for a viewer without a QoE token (staff)", () => {
    const { container, unmount } = show(LIVE_EVENT, { ...LIVE_WATCH, qoe_token: null });
    fireEvent.playing(liveVideo(container));
    tick(120_000);
    act(() => hookArgs.onFailure("timeout"));
    tick(2_000);
    unmount();
    expect(fetch).not.toHaveBeenCalled();
  });
});

describe("replay playback quality", () => {
  it("measures the file player: buffering mid-play counts, the seek and the end do not", () => {
    const { container } = show(ENDED_EVENT, REPLAY_WATCH);
    fireEvent.click(screen.getByRole("button", { name: /replay/i }));
    const video = container.querySelector("video");
    tick(600);
    fireEvent.playing(video);
    expect(beacons()).toEqual([expect.objectContaining({ token: REPLAY_WATCH.qoe_token, started: true,
                                                         startup_ms: 600 })]);
    tick(20_000);
    fireEvent.waiting(video);          // buffering mid-play
    tick(2_500);
    fireEvent.playing(video);
    tick(20_000);
    fireEvent.seeking(video);          // the viewer jumped ahead
    fireEvent.waiting(video);
    tick(4_000);
    fireEvent.seeked(video);
    fireEvent.playing(video);
    tick(10_000);
    fireEvent.ended(video);
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 1, rebuffer_ms: 2_500, playback_ms: 52_500,
                                             ended: true });
  });

  function startReplay({ startupMs = 600 } = {}) {
    const utils = show(ENDED_EVENT, REPLAY_WATCH);
    fireEvent.click(screen.getByRole("button", { name: /replay/i }));
    const video = utils.container.querySelector("video");
    fireEvent.play(video);
    fireEvent.waiting(video);          // initial buffering
    tick(startupMs);
    fireEvent.playing(video);
    return { ...utils, video };
  }

  it("does not count the initial buffering, only a stall while playing and not paused", () => {
    const { video, unmount } = startReplay({ startupMs: 5_000 });
    tick(10_000);
    fireEvent.waiting(video);          // the network could not keep up
    tick(3_000);
    fireEvent.playing(video);
    tick(10_000);
    unmount();
    expect(beacons().at(-1)).toMatchObject({ startup_ms: 5_000, rebuffer_count: 1, rebuffer_ms: 3_000,
                                             playback_ms: 23_000 });
  });

  it("counts a pause from the player's own button as a pause, and the resume as no stall", () => {
    const { video, unmount } = startReplay();
    tick(10_000);
    fireEvent.click(screen.getByRole("button", { name: "Pause" }));
    fireEvent.pause(video);            // what the element then fires in a browser
    tick(60_000);
    fireEvent.click(screen.getAllByRole("button", { name: "Play" })[0]);
    fireEvent.play(video);
    fireEvent.waiting(video);          // resuming
    tick(700);
    fireEvent.playing(video);
    tick(10_000);
    unmount();
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 0, rebuffer_ms: 0, playback_ms: 20_000 });
  });

  // The regression: a pause that did not come from the page's button only cleared `flowing`
  // while `playing` stayed on, so the whole pause was reported as rebuffering.
  it("counts a pause from outside the page (media keys, OS media session, headphones) as a pause", () => {
    const { video, unmount } = startReplay();
    tick(10_000);
    fireEvent.pause(video);            // no click: the browser paused the element
    expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();          // the button follows
    expect(screen.getAllByRole("button", { name: "Play" }).length).toBeGreaterThan(0);
    tick(120_000);
    fireEvent.play(video);             // resumed the same way
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    tick(300);
    fireEvent.playing(video);
    tick(10_000);
    unmount();
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 0, rebuffer_ms: 0, playback_ms: 20_000 });
  });

  it("counts Picture-in-Picture and native-fullscreen control pauses as pauses too", () => {
    const { video, unmount } = startReplay();
    tick(10_000);
    fireEvent(video, new Event("enterpictureinpicture"));
    fireEvent.pause(video);            // the PiP window's pause button
    tick(30_000);
    fireEvent.play(video);
    fireEvent.playing(video);
    tick(5_000);
    fireEvent(video, new Event("leavepictureinpicture"));
    fireEvent(video, new Event("webkitbeginfullscreen"));
    fireEvent.pause(video);            // iOS's native fullscreen controls
    tick(30_000);
    fireEvent.play(video);
    fireEvent.playing(video);
    tick(5_000);
    fireEvent(video, new Event("webkitendfullscreen"));
    unmount();
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 0, rebuffer_ms: 0, playback_ms: 20_000 });
  });

  it("closes a stall at the viewer's pause, and does not count the end that pauses the element", () => {
    const { video, unmount } = startReplay();
    tick(10_000);
    fireEvent.waiting(video);
    tick(2_000);
    fireEvent.pause(video);            // paused while buffering: 2 s of stall, no more
    tick(60_000);
    fireEvent.play(video);
    fireEvent.playing(video);
    tick(10_000);
    Object.defineProperty(video, "ended", { value: true, configurable: true });
    fireEvent.pause(video);            // reaching the end pauses the element first...
    fireEvent.ended(video);            // ...then ends it
    unmount();
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 1, rebuffer_ms: 2_000, playback_ms: 22_000,
                                             ended: true });
  });

  it("caps a replay stall the viewer leaves in", () => {
    const { video, unmount } = startReplay();
    tick(10_000);
    fireEvent.waiting(video);
    tick(30 * 60_000);
    unmount();
    expect(beacons().at(-1)).toMatchObject({ rebuffer_count: 1, rebuffer_ms: MAX_STALL_MS,
                                             playback_ms: 10_000 + MAX_STALL_MS });
  });

  it("reports a media error by its normalized reason", () => {
    const { container } = show(ENDED_EVENT, REPLAY_WATCH);
    fireEvent.click(screen.getByRole("button", { name: /replay/i }));
    const video = container.querySelector("video");
    Object.defineProperty(video, "error", { value: { code: 4 }, configurable: true });
    fireEvent.error(video);
    tick(1_000);
    expect(beacons().at(-1).failures).toEqual(["stream_unavailable"]);
  });
});
