import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

// Regression cover for the reported failure: the platform showed an event as LIVE while the
// host console read "Not publishing — Lost connection to the stream — still trying to
// reconnect in the background", and viewers saw only the host placeholder.
//
// Both halves are covered here against a fake livekit-client, because the real defects were
// in the LIFECYCLE around the SDK, not in the SDK:
//
//   * PRODUCER: connectOnce() built a `new Room()` without disposing the one it replaced,
//     and the effect cleanup never awaited disconnect(). LiveKit permits one connection per
//     identity and evicts the older, so every retry added a second connection under
//     secondary(user, "host"); the eviction arrived on the previous room as
//     DUPLICATE_IDENTITY, whose handler scheduled another retry, which built another room.
//     The console evicted itself in a loop and never sustained a publication.
//   * PRODUCER: `connected` (the signalling session) was reported to the UI and the backend
//     as "isPublishing". A connect that published nothing still claimed "Live — this feed is
//     being published to viewers".
//   * VIEWER: TrackSubscribed attached only `if (mediaRef.current)` but set hasVideo
//     unconditionally, so a track arriving before the <video> element mounted was never
//     attached while the placeholder was dismissed — and nothing retried.

// ── fake livekit-client ───────────────────────────────────────────────────────
// Enum values mirror livekit-client's own, so a mismatch between what the hooks listen for
// and what the SDK emits would fail here rather than only in production.
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
const DisconnectReason = {
  CLIENT_INITIATED: 1,
  DUPLICATE_IDENTITY: 2,
  SERVER_SHUTDOWN: 3,
  PARTICIPANT_REMOVED: 4,
  ROOM_DELETED: 5,
};

// Every Room the code under test constructs, in order — how a leaked second connection
// becomes observable.
let rooms = [];
// Set by a test to make the next publishTrack reject, reproducing a publish that fails after
// a successful connect (the first domino in the eviction loop).
let publishFailures = 0;

class Emitter {
  constructor() {
    this.handlers = new Map();
  }

  on(event, fn) {
    if (!this.handlers.has(event)) this.handlers.set(event, new Set());
    this.handlers.get(event).add(fn);
    return this;
  }

  off(event, fn) {
    this.handlers.get(event)?.delete(fn);
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

let sid = 0;

class FakeLocalParticipant {
  constructor() {
    this.identity = "host::11111111-1111-4111-8111-111111111111";
    this.trackPublications = new Map();
    this.videoTrackPublications = new Map();
    this.audioTrackPublications = new Map();
  }

  async publishTrack(mediaStreamTrack, options) {
    if (publishFailures > 0) {
      publishFailures -= 1;
      throw new Error("insufficient permissions");
    }
    sid += 1;
    const kind = options.source === Track.Source.Microphone
      || options.source === Track.Source.ScreenShareAudio
      ? Track.Kind.Audio : Track.Kind.Video;
    const pub = {
      trackSid: `TR_${sid}`,
      source: options.source,
      kind,
      isMuted: mediaStreamTrack.enabled === false,
      track: { mediaStreamTrack, detach: vi.fn(), attach: vi.fn() },
    };
    this.trackPublications.set(pub.trackSid, pub);
    (kind === Track.Kind.Video ? this.videoTrackPublications : this.audioTrackPublications)
      .set(pub.trackSid, pub);
    return pub;
  }

  async unpublishTrack(track) {
    for (const [key, pub] of this.trackPublications) {
      if (pub.track === track) {
        this.trackPublications.delete(key);
        this.videoTrackPublications.delete(key);
        this.audioTrackPublications.delete(key);
        return pub;
      }
    }
    return null;
  }
}

class FakeRoom extends Emitter {
  constructor(options) {
    super();
    this.options = options || {};
    this.name = null;
    this.state = "disconnected";
    this.localParticipant = new FakeLocalParticipant();
    this.remoteParticipants = new Map();
    this.connectCalls = [];
    this.disconnectCalls = [];
    rooms.push(this);
  }

  async connect(url, token) {
    this.connectCalls.push({ url, token });
    this.state = "connected";
    this.name = "event_70312f66-5acc-4095-a977-41e27ea31288";
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
}));

const { default: useLiveKitPublish } = await import("./useLiveKitPublish");
const { default: useLiveKitViewer } = await import("./useLiveKitViewer");

// ── helpers ───────────────────────────────────────────────────────────────────
const URL_ = "wss://zoikostream-test.livekit.cloud";
const TOKEN = "fake.publish.token";

const mediaStreamTrack = (kind, enabled = true) => ({ kind, enabled, readyState: "live", stop: vi.fn() });

const fakeStream = ({ video = true, audio = true } = {}) => {
  const v = video ? [mediaStreamTrack("video")] : [];
  const a = audio ? [mediaStreamTrack("audio")] : [];
  return { getVideoTracks: () => v, getAudioTracks: () => a, getTracks: () => [...v, ...a] };
};

const publishHook = (overrides = {}) => {
  const streamRef = { current: overrides.stream === null ? null : (overrides.stream || fakeStream()) };
  return renderHook(() => useLiveKitPublish({
    enabled: overrides.enabled ?? true,
    url: overrides.url ?? URL_,
    token: overrides.token ?? TOKEN,
    streamRef,
    screenTrack: null,
    screenAudioTrack: null,
    videoTrack: streamRef.current?.getVideoTracks()[0] ?? null,
    ...overrides.props,
  }));
};

const remoteTrack = (kind) => ({
  kind,
  source: kind === Track.Kind.Video ? Track.Source.Camera : Track.Source.Microphone,
  attach: vi.fn(),
  detach: vi.fn(),
});

beforeEach(() => {
  rooms = [];
  publishFailures = 0;
  sid = 0;
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── producer ──────────────────────────────────────────────────────────────────

describe("producer publication", () => {
  it("does not report publishing from a local preview alone", async () => {
    // `enabled` is Dashboard's `live && media.active`. Preview-only means not live: the hook
    // must not connect at all, and must certainly not claim to be publishing.
    const { result } = publishHook({ enabled: false });
    await waitFor(() => expect(result.current.publishing).toBe(false));
    expect(result.current.connected).toBe(false);
    expect(rooms).toHaveLength(0);
  });

  it("reports publishing only once LiveKit holds a real track publication", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.publishing).toBe(true));
    expect(result.current.connected).toBe(true);
    expect(result.current.publishedVideo).toBe(true);
    expect(result.current.publishedAudio).toBe(true);
    expect(result.current.publishError).toBeNull();
    // Verified against the room's own publications, not against our intent.
    const [room] = rooms;
    expect(room.localParticipant.videoTrackPublications.size).toBe(1);
    expect([...room.localParticipant.videoTrackPublications.values()][0].source)
      .toBe(Track.Source.Camera);
  });

  it("refuses to call a connect that published nothing a success", async () => {
    // THE FALSE-LIVE BUG: a room whose stream had no tracks left still set connected=true,
    // and `connected` was reported to the UI and the server as "publishing". The connect now
    // fails instead, which puts the hook into its retry loop — a first failure retries
    // QUIETLY (publishError stays null while `reconnecting` is true; see scheduleRetry's
    // stillFast branch), so the load-bearing assertion is that it never claims to publish.
    const empty = { getVideoTracks: () => [], getAudioTracks: () => [], getTracks: () => [] };
    const { result } = publishHook({ stream: empty });
    await waitFor(() => expect(result.current.reconnecting).toBe(true));
    expect(result.current.publishing).toBe(false);
    expect(result.current.publishedVideo).toBe(false);
    expect(result.current.publishedAudio).toBe(false);
  });

  it("becomes honest about a persistent failure instead of claiming to be live", async () => {
    // Past SLOW_ATTEMPTS_AFTER the banner stops saying "reconnecting" and shows the message
    // from the reported screenshot. Whatever it says, it must never report publishing.
    vi.useFakeTimers();
    try {
      const empty = { getVideoTracks: () => [], getAudioTracks: () => [], getTracks: () => [] };
      const streamRef = { current: empty };
      const { result } = renderHook(() => useLiveKitPublish({
        enabled: true, url: URL_, token: TOKEN, streamRef,
        screenTrack: null, screenAudioTrack: null, videoTrack: null,
      }));
      // Six failed attempts crosses the threshold; the capped backoff tops out at 15s.
      for (let i = 0; i < 7; i += 1) {
        // eslint-disable-next-line no-await-in-loop
        await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
      }
      expect(result.current.publishing).toBe(false);
      expect(result.current.publishError).toMatch(/lost connection to the stream/i);
      expect(result.current.reconnecting).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });

  it("publishes an audio-only broadcast rather than treating it as a failure", async () => {
    // useMediaPreview's acquireWithFallback yields audio only when the camera is blocked or
    // busy. That is a legitimate broadcast, so `publishing` must be true.
    const { result } = publishHook({ stream: fakeStream({ video: false }) });
    await waitFor(() => expect(result.current.publishing).toBe(true));
    expect(result.current.publishedVideo).toBe(false);
    expect(result.current.publishedAudio).toBe(true);
  });

  it("joins with exactly the server-issued url and token, and builds no room name of its own", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    expect(rooms[0].connectCalls).toEqual([{ url: URL_, token: TOKEN }]);
    // The room name is whatever the token's grant says; the client never derives it.
    expect(rooms[0].name).toBe("event_70312f66-5acc-4095-a977-41e27ea31288");
  });

  it("never stops the preview's own tracks when LiveKit tears down", async () => {
    const { result, unmount } = publishHook();
    await waitFor(() => expect(result.current.publishing).toBe(true));
    expect(rooms[0].options.stopLocalTrackOnUnpublish).toBe(false);
    unmount();
    await waitFor(() => expect(rooms[0].disconnectCalls).toEqual([{ stopTracks: false }]));
  });
});

describe("producer reconnect", () => {
  it("keeps exactly one live connection when a publish failure forces a retry", async () => {
    // THE EVICTION LOOP. The first publish fails after a successful connect; the retry must
    // dispose the first room BEFORE connecting again, or LiveKit sees two connections under
    // one identity and evicts one, whose Disconnected handler retries, and so on forever.
    vi.useFakeTimers();
    try {
      publishFailures = 1;
      const streamRef = { current: fakeStream() };
      renderHook(() => useLiveKitPublish({
        enabled: true, url: URL_, token: TOKEN, streamRef,
        screenTrack: null, screenAudioTrack: null, videoTrack: null,
      }));
      // Let the failing attempt settle, then run the backoff timer.
      await act(async () => { await Promise.resolve(); });
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });

      expect(rooms.length).toBeGreaterThan(1);
      // Every room but the newest must have been disconnected. A single still-connected
      // predecessor is the bug.
      const stillConnected = rooms.filter((r) => r.state === "connected");
      expect(stillConnected).toHaveLength(1);
      expect(stillConnected[0]).toBe(rooms[rooms.length - 1]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("republishes only what is missing after a reconnect, never duplicating a track", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.publishing).toBe(true));
    const room = rooms[0];
    expect(room.localParticipant.trackPublications.size).toBe(2); // camera + mic

    // A reconnect that came back with publications intact.
    await act(async () => {
      room.emit(RoomEvent.Reconnected);
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.publishing).toBe(true));
    expect(room.localParticipant.trackPublications.size).toBe(2);
    expect(room.localParticipant.videoTrackPublications.size).toBe(1);
  });

  it("restores the camera when a reconnect comes back with an empty publication set", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.publishing).toBe(true));
    const room = rooms[0];

    // A full reconnect can lose the publications entirely — the case that used to leave the
    // host "connected" and apparently live while sending nothing.
    room.localParticipant.trackPublications.clear();
    room.localParticipant.videoTrackPublications.clear();
    room.localParticipant.audioTrackPublications.clear();

    await act(async () => {
      room.emit(RoomEvent.Reconnected);
      await Promise.resolve();
    });
    await waitFor(() => expect(room.localParticipant.videoTrackPublications.size).toBe(1));
    await waitFor(() => expect(result.current.publishing).toBe(true));
  });

  it("stops reporting publishing the moment the connection drops", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.publishing).toBe(true));

    await act(async () => {
      rooms[0].emit(RoomEvent.Reconnecting);
    });
    // Mid-reconnect the publication state is unknown; claiming it holds is what kept the
    // green "being published to viewers" banner over a dropped producer.
    expect(result.current.publishing).toBe(false);
  });

  it("stops retrying — and says why — when the identity is already connected elsewhere", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.publishing).toBe(true));
    const before = rooms.length;

    await act(async () => {
      rooms[0].emit(RoomEvent.Disconnected, DisconnectReason.DUPLICATE_IDENTITY);
    });
    await waitFor(() => expect(result.current.publishError).toMatch(/another tab or window/i));
    expect(result.current.reconnecting).toBe(false);
    expect(rooms).toHaveLength(before); // no retry room was built
  });

  it("re-derives publication state when LiveKit unpublishes a local track", async () => {
    const { result } = publishHook();
    await waitFor(() => expect(result.current.publishing).toBe(true));
    const room = rooms[0];

    room.localParticipant.trackPublications.clear();
    room.localParticipant.videoTrackPublications.clear();
    room.localParticipant.audioTrackPublications.clear();
    await act(async () => {
      room.emit(RoomEvent.LocalTrackUnpublished);
    });
    expect(result.current.publishing).toBe(false);
  });

  it("treats a muted camera as still published", async () => {
    // The host toggling their camera off disables the track; the publication survives, and
    // viewers handle a muted track. Reporting "not publishing" here would degrade the event.
    const off = mediaStreamTrack("video", false);
    const stream = {
      getVideoTracks: () => [off],
      getAudioTracks: () => [mediaStreamTrack("audio")],
      getTracks: () => [off],
    };
    const { result } = publishHook({ stream });
    await waitFor(() => expect(result.current.publishing).toBe(true));
    expect(result.current.publishedVideo).toBe(true);
    expect([...rooms[0].localParticipant.videoTrackPublications.values()][0].isMuted).toBe(true);
  });
});

// ── viewer ────────────────────────────────────────────────────────────────────

describe("viewer subscription", () => {
  const viewerHook = (overrides = {}) => renderHook(() => useLiveKitViewer({
    enabled: true, url: URL_, token: "fake.viewer.token", ...overrides,
  }));

  const attachElement = (result) => {
    const el = document.createElement("video");
    act(() => { result.current.mediaRef(el); });
    return el;
  };

  it("renders a track that was already published before the viewer joined", async () => {
    // The room is populated at connect time, so nothing new is ever emitted — the case a
    // "listen only for future TrackSubscribed" implementation misses entirely.
    const video = remoteTrack(Track.Kind.Video);
    const RoomCtor = rooms;
    const { result } = viewerHook();
    await waitFor(() => expect(RoomCtor.length).toBe(1));
    const room = rooms[0];
    room.remoteParticipants.set("host", {
      identity: "host",
      trackPublications: new Map([["a", { track: video, isSubscribed: true, source: Track.Source.Camera, kind: Track.Kind.Video }]]),
    });
    // Re-emit the room's post-connect sweep by reconnecting, which re-derives from the room.
    await act(async () => { room.emit(RoomEvent.Reconnected); await Promise.resolve(); });

    const el = attachElement(result);
    await waitFor(() => expect(result.current.hasVideo).toBe(true));
    await waitFor(() => expect(video.attach).toHaveBeenCalledWith(el));
  });

  it("renders a track published AFTER the viewer joined", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    const el = attachElement(result);
    const video = remoteTrack(Track.Kind.Video);

    await act(async () => { rooms[0].emit(RoomEvent.TrackSubscribed, video); });
    await waitFor(() => expect(result.current.hasVideo).toBe(true));
    expect(video.attach).toHaveBeenCalledWith(el);
  });

  it("attaches a track that arrived BEFORE the video element existed", async () => {
    // THE PLACEHOLDER BUG: the old handler skipped attach() when mediaRef.current was null
    // but still flipped hasVideo, so the placeholder vanished over an element that had never
    // been handed a track — and nothing retried.
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    const video = remoteTrack(Track.Kind.Video);

    await act(async () => { rooms[0].emit(RoomEvent.TrackSubscribed, video); });
    expect(video.attach).not.toHaveBeenCalled();   // no element yet — correctly deferred

    const el = attachElement(result);
    await waitFor(() => expect(video.attach).toHaveBeenCalledWith(el));
    expect(result.current.hasVideo).toBe(true);
  });

  it("keeps video when a second publisher leaves", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);
    const hostVideo = remoteTrack(Track.Kind.Video);
    const speakerVideo = remoteTrack(Track.Kind.Video);

    await act(async () => {
      rooms[0].emit(RoomEvent.TrackSubscribed, hostVideo);
      rooms[0].emit(RoomEvent.TrackSubscribed, speakerVideo);
    });
    await waitFor(() => expect(result.current.hasVideo).toBe(true));

    await act(async () => { rooms[0].emit(RoomEvent.TrackUnsubscribed, speakerVideo); });
    // The host is still sending; a per-kind boolean set by hand reported false here.
    expect(result.current.hasVideo).toBe(true);
  });

  it("removes stale video when the host leaves", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);
    const video = remoteTrack(Track.Kind.Video);

    await act(async () => { rooms[0].emit(RoomEvent.TrackSubscribed, video); });
    await waitFor(() => expect(result.current.hasVideo).toBe(true));

    await act(async () => {
      rooms[0].emit(RoomEvent.ParticipantDisconnected, {
        identity: "host",
        trackPublications: new Map([["a", { track: video }]]),
      });
    });
    // Without this the element kept the host's frozen last frame.
    expect(result.current.hasVideo).toBe(false);
    expect(video.detach).toHaveBeenCalled();
  });

  it("tracks audio independently of video, so an audio-only stream is not 'no media'", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));
    attachElement(result);

    await act(async () => { rooms[0].emit(RoomEvent.TrackSubscribed, remoteTrack(Track.Kind.Audio)); });
    await waitFor(() => expect(result.current.hasAudio).toBe(true));
    expect(result.current.hasVideo).toBe(false);
  });

  it("keeps one connection across a retry, and stops on a deleted room", async () => {
    const { result } = viewerHook();
    await waitFor(() => expect(result.current.connected).toBe(true));

    await act(async () => { rooms[0].emit(RoomEvent.Disconnected, DisconnectReason.ROOM_DELETED); });
    await waitFor(() => expect(result.current.error).toMatch(/broadcast has ended/i));
    expect(rooms).toHaveLength(1);   // never retried into a room that is gone
  });
});
