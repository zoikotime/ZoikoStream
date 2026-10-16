// usePlaybackQoe: one playback session's beacons to POST /api/playback/qoe — when they go out,
// what they carry, and what never causes one (re-renders, a fresh token from a poll, a viewer
// with no token at all).
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import usePlaybackQoe, { FLUSH_EVERY_MS, QOE_URL } from "./usePlaybackQoe";

let clock = 0;
const tick = (ms) => {
  clock += ms;
  vi.advanceTimersByTime(ms);
};

beforeEach(() => {
  clock = 0;
  vi.spyOn(performance, "now").mockImplementation(() => clock);
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
  globalThis.fetch = vi.fn(() => Promise.resolve({ ok: true, status: 204 }));
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const TOKEN = "qoe-token-for-session-one";
const base = { token: TOKEN, active: true, intent: true, visible: true, flowing: false };
const beacons = () => fetch.mock.calls.map(([url, init]) => ({ url, init, body: JSON.parse(init.body) }));
const flushPromises = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

function mount(props = {}) {
  return renderHook((p) => usePlaybackQoe(p), { initialProps: { ...base, ...props } });
}

describe("a playback session's beacons", () => {
  it("say nothing until playback starts, then report the start exactly once", () => {
    const { rerender } = mount();
    tick(1_200);
    rerender({ ...base });
    expect(fetch).not.toHaveBeenCalled();
    rerender({ ...base, flowing: true });
    rerender({ ...base, flowing: true });                 // re-renders add nothing
    rerender({ ...base, flowing: true });
    expect(beacons()).toHaveLength(1);
    const [{ url, init, body }] = beacons();
    expect(url).toBe(QOE_URL);
    expect(init).toMatchObject({ method: "POST", credentials: "omit", keepalive: false,
                                 headers: { "Content-Type": "text/plain;charset=UTF-8" } });
    expect(body).toMatchObject({ token: TOKEN, seq: 1, started: true, startup_ms: 1_200, playback_ms: 0 });
  });

  it("keep the token the session began with, while totals move on a timer", () => {
    const { rerender } = mount({ flowing: true });
    // The watch page re-polled and got a fresh token: not a new session.
    rerender({ ...base, flowing: true, token: "a-newer-token-from-a-poll" });
    tick(FLUSH_EVERY_MS);
    const last = beacons().at(-1).body;
    expect(last).toMatchObject({ token: TOKEN, seq: 2, playback_ms: FLUSH_EVERY_MS });
  });

  it("stay quiet while nothing changes", () => {
    const { rerender } = mount({ flowing: true });
    tick(10_000);
    rerender({ ...base, flowing: true, intent: false });  // paused
    tick(FLUSH_EVERY_MS);
    expect(beacons().at(-1).body).toMatchObject({ seq: 2, playback_ms: 10_000 });
    tick(FLUSH_EVERY_MS);
    tick(FLUSH_EVERY_MS);
    expect(beacons()).toHaveLength(2);
  });

  it("carry failures once each, batched", () => {
    const { result } = mount();
    act(() => {
      result.current.fail("autoplay_blocked");
      result.current.fail("autoplay_blocked");
      result.current.fail("network_error");
    });
    expect(fetch).not.toHaveBeenCalled();
    act(() => tick(1_000));
    expect(beacons()).toHaveLength(1);
    expect(beacons()[0].body).toMatchObject({ started: false, playback_ms: 0,
                                              failures: ["autoplay_blocked", "network_error"] });
  });

  it("end with a keepalive beacon when the player goes, counting the stall the viewer left in", () => {
    const { rerender, unmount } = mount({ flowing: true });
    tick(10_000);
    rerender({ ...base, flowing: false });
    tick(5_000);
    unmount();
    const last = beacons().at(-1);
    expect(last.init.keepalive).toBe(true);
    expect(last.body).toMatchObject({ ended: true, rebuffer_count: 1, rebuffer_ms: 5_000, playback_ms: 15_000 });
  });

  it("end when the player stops being active (live -> ended), and on pagehide", () => {
    const first = mount({ flowing: true });
    tick(3_000);
    first.rerender({ ...base, flowing: true, active: false });
    expect(beacons().at(-1).body).toMatchObject({ ended: true, playback_ms: 3_000 });

    fetch.mockClear();
    mount({ flowing: true, token: "another-session-token" });
    tick(2_000);
    act(() => window.dispatchEvent(new Event("pagehide")));
    expect(beacons().at(-1)).toMatchObject({ init: { keepalive: true },
                                             body: { token: "another-session-token", ended: true } });
  });

  it("measure one session per token: a re-activation on a reported token is not a second run", () => {
    const { rerender } = mount({ flowing: true });
    tick(5_000);
    rerender({ ...base, flowing: true, active: false });      // ended: final beacon
    const reported = beacons().length;
    rerender({ ...base, flowing: true, active: true });       // same token again
    tick(FLUSH_EVERY_MS);
    rerender({ ...base, flowing: true, active: false });
    expect(beacons()).toHaveLength(reported);
    // A fresh token (a new session from the server) is measured as usual.
    rerender({ ...base, flowing: true, active: true, token: "a-fresh-session-token" });
    expect(beacons().at(-1)).toMatchObject({ body: { token: "a-fresh-session-token", seq: 1, started: true } });
  });

  it("never exist for a viewer without a token (staff, a monitor feed)", () => {
    const { result, rerender, unmount } = mount({ token: null, flowing: true });
    act(() => result.current.fail("network_error"));
    rerender({ ...base, token: null, flowing: false });
    tick(FLUSH_EVERY_MS * 2);
    unmount();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("close on the facts of the commit that ended them: the stream ending mid-stall is not a stall", () => {
    const { rerender } = mount({ flowing: true });
    tick(10_000);
    rerender({ ...base, flowing: false });                  // reconnecting
    tick(30_000);
    // The event completed under the viewer: the player goes inactive AND ended in one render.
    rerender({ ...base, flowing: false, active: false, ended: true });
    expect(beacons().at(-1)).toMatchObject({ init: { keepalive: true },
                                             body: { ended: true, rebuffer_count: 0, rebuffer_ms: 0,
                                                     playback_ms: 10_000 } });
  });

  it("stop once the server refuses the session, but survive a network failure", async () => {
    fetch.mockImplementationOnce(() => Promise.reject(new TypeError("offline")));
    const { rerender } = mount({ flowing: true });          // start beacon: lost
    await flushPromises();
    rerender({ ...base, flowing: true, intent: false });
    tick(FLUSH_EVERY_MS);                                   // retried with the same totals
    expect(beacons().map((b) => b.body.seq)).toEqual([1, 2]);

    fetch.mockImplementation(() => Promise.resolve({ ok: false, status: 401 }));
    rerender({ ...base, flowing: true });
    tick(FLUSH_EVERY_MS);
    await flushPromises();
    const count = beacons().length;
    rerender({ ...base, flowing: true, intent: false });
    tick(FLUSH_EVERY_MS * 3);
    expect(beacons()).toHaveLength(count);
  });

  it("carry exactly the beacon contract, well under the server's 4 KB", () => {
    const { result, unmount } = mount({ flowing: true, token: "t".repeat(512) });
    act(() => {
      ["media_connection_failed", "track_subscription_failed", "playback_start_failed", "decoder_error",
       "network_error", "autoplay_blocked", "stream_unavailable", "access_denied", "timeout", "unknown"]
        .forEach((reason) => result.current.fail(reason));
    });
    tick(1_000);
    unmount();
    for (const { init, body } of beacons()) {
      expect(Object.keys(body).sort()).toEqual(["ended", "failures", "playback_ms", "rebuffer_count",
                                                "rebuffer_ms", "seq", "started", "startup_ms", "token"]);
      expect(new TextEncoder().encode(init.body).length).toBeLessThan(4_096);
    }
  });
});

describe("the beacons a page may not outlive", () => {
  let visibility = "visible";
  beforeEach(() => {
    visibility = "visible";
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => visibility });
  });
  afterEach(() => {
    delete document.visibilityState;
    delete navigator.sendBeacon;
    vi.unstubAllGlobals();
  });
  const hide = () => act(() => {
    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
  });

  it("go out with keepalive when the tab is hidden, counting the stall the viewer sat through", () => {
    const { rerender } = mount({ flowing: true });
    tick(FLUSH_EVERY_MS);
    expect(beacons().at(-1).init.keepalive).toBe(false);    // routine flushes are ordinary requests
    rerender({ ...base, flowing: false });
    tick(4_000);
    hide();                                                 // before React has rendered it
    const last = beacons().at(-1);
    expect(last.init).toMatchObject({ keepalive: true, credentials: "omit",
                                      headers: { "Content-Type": "text/plain;charset=UTF-8" } });
    expect(last.body).toMatchObject({ ended: false, rebuffer_count: 1, rebuffer_ms: 4_000,
                                      playback_ms: FLUSH_EVERY_MS + 4_000 });
    // React catches up (the player reports the tab hidden): nothing new to say, then or later.
    const count = beacons().length;
    rerender({ ...base, flowing: false, visible: false });
    tick(FLUSH_EVERY_MS * 2);
    expect(beacons()).toHaveLength(count);
  });

  it("fall back to sendBeacon where fetch has no real keepalive", async () => {
    vi.stubGlobal("Request", class {});                     // no Request#keepalive: Firefox < 133
    navigator.sendBeacon = vi.fn(() => true);
    const { unmount } = mount({ flowing: true });
    expect(fetch).toHaveBeenCalledTimes(1);                 // the start: an ordinary request
    tick(5_000);
    hide();
    unmount();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(navigator.sendBeacon).toHaveBeenCalledTimes(2);
    const [url, blob] = navigator.sendBeacon.mock.calls.at(-1);
    expect(url).toBe(QOE_URL);
    expect(blob.type).toBe("text/plain;charset=utf-8");
    const body = JSON.parse(await new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.readAsText(blob);
    }));
    expect(body).toMatchObject({ token: TOKEN, seq: 3, ended: true, playback_ms: 5_000 });
  });

  it("fall back to sendBeacon when fetch refuses a keepalive request outright", () => {
    navigator.sendBeacon = vi.fn(() => true);
    const { unmount } = mount({ flowing: true });
    fetch.mockImplementation(() => { throw new TypeError("keepalive refused"); });
    tick(5_000);
    unmount();
    expect(navigator.sendBeacon).toHaveBeenCalledTimes(1);
  });
});
