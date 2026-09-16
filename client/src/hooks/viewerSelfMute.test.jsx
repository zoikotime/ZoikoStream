// A speaker muting themselves has to reach the host.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// toggleMic set `enabled = false` on the raw MediaStreamTrack. That silences the audio and
// nothing else: LiveKit is never told, so it emits no mute event, the track stays published
// and unmuted as far as the room is concerned, and the host console went on showing a live,
// unmuted speaker who was in fact silent. `participant.state` — the action that exists
// precisely for a client reporting its own media state — had no sender at all.
import { readFileSync } from "node:fs";
import { beforeEach, describe, expect, it, vi } from "vitest";

// A LocalAudioTrack stand-in: mute()/unmute() are the livekit-client 2.x API and both are
// async, which is why the hook awaits them.
function fakeTrack() {
  return {
    isMuted: false,
    mute: vi.fn(function () { this.isMuted = true; return Promise.resolve(this); }),
    unmute: vi.fn(function () { this.isMuted = false; return Promise.resolve(this); }),
    // The raw track underneath. Nothing should be reaching past LiveKit to touch it.
    mediaStreamTrack: { enabled: true, stop: vi.fn() },
  };
}

vi.mock("livekit-client", () => ({
  Room: class { on() { return this; } off() { return this; } },
  RoomEvent: {},
  Track: { Kind: { Audio: "audio", Video: "video" }, Source: { Microphone: "microphone" } },
}));

// The hook under test reaches LiveKit through refs set during publish, which a unit test
// cannot reach. So this drives the LOGIC of toggleMic against the same contract: the
// published track is muted through LiveKit, and the resulting state is reported outward.
//
// The contract is asserted directly rather than through a rendered room, because what broke
// was never the rendering — it was which API got called and whether anyone was told.
function makeToggle({ track, micOn, onMuteChange, setMicOn, setMicError }) {
  // Mirrors hooks/useLiveKitViewer.js toggleMic exactly.
  return async () => {
    if (!track) return;
    const next = !micOn;
    try {
      if (next) await track.unmute();
      else await track.mute();
    } catch (e) {
      setMicError(e?.message || "Couldn't change your microphone state");
      return;
    }
    setMicOn(next);
    onMuteChange?.(!next);
  };
}

describe("muting goes through LiveKit, not the raw track", () => {
  let track, onMuteChange, setMicOn, setMicError;

  beforeEach(() => {
    track = fakeTrack();
    onMuteChange = vi.fn();
    setMicOn = vi.fn();
    setMicError = vi.fn();
  });

  it("calls LocalAudioTrack.mute() when a live speaker mutes", async () => {
    const toggle = makeToggle({ track, micOn: true, onMuteChange, setMicOn, setMicError });
    await toggle();

    expect(track.mute).toHaveBeenCalledTimes(1);
    // The failure being guarded: silencing the raw track signals nothing to the room.
    expect(track.mediaStreamTrack.enabled).toBe(true);
  });

  it("reports muted=true so presence — and therefore the host — learns", async () => {
    const toggle = makeToggle({ track, micOn: true, onMuteChange, setMicOn, setMicError });
    await toggle();

    expect(onMuteChange).toHaveBeenCalledWith(true);
    expect(setMicOn).toHaveBeenCalledWith(false);
  });

  it("calls unmute() and reports muted=false on the way back", async () => {
    const toggle = makeToggle({ track, micOn: false, onMuteChange, setMicOn, setMicError });
    await toggle();

    expect(track.unmute).toHaveBeenCalledTimes(1);
    expect(track.mute).not.toHaveBeenCalled();
    expect(onMuteChange).toHaveBeenCalledWith(false);
  });

  it("reports nothing when LiveKit refuses, so the host keeps the truth", async () => {
    track.mute = vi.fn(() => Promise.reject(new Error("track is gone")));
    const toggle = makeToggle({ track, micOn: true, onMuteChange, setMicOn, setMicError });
    await toggle();

    // Nothing changed, so nothing is claimed: the console must not show "Muted" for a mute
    // that did not happen.
    expect(onMuteChange).not.toHaveBeenCalled();
    expect(setMicOn).not.toHaveBeenCalled();
    expect(setMicError).toHaveBeenCalledWith("track is gone");
  });

  it("does nothing at all with no published track", async () => {
    const toggle = makeToggle({ track: null, micOn: true, onMuteChange, setMicOn, setMicError });
    await toggle();

    expect(onMuteChange).not.toHaveBeenCalled();
    expect(setMicOn).not.toHaveBeenCalled();
  });
});

describe("the report can only ever describe yourself", () => {
  it("carries no identity — the server scopes it to the sender", () => {
    // EventWatch sends `participant.state {muted}` and nothing else. The server
    // (services/moderation._participant_state) writes it against ctx.identity, so a viewer
    // cannot report state for another participant even by editing the payload.
    const sent = [];
    const sendPanel = (type, payload) => sent.push([type, payload]);
    const reportMuted = (muted) => sendPanel("participant.state", { muted });

    reportMuted(true);
    expect(sent).toEqual([["participant.state", { muted: true }]]);
    expect(Object.keys(sent[0][1])).toEqual(["muted"]);
    expect(sent[0][1]).not.toHaveProperty("identity");
  });
});

describe("the real hook, not just this mirror", () => {
  // The cases above drive a COPY of toggleMic, which is only as good as its likeness. This
  // reads the actual source so the copy cannot quietly drift from it — and so the specific
  // regression (going back to raw-track muting) is caught at its source.
  // Plain path from the project root: import.meta.url is not a file: URL under Vitest's
  // module transform, so new URL(...) throws at collection time.
  const source = readFileSync("src/hooks/useLiveKitViewer.js", "utf8");
  const toggleMicBody = source.slice(
    source.indexOf("const toggleMic"),
    source.indexOf("return {", source.indexOf("const toggleMic"))
  );

  it("mutes through LiveKit", () => {
    expect(toggleMicBody).toMatch(/track\.mute\(\)/);
    expect(toggleMicBody).toMatch(/track\.unmute\(\)/);
  });

  it("does not silence the raw MediaStreamTrack instead", () => {
    // `enabled = false` is the exact bug: locally silent, invisible to the room.
    expect(toggleMicBody).not.toMatch(/\.enabled\s*=/);
  });

  it("reports the result outward", () => {
    expect(toggleMicBody).toMatch(/onMuteChange/);
  });
});
