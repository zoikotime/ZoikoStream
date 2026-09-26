import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

// Cover for the viewer media-quality defects found in the audio/video audit:
//
//   * LAG: the viewer built a bare `new Room()`, and livekit-client's roomOptionDefaults are
//     `adaptiveStream: false, dynacast: false`. With adaptive stream off and nothing ever
//     calling setVideoQuality, RemoteTrackPublication.emitTrackUpdate() fell through to its
//     last branch and sent `quality: HIGH` forever, so every viewer was pinned to the top
//     simulcast layer (1920x1080 @ 3 Mbps) no matter how small the player or how poor the
//     connection.
//   * NO SELECTOR: the quality menu existed but every entry except "Auto" was hardcoded and
//     `disabled`, and setVideoQuality() was never called anywhere in the codebase.
//   * LOST AUDIO: every subscribed track was attached to the SAME <video> element on the
//     belief that attach() mixes. livekit's attachToElement() does the opposite — it removes
//     every existing same-kind track from the element before adding the new one — so a
//     promoted speaker's mic silently erased the host's, and screen-share audio silently
//     erased the mic. The effect also re-attached everything on each track event, churning
//     remove/add against a live srcObject.
//
// The eviction semantics are MODELLED in the fake below rather than spied on, because a plain
// vi.fn() attach() would pass just as happily with the bug present.

const RoomEvent = {
  Reconnecting: "reconnecting",
  Reconnected: "reconnected",
  Disconnected: "disconnected",
  LocalTrackPublished: "localTrackPublished",
  LocalTrackUnpublished: "localTrackUnpublished",
  TrackSubscribed: "trackSubscribed",
  TrackUnsubscribed: "trackUnsubscribed",
  TrackPublished: "trackPublished",
  ParticipantConnected: "participantConnected",
  ParticipantDisconnected: "participantDisconnected",
};
const Track = {
  Kind: { Video: "video", Audio: "audio" },
  Source: {
    Camera: "camera",
    Microphone: "microphone",
    ScreenShare: "screen_share",
    ScreenShareAudio: "screen_share_audio",
  },
};
const DisconnectReason = { CLIENT_INITIATED: 1, DUPLICATE_IDENTITY: 2, ROOM_DELETED: 5 };
// livekit-client declares its own three-value VideoQuality (the protocol's has a fourth,
// OFF). These are the values the SDK actually emits, so a drift between what the hook sends
// and what the SDK expects fails here rather than only in production.
const VideoQuality = { LOW: 0, MEDIUM: 1, HIGH: 2 };
const VideoPresets = {
  h180: { width: 320, height: 180, encoding: { maxBitrate: 160000, maxFramerate: 20 } },
  h360: { width: 640, height: 360, encoding: { maxBitrate: 450000, maxFramerate: 20 } },
  h720: { width: 1280, height: 720, encoding: { maxBitrate: 1700000, maxFramerate: 30 } },
  h1080: { width: 1920, height: 1080, encoding: { maxBitrate: 3000000, maxFramerate: 30 } },
};

let rooms = [];

class Emitter {
  constructor() {
    this.handlers = new Map();
  }

  on(event, fn) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event).add(fn);
    return this;
  }

  removeAllListeners() {
    this.handlers.clear();
    return this;
  }

  emit(event, ...args) {
    [...(this.handlers.get(event) || [])].forEach((fn) => fn(...args));
  }
}

class FakeRoom extends Emitter {
  constructor(options) {
    super();
    this.options = options || {};
    this.name = "event_70312f66-5acc-4095-a977-41e27ea31288";
    this.state = "disconnected";
    this.localParticipant = {
      identity: "viewer::1",
      trackPublications: new Map(),
      videoTrackPublications: new Map(),
      audioTrackPublications: new Map(),
      publishTrack: vi.fn(async () => ({ track: {} })),
      unpublishTrack: vi.fn(async () => null),
    };
    this.remoteParticipants = new Map();
    this.connectCalls = [];
    this.disconnectCalls = [];
    rooms.push(this);
  }

  async connect(url, token) {
    this.connectCalls.push({ url, token });
    this.state = "connected";
  }

  async disconnect(stopTracks) {
    this.disconnectCalls.push({ stopTracks });
    this.state = "disconnected";
  }
}

vi.mock("livekit-client", () => ({
  Room: class {
    constructor(options) {
      return new FakeRoom(options);
    }
  },
  RoomEvent,
  Track,
  DisconnectReason,
  VideoQuality,
  VideoPresets,
}));

const { default: useLiveKitViewer } = await import("./useLiveKitViewer");
const { default: useLiveKitPublish } = await import("./useLiveKitPublish");

// ── faithful attach/detach ────────────────────────────────────────────────────
// Mirrors livekit-client's attachToElement(): an element's stream holds at most one track of
// each kind, and attaching a second of the same kind EVICTS the first.
const makeTrack = (kind, source) => {
  const track = {
    kind,
    source,
    attachedTo: new Set(),
    // `__mutations` counts real changes to the element's track set. That, not the call count,
    // is what "churn" means: re-calling attach() for a track already on the element is a
    // deliberate no-op in livekit (its own comment notes srcObject may have been reset out of
    // band, so it re-attaches defensively), and it neither interrupts playback nor clicks.
    attach: vi.fn((el) => {
      if (!el) throw new Error("this suite always attaches to an explicit element");
      if (!el.__tracks) el.__tracks = [];
      if (el.__tracks.includes(track)) return el;
      el.__tracks
        .filter((t) => t.kind === kind)
        .forEach((t) => {
          el.__tracks.splice(el.__tracks.indexOf(t), 1);
          t.attachedTo.delete(el);
          el.__mutations = (el.__mutations || 0) + 1;
        });
      el.__tracks.push(track);
      el.__mutations = (el.__mutations || 0) + 1;
      track.attachedTo.add(el);
      return el;
    }),
    detach: vi.fn((el) => {
      const targets = el ? [el] : [...track.attachedTo];
      targets.forEach((target) => {
        if (target.__tracks) {
          const at = target.__tracks.indexOf(track);
          if (at >= 0) {
            target.__tracks.splice(at, 1);
            target.__mutations = (target.__mutations || 0) + 1;
          }
        }
        track.attachedTo.delete(target);
      });
    }),
  };
  return track;
};

// The ladder the publisher now sends: 360p / 720p / 1080p.
const LADDER = [
  { quality: VideoQuality.LOW, width: 640, height: 360 },
  { quality: VideoQuality.MEDIUM, width: 1280, height: 720 },
  { quality: VideoQuality.HIGH, width: 1920, height: 1080 },
];

const pubFor = (track, layers) => ({
  track,
  kind: track.kind,
  source: track.source,
  isSubscribed: true,
  trackInfo: layers ? { layers } : undefined,
  setVideoQuality: vi.fn(),
});

const URL_ = "wss://zoikostream-test.livekit.cloud";
const TOKEN = "fake.viewer.token";

const viewerHook = (overrides = {}) => renderHook((props) => useLiveKitViewer({
  enabled: true, url: URL_, token: TOKEN, ...overrides, ...props,
}), { initialProps: {} });

const attachElement = (result) => {
  const el = document.createElement("video");
  act(() => { result.current.mediaRef(el); });
  return el;
};

const audioEls = () => [...document.querySelectorAll("audio")];

const subscribe = async (room, ...pubs) => {
  await act(async () => {
    pubs.forEach((pub) => room.emit(RoomEvent.TrackSubscribed, pub.track, pub));
  });
};

beforeEach(() => {
  rooms = [];
});

afterEach(() => {
  vi.restoreAllMocks();
  document.querySelectorAll("audio").forEach((el) => el.remove());
});

// ── Phase 2/3: room configuration ─────────────────────────────────────────────

describe("viewer room configuration", () => {
  it("enables adaptive stream, at the screen's real pixel density", async () => {
    // THE LAG BUG: a bare `new Room()` inherits adaptiveStream:false, which is what left
    // every viewer requesting VideoQuality.HIGH forever.
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    const { adaptiveStream } = rooms[0].options;
    expect(adaptiveStream).toBeTruthy();
    // pixelDensity 'screen' is what keeps 1080p reachable: livekit's own default is 1 for any
    // devicePixelRatio <= 2, which would cap an ordinary 2x laptop at the 720p layer.
    expect(adaptiveStream.pixelDensity).toBe("screen");
  });

  it("does not pin the subscription to a fixed quality by default", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    // "auto" means adaptive stream decides. The selector itself (describeLayers /
    // applyQuality) is exercised by videoQuality.test.jsx; what matters here is only that
    // the room does not start out capped.
    expect(result.current.quality).toBe("auto");
  });
});

describe("publisher room configuration", () => {
  const publishHook = () => {
    const v = { kind: "video", enabled: true, readyState: "live", stop: vi.fn() };
    const a = { kind: "audio", enabled: true, readyState: "live", stop: vi.fn() };
    const streamRef = {
      current: { getVideoTracks: () => [v], getAudioTracks: () => [a], getTracks: () => [v, a] },
    };
    return renderHook(() => useLiveKitPublish({
      enabled: true, url: URL_, token: TOKEN, streamRef,
      screenTrack: null, screenAudioTrack: null, videoTrack: v,
    }));
  };

  it("enables dynacast so unwatched simulcast layers stop being encoded", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(rooms[0].options.dynacast).toBe(true);
  });

  it("still refuses to let LiveKit stop the preview's own tracks", async () => {
    // Pre-existing and load-bearing: useMediaPreview owns those tracks.
    const { result } = publishHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(rooms[0].options.stopLocalTrackOnUnpublish).toBe(false);
  });

  it("publishes a 360p/720p/1080p ladder instead of livekit's 180p/360p default", async () => {
    // The default [h180, h360] left a 360p -> 1080p cliff and no layer that could honestly be
    // labelled 720p in the viewer's menu.
    const { result } = publishHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    const layers = rooms[0].options.publishDefaults.videoSimulcastLayers;
    expect(layers.map((l) => l.height)).toEqual([360, 720]);
  });

  it("overrides only the ladder, leaving livekit's other publish defaults alone", async () => {
    // The Room constructor merges publishDefaults over the SDK's own, so codec/simulcast/
    // audioPreset must NOT appear here — naming them would be how audio encoding got changed
    // by accident.
    const { result } = publishHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(Object.keys(rooms[0].options.publishDefaults)).toEqual(["videoSimulcastLayers"]);
  });
});

// ── Phase 4: audio track handling ─────────────────────────────────────────────

describe("remote audio", () => {
  it("gives every audio track its own element instead of one shared surface", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);

    const hostMic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    const speakerMic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    await subscribe(rooms[0], pubFor(hostMic), pubFor(speakerMic));

    await waitFor(() => expect(audioEls()).toHaveLength(2));
    // Each element carries exactly one audio track, and both tracks are still live.
    expect(hostMic.attachedTo.size).toBe(1);
    expect(speakerMic.attachedTo.size).toBe(1);
    expect([...hostMic.attachedTo][0]).not.toBe([...speakerMic.attachedTo][0]);
  });

  it("keeps the host audible when a promoted speaker starts talking", async () => {
    // THE LOST-AUDIO BUG, stated directly: with both mics on one element the second attach
    // evicted the first and the host went silent with nothing reporting it.
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);

    const hostMic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    await subscribe(rooms[0], pubFor(hostMic));
    await waitFor(() => expect(hostMic.attachedTo.size).toBe(1));

    const speakerMic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    await subscribe(rooms[0], pubFor(speakerMic));
    await waitFor(() => expect(speakerMic.attachedTo.size).toBe(1));

    expect(hostMic.attachedTo.size).toBe(1);   // still attached, still audible
    expect(result.current.hasAudio).toBe(true);
  });

  it("keeps the microphone audible when screen-share audio arrives", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);

    const mic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    const shareAudio = makeTrack(Track.Kind.Audio, Track.Source.ScreenShareAudio);
    await subscribe(rooms[0], pubFor(mic), pubFor(shareAudio));

    await waitFor(() => expect(audioEls()).toHaveLength(2));
    expect(mic.attachedTo.size).toBe(1);
    expect(shareAudio.attachedTo.size).toBe(1);
  });

  it("never routes audio through the video element", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    const videoEl = attachElement(result);

    const mic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    const cam = makeTrack(Track.Kind.Video, Track.Source.Camera);
    await subscribe(rooms[0], pubFor(mic), pubFor(cam, LADDER));

    await waitFor(() => expect(cam.attachedTo.has(videoEl)).toBe(true));
    expect(mic.attach).not.toHaveBeenCalledWith(videoEl);
    expect(mic.attachedTo.has(videoEl)).toBe(false);
    // The video element holds video and nothing else.
    expect(videoEl.__tracks.map((t) => t.kind)).toEqual([Track.Kind.Video]);
  });

  it("does not re-attach a live audio track when an unrelated track changes", async () => {
    // THE CHURN: the old effect re-attached every track on each bump, so two audio tracks
    // produced remove/add/remove/add on a live srcObject — audible clicks and dropouts.
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);

    const mic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    await subscribe(rooms[0], pubFor(mic));
    await waitFor(() => expect(mic.attach).toHaveBeenCalledTimes(1));

    // Three further unrelated subscriptions, each of which bumps the track set.
    await subscribe(rooms[0], pubFor(makeTrack(Track.Kind.Video, Track.Source.Camera), LADDER));
    await subscribe(rooms[0], pubFor(makeTrack(Track.Kind.Audio, Track.Source.Microphone)));
    await subscribe(rooms[0], pubFor(makeTrack(Track.Kind.Audio, Track.Source.Microphone)));

    expect(mic.attach).toHaveBeenCalledTimes(1);   // untouched throughout
    expect(mic.detach).not.toHaveBeenCalled();
  });

  it("releases an audio element when its track goes away", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);

    const mic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    const pub = pubFor(mic);
    await subscribe(rooms[0], pub);
    await waitFor(() => expect(audioEls()).toHaveLength(1));

    await act(async () => { rooms[0].emit(RoomEvent.TrackUnsubscribed, mic, pub); });
    await waitFor(() => expect(audioEls()).toHaveLength(0));
    expect(result.current.hasAudio).toBe(false);
  });

  it("leaves nothing playing in the document after unmount", async () => {
    const { result, unmount } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);
    await subscribe(rooms[0], pubFor(makeTrack(Track.Kind.Audio, Track.Source.Microphone)));
    await waitFor(() => expect(audioEls()).toHaveLength(1));

    unmount();
    expect(audioEls()).toHaveLength(0);
  });

  it("creates no duplicate audio elements under StrictMode's double-invoke", async () => {
    // main.jsx wraps the app in StrictMode, so every effect runs setup/cleanup/setup on
    // mount. Imperatively created elements are exactly the kind of thing that leaks a second
    // copy — and two elements on one track would play the same audio twice.
    const { result } = renderHook(() => useLiveKitViewer({
      enabled: true, url: URL_, token: TOKEN,
    }), { wrapper: StrictMode });
    await waitFor(() => expect(result.current.connected).toBe(true));
    const el = document.createElement("video");
    act(() => { result.current.mediaRef(el); });

    const mic = makeTrack(Track.Kind.Audio, Track.Source.Microphone);
    // rooms[] can hold more than one FakeRoom here: StrictMode's remount builds a second,
    // and the hook disposes the first. Drive the live one.
    const live = rooms[rooms.length - 1];
    await subscribe(live, pubFor(mic));

    await waitFor(() => expect(result.current.hasAudio).toBe(true));
    expect(audioEls()).toHaveLength(1);
    expect(mic.attachedTo.size).toBe(1);
  });

  it("applies the viewer's mute and volume to the audio elements", async () => {
    const { result, rerender } = renderHook(
      ({ audioMuted, audioVolume }) => useLiveKitViewer({
        enabled: true, url: URL_, token: TOKEN, audioMuted, audioVolume,
      }),
      { initialProps: { audioMuted: true, audioVolume: 80 } },
    );
    await waitFor(() => expect(result.current.connected).toBe(true));
    const el = document.createElement("video");
    act(() => { result.current.mediaRef(el); });
    await subscribe(rooms[0], pubFor(makeTrack(Track.Kind.Audio, Track.Source.Microphone)));
    await waitFor(() => expect(audioEls()).toHaveLength(1));

    expect(audioEls()[0].muted).toBe(true);
    expect(audioEls()[0].volume).toBeCloseTo(0.8);

    await act(async () => { rerender({ audioMuted: false, audioVolume: 40 }); });
    expect(audioEls()[0].muted).toBe(false);
    expect(audioEls()[0].volume).toBeCloseTo(0.4);
  });
});

// ── Phase 4/10: one video surface, deterministically ──────────────────────────

describe("video attachment", () => {
  it("prefers a screen share over a camera rather than letting them fight", async () => {
    // One <video> can only show one track, so the old code's "whichever attached last" was
    // arbitrary and churned on every re-run.
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    const videoEl = attachElement(result);

    const cam = makeTrack(Track.Kind.Video, Track.Source.Camera);
    const share = makeTrack(Track.Kind.Video, Track.Source.ScreenShare);
    await subscribe(rooms[0], pubFor(cam, LADDER), pubFor(share, LADDER));

    await waitFor(() => expect(share.attachedTo.has(videoEl)).toBe(true));
    expect(videoEl.__tracks).toEqual([share]);
    expect(cam.attachedTo.has(videoEl)).toBe(false);
  });

  it("does not disturb the shown video stream on an unrelated event", async () => {
    // Under the old all-tracks-to-one-element code an arriving audio track mutated the
    // <video>'s own stream (audio was added to it, evicting nothing but still a mutation, and
    // a second audio track then churned it). Now the video element's track set is written
    // exactly once and never touched again.
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    const videoEl = attachElement(result);

    const cam = makeTrack(Track.Kind.Video, Track.Source.Camera);
    await subscribe(rooms[0], pubFor(cam, LADDER));
    await waitFor(() => expect(videoEl.__tracks).toEqual([cam]));
    expect(videoEl.__mutations).toBe(1);

    await subscribe(rooms[0], pubFor(makeTrack(Track.Kind.Audio, Track.Source.Microphone)));
    await subscribe(rooms[0], pubFor(makeTrack(Track.Kind.Audio, Track.Source.Microphone)));

    expect(videoEl.__mutations).toBe(1);          // stream never rewritten
    expect(videoEl.__tracks).toEqual([cam]);
    expect(cam.detach).not.toHaveBeenCalled();
  });
});
