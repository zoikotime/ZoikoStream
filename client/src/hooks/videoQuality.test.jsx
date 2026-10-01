// Manual video quality: the menu offers only renditions that actually exist, and choosing
// one really changes this viewer's LiveKit subscription.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// The Quality menu was a hardcoded array — ["Auto","1080p","720p","480p","360p"] — with
// `disabled={quality !== "Auto"}` written as a constant and the checkmark pinned to "Auto".
// Its onClick only closed the menu. It had never been connected to LiveKit at all.
//
// It was NOT a simulcast problem: livekit-client publishes with simulcast:true by default,
// so the host was already sending layers. Nothing asked for them.
//
// ── THE SECOND BUG ──────────────────────────────────────────────────────────────────────
// Once wired up, every option went through setVideoQuality, whose scale is only LOW/MEDIUM/
// HIGH. "Auto" has to cap at HIGH (a cap at the top is no cap, which is what hands control
// back to adaptive streaming) — so the TOP rendition and Auto sent the same value.
// livekit-client's setVideoQuality early-returns on `requestedMaxQuality === quality`, so
// Auto -> 720p emitted no UpdateTrackSettings at all: the checkmark moved and the
// subscription did not. A three-level enum also cannot express a 1080p/720p/360p menu.
//
// Manual picks are now expressed as the layer's real DIMENSIONS. The two setters write
// different fields and each clears the other, so Auto and every rendition are distinct
// states with distinct requests. That buys DISTINCTNESS, not more rungs: WebRTC simulcast
// publishes at most three renditions, so the ladder tops out at three rows plus Auto and a
// 4K camera reads 2160p/720p/360p — 1080p is replaced, not added to.
//
// These exercise the real exported helpers rather than a copy of them.
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
// The Auto tests drive a REAL RemoteTrackPublication rather than a stub: whether
// setVideoQuality(HIGH) is a ceiling or a pin is a fact about livekit-client, so asserting it
// against a mock of our own making would prove nothing.
import { RemoteTrackPublication, Track, TrackEvent, VideoQuality as LKVideoQuality } from "livekit-client";
import { TrackInfo, TrackType, VideoLayer } from "@livekit/protocol";

import { applyQuality, describeLayers, resolveQuality } from "./useLiveKitViewer";
import VideoPlayer from "../components/watch/VideoPlayer";

// Only the hook's DEFAULT export is faked, so the menu can be driven with a chosen set of
// layers. describeLayers/applyQuality above stay REAL — the point of the last describe is
// that the labels the menu renders are the same strings applyQuality can resolve.
const viewer = vi.hoisted(() => ({ current: null }));
vi.mock("./useLiveKitViewer", async (importOriginal) => ({
  ...(await importOriginal()),
  default: () => viewer.current,
}));

// VideoQuality from livekit-client: LOW 0, MEDIUM 1, HIGH 2.
const LOW = 0;
const MEDIUM = 1;
const HIGH = 2;

const pubWith = (layers) => ({
  trackInfo: layers ? { layers } : undefined,
  setVideoQuality: vi.fn(),
  setVideoDimensions: vi.fn(),
});

// What a 1080p camera really yields under LiveKit simulcast: the publisher's h360 and h720
// presets plus the real capture resolution on top.
const THREE_LAYERS = [
  { quality: HIGH, width: 1920, height: 1080 },
  { quality: MEDIUM, width: 1280, height: 720 },
  { quality: LOW, width: 640, height: 360 },
];

// The same publisher ladder driven by a 720p camera: the mid preset and the capture
// resolution coincide, so there is no 1080p rendition to offer.
const SEVEN_TWENTY_LADDER = [
  { quality: HIGH, width: 1280, height: 720 },
  { quality: MEDIUM, width: 1280, height: 720 },
  { quality: LOW, width: 640, height: 360 },
];

// And driven by a 4K camera. There are only three simulcast rids, and the publisher's mid
// preset is pinned at h720, so the capture resolution takes the top slot and 1080p is
// REPLACED rather than added to — 2160p/720p/360p, never 2160p/1080p/720p/360p.
const FOUR_K_LADDER = [
  { quality: HIGH, width: 3840, height: 2160 },
  { quality: MEDIUM, width: 1280, height: 720 },
  { quality: LOW, width: 640, height: 360 },
];

describe("the menu is built from layers that exist", () => {
  it("offers nothing manual for a single-layer publication", () => {
    // Screen share, or a publisher with simulcast off: Auto is the only honest option.
    expect(describeLayers(pubWith([{ quality: HIGH, width: 1920, height: 1080 }]))).toEqual([]);
  });

  it("offers nothing when there is no publication or no layer info at all", () => {
    expect(describeLayers(null)).toEqual([]);
    expect(describeLayers(undefined)).toEqual([]);
    expect(describeLayers(pubWith(undefined))).toEqual([]);
    expect(describeLayers(pubWith([]))).toEqual([]);
  });

  it("labels each layer by the height the publisher really encoded", () => {
    expect(describeLayers(pubWith(THREE_LAYERS)).map((l) => l.label))
      .toEqual(["1080p", "720p", "360p"]);
  });

  it("does not invent 1080p for a 720p camera", () => {
    // Whatever ladder the publisher runs, a 720p camera tops out at 720p. The old fixed list
    // would have shown 1080p and 480p with nothing behind either — exactly the "upscale and
    // call it 1080p" failure.
    const labels = describeLayers(pubWith([
      { quality: HIGH, width: 1280, height: 720 },
      { quality: MEDIUM, width: 640, height: 360 },
      { quality: LOW, width: 320, height: 180 },
    ])).map((l) => l.label);

    expect(labels).toEqual(["720p", "360p", "180p"]);
    expect(labels).not.toContain("1080p");
    expect(labels).not.toContain("480p");
  });

  // The publisher's mid simulcast preset is h720 while the top layer is always the real
  // capture resolution, so a camera granting exactly 720p encodes 720p twice. Both layers
  // are real, but two rows reading "720p" is a choice with no visible difference.
  it("collapses layers that share a height, keeping the highest quality at that size", () => {
    const out = describeLayers(pubWith([
      { quality: HIGH, width: 1280, height: 720 },
      { quality: MEDIUM, width: 1280, height: 720 },
      { quality: LOW, width: 640, height: 360 },
    ]));

    expect(out.map((l) => l.label)).toEqual(["720p", "360p"]);
    // HIGH, not MEDIUM: at equal size, the layer with the bandwidth headroom behind it.
    expect(out[0].quality).toBe(HIGH);
  });

  it("offers nothing manual when every layer collapses to one height", () => {
    // Degenerate but reachable: a capture resolution that exactly matches the mid preset
    // with no low layer. One distinct rendition is what Auto already does.
    expect(describeLayers(pubWith([
      { quality: HIGH, width: 1280, height: 720 },
      { quality: MEDIUM, width: 1280, height: 720 },
    ]))).toEqual([]);
  });

  it("sorts highest first and drops layers with no height", () => {
    const out = describeLayers(pubWith([
      { quality: LOW, width: 640, height: 360 },
      { quality: HIGH, width: 1920, height: 1080 },
      { quality: MEDIUM, width: 0, height: 0 },   // server reported nothing usable
    ]));
    expect(out.map((l) => l.height)).toEqual([1080, 360]);
  });
});

describe("selecting a quality changes the subscription", () => {
  it("requests the matching layer by its real dimensions, not a CSS resize", () => {
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "720p");
    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });
  });

  it("requests the low layer for the lowest rendition", () => {
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "360p");
    // The 360p row maps to the layer the publisher labels LOW — asked for by that layer's
    // own size, which emitTrackUpdate turns into a real ceiling below what adaptive wanted.
    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 640, height: 360 });
    expect(describeLayers(pub).find((l) => l.label === "360p").quality).toBe(LOW);
  });

  it("restores adaptive selection for Auto", () => {
    // setVideoQuality sets requestedMaxQuality — a CAP, not a pin. Capping at HIGH is no
    // cap at all, which hands the choice back to LiveKit's adaptive/dynacast selection, and
    // it is also the call that CLEARS requestedVideoDimensions, releasing a manual pick.
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "auto");
    expect(pub.setVideoQuality).toHaveBeenCalledWith(HIGH);
    expect(pub.setVideoDimensions).not.toHaveBeenCalled();
  });

  // The regression this change exists for.
  it("does not send the top rendition as a bare HIGH, which Auto already sends", () => {
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "1080p");           // the TOP row of this ladder

    // Not setVideoQuality(HIGH): that is byte-identical to Auto, and livekit-client
    // early-returns on an unchanged requestedMaxQuality, so nothing would reach the SFU.
    expect(pub.setVideoQuality).not.toHaveBeenCalled();
    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 1920, height: 1080 });
  });

  it("gives Auto and the top rendition genuinely different requests", () => {
    const onAuto = pubWith(THREE_LAYERS);
    const onTop = pubWith(THREE_LAYERS);

    applyQuality(onAuto, "auto");
    applyQuality(onTop, "1080p");

    expect(onAuto.setVideoQuality.mock.calls).toEqual([[HIGH]]);
    expect(onAuto.setVideoDimensions).not.toHaveBeenCalled();
    expect(onTop.setVideoDimensions.mock.calls).toEqual([[{ width: 1920, height: 1080 }]]);
    expect(onTop.setVideoQuality).not.toHaveBeenCalled();
  });

  it("gives every rendition in the ladder its own distinct request", () => {
    // A LOW/MEDIUM/HIGH mapping collapses the TOP rung onto Auto's HIGH, so one of the three
    // is never independently requestable. Dimensions name the rung itself. Three is still the
    // ceiling — this is about telling the rungs apart, not having more of them.
    const seen = describeLayers(pubWith(THREE_LAYERS)).map((layer) => {
      const pub = pubWith(THREE_LAYERS);
      applyQuality(pub, layer.label);
      return pub.setVideoDimensions.mock.calls[0][0];
    });

    expect(seen).toEqual([
      { width: 1920, height: 1080 },
      { width: 1280, height: 720 },
      { width: 640, height: 360 },
    ]);
    expect(new Set(seen.map((d) => `${d.width}x${d.height}`)).size).toBe(seen.length);
  });

  it("is inert when there is no publication to act on", () => {
    expect(() => applyQuality(null, "720p")).not.toThrow();
    expect(() => applyQuality({}, "720p")).not.toThrow();
    expect(() => applyQuality({}, "auto")).not.toThrow();
  });

  it("survives a publication that refuses the call", () => {
    // LiveKit's isManualOperationAllowed() returns false once a track is no longer
    // subscribed. The next subscribe re-applies the preference, so this must not throw.
    const pub = {
      trackInfo: { layers: THREE_LAYERS },
      setVideoQuality: vi.fn(() => { throw new Error("not subscribed"); }),
      setVideoDimensions: vi.fn(() => { throw new Error("not subscribed"); }),
    };
    expect(() => applyQuality(pub, "720p")).not.toThrow();
    expect(() => applyQuality(pub, "auto")).not.toThrow();
  });
});

describe("switching between qualities", () => {
  // Each transition has to produce the right viewer-side request, not just the right label.
  // applyQuality is stateless — the publication holds the state — so one publication is
  // driven through the whole sequence and every call is inspected in order.
  it("Auto -> 720p -> 360p -> 720p -> Auto requests the right thing at each step", () => {
    const pub = pubWith(THREE_LAYERS);

    applyQuality(pub, "auto");
    applyQuality(pub, "720p");
    applyQuality(pub, "360p");
    applyQuality(pub, "720p");
    applyQuality(pub, "auto");

    // Auto is a quality call (releases the dimension cap); a rendition is a dimensions call.
    expect(pub.setVideoQuality.mock.calls).toEqual([[HIGH], [HIGH]]);
    expect(pub.setVideoDimensions.mock.calls).toEqual([
      [{ width: 1280, height: 720 }],
      [{ width: 640, height: 360 }],
      [{ width: 1280, height: 720 }],
    ]);
  });

  it("360p -> Auto releases the cap rather than lowering it further", () => {
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "360p");
    applyQuality(pub, "auto");

    expect(pub.setVideoDimensions.mock.calls).toEqual([[{ width: 640, height: 360 }]]);
    expect(pub.setVideoQuality.mock.calls).toEqual([[HIGH]]);
  });

  it("360p -> 720p raises the ceiling instead of doing nothing", () => {
    // The old mapping made this LOW -> HIGH, which worked; the same transition on a TOP
    // rendition (HIGH -> HIGH) did not. Dimensions make both of them real.
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "360p");
    applyQuality(pub, "720p");

    expect(pub.setVideoDimensions.mock.calls).toEqual([
      [{ width: 640, height: 360 }],
      [{ width: 1280, height: 720 }],
    ]);
  });
});

describe("a quality that the publisher is not sending", () => {
  it("steps 1080p down to 720p instead of fabricating the layer", () => {
    // A 720p camera has no 1080p rendition. Asking for one must not invent dimensions, and
    // must not silently hand the viewer back to Auto either — Auto can give them MORE than
    // they asked for, which is the opposite of a manual cap.
    const pub = pubWith(SEVEN_TWENTY_LADDER);

    applyQuality(pub, "1080p");

    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });
    expect(pub.setVideoQuality).not.toHaveBeenCalled();
  });

  it("steps down past a gap to the next rendition that exists", () => {
    // 1080p requested, ladder is 720p/360p with nothing at 1080: 720p is the closest step
    // DOWN, never 360p and never a jump back up to adaptive.
    expect(resolveQuality(describeLayers(pubWith(SEVEN_TWENTY_LADDER)), "1080p")).toBe("720p");
  });

  it("falls to the lowest rendition when nothing at or below the request exists", () => {
    // Viewer is on 360p to save bandwidth and the host moves to a 1080p/720p ladder. There
    // is nothing at or below 360p, so the lowest on offer is the closest to the intent.
    const layers = describeLayers(pubWith([
      { quality: HIGH, width: 1920, height: 1080 },
      { quality: LOW, width: 1280, height: 720 },
    ]));
    expect(resolveQuality(layers, "360p")).toBe("720p");
  });

  it("falls back to adaptive for a single-rendition publication", () => {
    // Screen share, or simulcast off: describeLayers offers nothing, so there is no
    // rendition to request and Auto is the only honest state.
    const pub = pubWith([{ quality: HIGH, width: 1920, height: 1080 }]);
    applyQuality(pub, "1080p");

    expect(pub.setVideoDimensions).not.toHaveBeenCalled();
    expect(pub.setVideoQuality).toHaveBeenCalledWith(HIGH);
  });

  it("does not throw when layer metadata has not arrived yet", () => {
    const pub = pubWith(undefined);
    expect(() => applyQuality(pub, "720p")).not.toThrow();
    expect(pub.setVideoDimensions).not.toHaveBeenCalled();
  });
});

describe("one viewer's choice is only their own", () => {
  it("acts on that viewer's publication and nothing else", () => {
    // Two browsers, two subscriptions. setVideoQuality is a per-subscription request, so
    // viewer A picking 360p cannot alter what viewer B receives — nor what the host sends.
    const viewerA = pubWith(THREE_LAYERS);
    const viewerB = pubWith(THREE_LAYERS);

    applyQuality(viewerA, "360p");

    expect(viewerA.setVideoDimensions).toHaveBeenCalledWith({ width: 640, height: 360 });
    expect(viewerB.setVideoDimensions).not.toHaveBeenCalled();
    expect(viewerB.setVideoQuality).not.toHaveBeenCalled();
  });
});

describe("the publication can change underneath the control", () => {
  it("re-applies the standing preference to a replacement publication", () => {
    // Host turns the camera off and on, swaps device, or the room reconnects: a NEW
    // publication arrives and the viewer's choice has to follow it rather than stay bound
    // to the old one. This is what onSubscribed does with preferenceRef.
    const preference = "720p";

    const first = pubWith(THREE_LAYERS);
    applyQuality(first, preference);
    expect(first.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });

    const replacement = pubWith(THREE_LAYERS);
    applyQuality(replacement, preference);
    expect(replacement.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });
  });

  it("re-resolves the label against the NEW ladder, not the old one", () => {
    // A label survives a publication swap in a way a VideoQuality enum could not: the host
    // moves from a 1080p camera to a 720p one, where 720p is HIGH rather than MEDIUM. The
    // viewer still gets 720p, because the request is resolved from the layer list in hand.
    const preference = "720p";

    const onePointEight = pubWith(THREE_LAYERS);
    applyQuality(onePointEight, preference);
    expect(onePointEight.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });

    const sevenTwenty = pubWith([
      { quality: HIGH, width: 1280, height: 720 },
      { quality: LOW, width: 640, height: 360 },
    ]);
    applyQuality(sevenTwenty, preference);
    expect(sevenTwenty.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });
  });

  it("re-derives the offered renditions from the new publication", () => {
    // Camera (three layers) replaced by a single-layer screen share: manual options must
    // disappear rather than linger from the previous track.
    expect(describeLayers(pubWith(THREE_LAYERS))).toHaveLength(3);
    expect(describeLayers(pubWith([{ quality: HIGH, width: 1920, height: 1080 }]))).toEqual([]);
  });
});

describe("layer metadata can arrive after the subscribe", () => {
  // RemoteTrackPublication.updateInfo() mutates the SAME object in place and emits nothing.
  // Deriving the menu from useMemo([publication]) therefore never recomputed: layers that
  // landed after TrackSubscribed stayed invisible and the menu said "single rendition" for
  // the life of the track. describeLayers is now called again on every surrounding track
  // event, so it must read whatever the object holds AT CALL TIME.
  it("re-reads the same publication object after updateInfo mutates it", () => {
    const pub = pubWith([{ quality: HIGH, width: 1280, height: 720 }]);

    // First read: one layer, so no manual options — correct for that moment.
    expect(describeLayers(pub)).toEqual([]);

    // The server's TrackInfo update lands, mutating in place. Same object, new layers.
    pub.trackInfo = { layers: THREE_LAYERS };

    expect(describeLayers(pub).map((l) => l.label)).toEqual(["1080p", "720p", "360p"]);
  });

  it("does not conclude single-rendition from an empty first snapshot", () => {
    const pub = pubWith(undefined);          // nothing known yet
    expect(describeLayers(pub)).toEqual([]);

    pub.trackInfo = { layers: THREE_LAYERS }; // metadata arrives
    expect(describeLayers(pub)).toHaveLength(3);
  });
});

describe("no publication is not the same as one rendition", () => {
  it("yields no options when there is no video publication at all", () => {
    // Starting soon / PREVIEW, or the host's camera is off. The player distinguishes this
    // from a genuine single-layer stream so it does not describe a stream that is not
    // arriving.
    expect(describeLayers(null)).toEqual([]);
  });

  it("yields no options for a genuine single-layer publication", () => {
    // Screen share, or simulcast off. Same empty list, different message in the UI.
    expect(describeLayers(pubWith([{ quality: HIGH, width: 1920, height: 1080 }]))).toEqual([]);
  });
});

// jsdom has no media pipeline: HTMLMediaElement.play() is a "not implemented" stub, and the
// player calls it on mount because a live event autoplays.
beforeAll(() => {
  HTMLMediaElement.prototype.play = vi.fn(() => Promise.resolve());
  HTMLMediaElement.prototype.pause = vi.fn();
});

beforeEach(() => {
  viewer.current = null;
});

const LADDER = describeLayers(pubWith(THREE_LAYERS));   // 1080p / 720p / 360p

const mountPlayer = (over = {}) => {
  viewer.current = {
    mediaRef: { current: null },
    connected: true,
    reconnecting: false,
    hasVideo: true,
    hasAudio: true,
    error: null,
    micOn: false,
    micError: null,
    micLive: false,
    toggleMic: vi.fn(),
    enableMic: vi.fn(() => Promise.resolve(true)),
    videoLayers: LADDER,
    hasVideoPublication: true,
    quality: "auto",
    selectQuality: vi.fn(),
    ...over,
  };

  render(
    <VideoPlayer
      event={{ status: "Live", title: "Quality audit" }}
      viewers={1}
      watch={{ livekit_token: "test-token-not-a-secret", livekit_url: "wss://example.invalid" }}
    />
  );
  return viewer.current;
};

const openQualityMenu = () => {
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  fireEvent.click(screen.getByRole("button", { name: /^Quality/ }));
};

// Mount and walk straight to the Quality page, since most cases start there. `openQuality:
// false` stops on the main Settings page, for the row that summarises the current choice.
const mountQualityMenu = ({ openQuality = true, ...over } = {}) => {
  const state = mountPlayer(over);
  if (openQuality) openQualityMenu();
  else fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  return state;
};

// The checkmark is an icon with no text of its own, so the row's accessible name cannot
// carry it — look for the rendered <svg> inside that row instead.
const checkedOn = (name) =>
  Boolean(screen.getByRole("button", { name }).querySelector("svg"));

describe("the quality menu is wired to the selection it displays", () => {
  it("offers exactly the renditions the publisher is sending, plus Auto", () => {
    mountPlayer();
    openQualityMenu();

    expect(screen.getByRole("button", { name: "Auto" })).toBeTruthy();
    ["1080p", "720p", "360p"].forEach((label) => {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    });
    // The fixed list this replaced advertised 480p with nothing behind it.
    expect(screen.queryByRole("button", { name: "480p" })).toBeNull();
  });

  it("asks for a rendition by the label it just displayed", () => {
    // The whole failure mode this guards: the menu renders one identifier and hands
    // applyQuality a different one, so the request never resolves to a layer.
    const state = mountPlayer();
    openQualityMenu();

    fireEvent.click(screen.getByRole("button", { name: "720p" }));

    expect(state.selectQuality).toHaveBeenCalledWith("720p");
    // And that label is resolvable against the same ladder — no silent fallback to adaptive.
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, state.selectQuality.mock.calls[0][0]);
    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });
  });

  it("checks the chosen rendition, and only that one", () => {
    mountPlayer({ quality: "360p" });
    openQualityMenu();

    expect(checkedOn("360p")).toBe(true);
    expect(checkedOn("Auto")).toBe(false);
    expect(checkedOn("720p")).toBe(false);
  });

  it("reports the choice on the main page instead of always saying Auto", () => {
    // This row was the literal string "Auto", so it contradicted the checkmark next door.
    mountPlayer({ quality: "720p" });
    fireEvent.click(screen.getByRole("button", { name: "Settings" }));

    expect(screen.getByRole("button", { name: /^Quality/ }).textContent).toContain("720p");
  });

  it("shows 720p checked when a 1080p pick has no 1080p layer behind it", () => {
    // Host swapped a 1080p camera for a 720p one. Leaving "1080p" checked would show a
    // rendition nobody is sending; showing Auto would claim a mode the viewer did not pick.
    mountPlayer({
      quality: "1080p",
      videoLayers: describeLayers(pubWith(SEVEN_TWENTY_LADDER)),
    });
    openQualityMenu();

    expect(screen.queryByRole("button", { name: "1080p" })).toBeNull();
    expect(checkedOn("720p")).toBe(true);
    expect(checkedOn("Auto")).toBe(false);
    expect(checkedOn("360p")).toBe(false);
  });

  it("shows Auto checked when there is no ladder to step down through", () => {
    // Single-rendition stream, or layer metadata has not landed yet. There is no row to
    // check, so exactly one checkmark still has to be on screen.
    mountPlayer({ quality: "1080p", videoLayers: [] });
    openQualityMenu();

    expect(checkedOn("Auto")).toBe(true);
  });
});

// ── 1080p ───────────────────────────────────────────────────────────────────────────────
// 1080p is not a new hardcoded row: describeLayers already names every rendition by the
// height the publisher encoded, so a 1080p entry appears exactly when a 1080p layer exists
// and is absent the moment it does not. These pin that down, and pin down what a 1080p pick
// does when the stream cannot back it.
describe("the 1080p option", () => {
  const labelsFor = (layers) => describeLayers(pubWith(layers)).map((l) => l.label);

  it("appears when the publication really carries a 1080p layer", () => {
    expect(labelsFor(THREE_LAYERS)).toEqual(["1080p", "720p", "360p"]);
  });

  it("is hidden when the stream only publishes 720p", () => {
    // The whole "do not show fake 1080p" requirement. The publisher's own ladder tops out at
    // the capture resolution, so a 720p camera can never produce a 1080p rendition.
    expect(labelsFor(SEVEN_TWENTY_LADDER)).toEqual(["720p", "360p"]);
    expect(labelsFor(SEVEN_TWENTY_LADDER)).not.toContain("1080p");
  });

  it("is hidden for a 1080p single-rendition stream, where Auto is the only honest option", () => {
    // Screen share or simulcast off: one layer, nothing to choose between.
    expect(labelsFor([{ quality: HIGH, width: 1920, height: 1080 }])).toEqual([]);
  });

  it("requests the highest available layer when selected", () => {
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "1080p");

    const top = describeLayers(pub)[0];
    expect(top.label).toBe("1080p");
    expect(top.quality).toBe(HIGH);                       // the highest VideoQuality on offer
    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 1920, height: 1080 });
  });

  it("is a different request from Auto, 720p and 360p", () => {
    const requestFor = (preference) => {
      const pub = pubWith(THREE_LAYERS);
      applyQuality(pub, preference);
      return pub.setVideoDimensions.mock.calls[0]?.[0] ?? { quality: pub.setVideoQuality.mock.calls[0][0] };
    };

    const requests = ["auto", "1080p", "720p", "360p"].map(requestFor);
    expect(requests).toEqual([
      { quality: HIGH },
      { width: 1920, height: 1080 },
      { width: 1280, height: 720 },
      { width: 640, height: 360 },
    ]);
    expect(new Set(requests.map((r) => JSON.stringify(r))).size).toBe(4);
  });

  it("leaves Auto, 720p and 360p behaving exactly as before", () => {
    const auto = pubWith(THREE_LAYERS);
    const sevenTwenty = pubWith(THREE_LAYERS);
    const threeSixty = pubWith(THREE_LAYERS);

    applyQuality(auto, "auto");
    applyQuality(sevenTwenty, "720p");
    applyQuality(threeSixty, "360p");

    expect(auto.setVideoQuality).toHaveBeenCalledWith(HIGH);
    expect(auto.setVideoDimensions).not.toHaveBeenCalled();
    expect(sevenTwenty.setVideoDimensions).toHaveBeenCalledWith({ width: 1280, height: 720 });
    expect(threeSixty.setVideoDimensions).toHaveBeenCalledWith({ width: 640, height: 360 });
  });

  it("switches 1080p -> 720p -> 1080p -> Auto with a real request each time", () => {
    const pub = pubWith(THREE_LAYERS);

    applyQuality(pub, "1080p");
    applyQuality(pub, "720p");
    applyQuality(pub, "1080p");
    applyQuality(pub, "auto");

    expect(pub.setVideoDimensions.mock.calls).toEqual([
      [{ width: 1920, height: 1080 }],
      [{ width: 1280, height: 720 }],
      [{ width: 1920, height: 1080 }],
    ]);
    expect(pub.setVideoQuality.mock.calls).toEqual([[HIGH]]);
  });

  it("resolves a standing 1080p pick to 720p once the ladder loses its top layer", () => {
    // What the viewer sees after the fallback: the menu reports 720p, not a dead 1080p row
    // and not Auto. resolveQuality is the single place that decides this, and the hook, the
    // checkmark and applyQuality all read it.
    const before = describeLayers(pubWith(THREE_LAYERS));
    const after = describeLayers(pubWith(SEVEN_TWENTY_LADDER));

    expect(resolveQuality(before, "1080p")).toBe("1080p");
    expect(resolveQuality(after, "1080p")).toBe("720p");
  });

  it("keeps Auto on Auto whatever the ladder does", () => {
    expect(resolveQuality(describeLayers(pubWith(THREE_LAYERS)), "auto")).toBe("auto");
    expect(resolveQuality(describeLayers(pubWith(SEVEN_TWENTY_LADDER)), "auto")).toBe("auto");
    expect(resolveQuality([], "auto")).toBe("auto");
  });

  it("does not step down while the pick is still on offer", () => {
    const layers = describeLayers(pubWith(THREE_LAYERS));
    expect(resolveQuality(layers, "1080p")).toBe("1080p");
    expect(resolveQuality(layers, "720p")).toBe("720p");
    expect(resolveQuality(layers, "360p")).toBe("360p");
  });

  it("treats an unknown or malformed preference as Auto rather than throwing", () => {
    const layers = describeLayers(pubWith(THREE_LAYERS));
    expect(resolveQuality(layers, "best")).toBe("auto");
    expect(resolveQuality(undefined, "1080p")).toBe("auto");
    expect(() => applyQuality(pubWith(THREE_LAYERS), "best")).not.toThrow();
  });
});

describe("the 1080p row in the menu", () => {
  it("is offered, checkable and requestable end to end", () => {
    const state = mountQualityMenu({ videoLayers: describeLayers(pubWith(THREE_LAYERS)) });

    expect(screen.getByRole("button", { name: "1080p" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "1080p" }));
    expect(state.selectQuality).toHaveBeenCalledWith("1080p");

    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, state.selectQuality.mock.calls[0][0]);
    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 1920, height: 1080 });
  });

  it("is absent from a 720p stream's menu", () => {
    mountQualityMenu({ videoLayers: describeLayers(pubWith(SEVEN_TWENTY_LADDER)) });

    expect(screen.queryByRole("button", { name: "1080p" })).toBeNull();
    expect(screen.getByRole("button", { name: "720p" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "360p" })).toBeTruthy();
  });

  it("reports 1080p on the main settings row when it is the active choice", () => {
    mountQualityMenu({
      quality: "1080p",
      videoLayers: describeLayers(pubWith(THREE_LAYERS)),
      openQuality: false,
    });

    expect(screen.getByRole("button", { name: /^Quality/ }).textContent).toContain("1080p");
  });
});

// ── 4K ──────────────────────────────────────────────────────────────────────────────────
// A 2160p rendition needs no special handling to appear — describeLayers names every layer by
// its encoded height — but it IS labelled differently, because "2160p" is the one rung
// viewers do not read as a resolution. The gloss trails the digits so the label still parses
// for step-down ordering, and so every other rung is left exactly as it was.
describe("a 4K publication", () => {
  const labelsFor = (layers) => describeLayers(pubWith(layers)).map((l) => l.label);

  it("shows the 2160p rendition as \"2160p (4K)\"", () => {
    expect(labelsFor(FOUR_K_LADDER)[0]).toBe("2160p (4K)");
  });

  it("leaves 1080p, 720p and 360p labels exactly as they were", () => {
    expect(labelsFor(THREE_LAYERS)).toEqual(["1080p", "720p", "360p"]);
    expect(labelsFor(SEVEN_TWENTY_LADDER)).toEqual(["720p", "360p"]);
    // No gloss leaks onto any other rung, including 1440p from a 2K camera.
    expect(labelsFor([
      { quality: HIGH, width: 2560, height: 1440 },
      { quality: MEDIUM, width: 1280, height: 720 },
      { quality: LOW, width: 640, height: 360 },
    ])).toEqual(["1440p", "720p", "360p"]);
  });

  it("selecting it still requests the real 3840x2160 layer", () => {
    const pub = pubWith(FOUR_K_LADDER);
    applyQuality(pub, "2160p (4K)");

    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 3840, height: 2160 });
    expect(pub.setVideoQuality).not.toHaveBeenCalled();
  });

  it("is the three-rung ladder 2160p/720p/360p, with no 1080p rung", () => {
    // The limitation this documents: three rids, and the mid preset pinned at h720. A fourth
    // row is not something the viewer can ask for.
    expect(labelsFor(FOUR_K_LADDER)).toEqual(["2160p (4K)", "720p", "360p"]);
    expect(labelsFor(FOUR_K_LADDER)).not.toContain("1080p");
    expect(labelsFor(FOUR_K_LADDER).length).toBeLessThanOrEqual(3);
  });

  it("does not invent 4K for a 1080p or 720p source", () => {
    expect(labelsFor(THREE_LAYERS)).not.toContain("2160p (4K)");
    expect(labelsFor(SEVEN_TWENTY_LADDER)).not.toContain("2160p (4K)");
  });

  // Fallback is unchanged — the same "highest rung at or below the request" rule, reached
  // through the same parseInt on the label's leading digits.
  it("steps a 2160p pick down to the next rung when 4K goes away", () => {
    // 4K ladder has no 1080p, so the step down from 2160p is 720p.
    expect(resolveQuality(describeLayers(pubWith(SEVEN_TWENTY_LADDER)), "2160p (4K)")).toBe("720p");
    // And where a 1080p rung does exist, it is preferred over 720p.
    expect(resolveQuality(describeLayers(pubWith(THREE_LAYERS)), "2160p (4K)")).toBe("1080p");
  });

  it("holds the 4K pick while the 2160p layer is still on offer", () => {
    expect(resolveQuality(describeLayers(pubWith(FOUR_K_LADDER)), "2160p (4K)")).toBe("2160p (4K)");
  });

  it("leaves the other rungs' fallback behaviour untouched", () => {
    const fourK = describeLayers(pubWith(FOUR_K_LADDER));
    expect(resolveQuality(fourK, "720p")).toBe("720p");
    expect(resolveQuality(fourK, "360p")).toBe("360p");
    expect(resolveQuality(fourK, "auto")).toBe("auto");
    // 1080p was never a rung on a 4K ladder: it steps down to 720p rather than to 2160p.
    expect(resolveQuality(fourK, "1080p")).toBe("720p");
  });

  it("falls back to adaptive when the 4K stream drops to a single rendition", () => {
    const pub = pubWith([{ quality: HIGH, width: 3840, height: 2160 }]);
    applyQuality(pub, "2160p (4K)");

    expect(pub.setVideoDimensions).not.toHaveBeenCalled();
    expect(pub.setVideoQuality).toHaveBeenCalledWith(HIGH);
  });
});

describe("the 4K row in the menu", () => {
  it("is offered, checkable and requestable end to end", () => {
    const state = mountQualityMenu({ videoLayers: describeLayers(pubWith(FOUR_K_LADDER)) });

    expect(screen.getByRole("button", { name: "2160p (4K)" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "2160p (4K)" }));
    expect(state.selectQuality).toHaveBeenCalledWith("2160p (4K)");

    const pub = pubWith(FOUR_K_LADDER);
    applyQuality(pub, state.selectQuality.mock.calls[0][0]);
    expect(pub.setVideoDimensions).toHaveBeenCalledWith({ width: 3840, height: 2160 });
  });

  it("offers Auto plus exactly three rungs, and no 1080p", () => {
    mountQualityMenu({ videoLayers: describeLayers(pubWith(FOUR_K_LADDER)) });

    ["Auto", "2160p (4K)", "720p", "360p"].forEach((name) => {
      expect(screen.getByRole("button", { name })).toBeTruthy();
    });
    expect(screen.queryByRole("button", { name: "1080p" })).toBeNull();
  });

  it("checks the 4K row, and only that one", () => {
    mountQualityMenu({
      quality: "2160p (4K)",
      videoLayers: describeLayers(pubWith(FOUR_K_LADDER)),
    });

    expect(checkedOn("2160p (4K)")).toBe(true);
    expect(checkedOn("Auto")).toBe(false);
    expect(checkedOn("720p")).toBe(false);
  });

  it("reports 2160p (4K) on the main settings row", () => {
    mountQualityMenu({
      quality: "2160p (4K)",
      videoLayers: describeLayers(pubWith(FOUR_K_LADDER)),
      openQuality: false,
    });

    expect(screen.getByRole("button", { name: /^Quality/ }).textContent).toContain("2160p (4K)");
  });

  it("shows 720p checked once the host drops from 4K to a 720p camera", () => {
    mountQualityMenu({
      quality: "2160p (4K)",
      videoLayers: describeLayers(pubWith(SEVEN_TWENTY_LADDER)),
    });

    expect(screen.queryByRole("button", { name: "2160p (4K)" })).toBeNull();
    expect(checkedOn("720p")).toBe(true);
    expect(checkedOn("Auto")).toBe(false);
  });
});

// ── AUTO ────────────────────────────────────────────────────────────────────────────────
// Auto is setVideoQuality(HIGH), and the obvious worry is that it pins the viewer to the top
// rendition. It does not: HIGH is a CEILING. livekit-client's own doc for setVideoQuality
// says "the highest quality the client can accept. if network bandwidth does not allow,
// server will automatically reduce quality", and emitTrackUpdate goes further — while
// adaptive streaming is on it substitutes the PLAYER's measured dimensions whenever they are
// smaller than the capped layer, so the request tracks render size rather than naming a
// rendition at all.
//
// These drive a REAL livekit-client RemoteTrackPublication and read the UpdateTrackSettings
// it emits, because that is the only way to prove ceiling-vs-pin rather than assert it.
// `videoDimensionsAdaptiveStream` is assigned directly to stand in for the resize observer —
// it is exactly what the SDK's own handleVideoDimensionsChange does on VideoDimensionsChanged.
describe("Auto asks LiveKit for the right layer", () => {
  const asTrackInfo = (layers) =>
    new TrackInfo({
      sid: "TR_auto",
      type: TrackType.VIDEO,
      name: "camera",
      simulcast: true,
      layers: layers.map((l) => new VideoLayer({ quality: l.quality, width: l.width, height: l.height })),
    });

  // The UpdateTrackSettings Auto produces for a player of the given rendered size.
  const autoRequestFor = (layers, player) => {
    const pub = new RemoteTrackPublication(Track.Kind.Video, asTrackInfo(layers), true);
    let last;
    pub.on(TrackEvent.UpdateSettings, (s) => { last = s; });
    pub.setVideoQuality(LKVideoQuality.HIGH);      // exactly what applyQuality(pub, "auto") does
    pub.videoDimensionsAdaptiveStream = player;
    pub.emitTrackUpdate();
    // When dimensions are sent, `quality` is left at its protobuf default (0) and the server
    // selects by size — so width/height is the signal to read, not quality.
    return last.width ? { width: last.width, height: last.height } : { quality: last.quality };
  };

  const SMALL = { width: 640, height: 360 };
  const MID = { width: 1280, height: 720 };
  const HUGE = { width: 3840, height: 2160 };

  it("is a ceiling, not a pin — a small player has a small layer requested for it", () => {
    // The whole question. If HIGH pinned the viewer to the top rendition, a 360p-sized player
    // would still be asking for 1920x1080.
    expect(autoRequestFor(THREE_LAYERS, SMALL)).toEqual({ width: 640, height: 360 });
    expect(autoRequestFor(THREE_LAYERS, SMALL)).not.toEqual({ quality: HIGH });
  });

  it("follows the player up and down the 1080p ladder", () => {
    expect(autoRequestFor(THREE_LAYERS, SMALL)).toEqual({ width: 640, height: 360 });
    expect(autoRequestFor(THREE_LAYERS, MID)).toEqual({ width: 1280, height: 720 });
    // Player at or above the top rendition: no dimensions, just the ceiling, and the server
    // returns the best layer it actually has.
    expect(autoRequestFor(THREE_LAYERS, HUGE)).toEqual({ quality: HIGH });
  });

  it("never asks a 720p source for 1080p or 4K", () => {
    const onHuge = autoRequestFor(SEVEN_TWENTY_LADDER, HUGE);
    expect(onHuge).toEqual({ quality: HIGH });
    expect(onHuge.width).toBeUndefined();

    // At every player size, nothing above the top published layer is ever named.
    [SMALL, MID, HUGE].forEach((player) => {
      const req = autoRequestFor(SEVEN_TWENTY_LADDER, player);
      if (req.width) expect(req.height).toBeLessThanOrEqual(720);
    });
  });

  it("uses the 4K ladder's real rungs and never names the 1080p that is not published", () => {
    expect(autoRequestFor(FOUR_K_LADDER, SMALL)).toEqual({ width: 640, height: 360 });
    expect(autoRequestFor(FOUR_K_LADDER, MID)).toEqual({ width: 1280, height: 720 });
    expect(autoRequestFor(FOUR_K_LADDER, HUGE)).toEqual({ quality: HIGH });

    [SMALL, MID, HUGE].forEach((player) => {
      expect(autoRequestFor(FOUR_K_LADDER, player)).not.toEqual({ width: 1920, height: 1080 });
    });
  });

  it("releases a manual dimension cap when the viewer goes back to Auto", () => {
    // The transition that matters: a manual pick leaves requestedVideoDimensions set, and if
    // Auto did not clear it the viewer would stay capped at that rendition however large the
    // player got. setVideoQuality is the call that clears it.
    //
    // requestedVideoDimensions is assigned directly rather than via setVideoDimensions
    // because that setter only records dimensions once a RemoteVideoTrack is attached
    // (`if (isRemoteVideoTrack(this.track))`), which a unit test cannot produce. The starting
    // state is what is being set up here; the assertion is about what Auto does to it.
    const pub = new RemoteTrackPublication(Track.Kind.Video, asTrackInfo(THREE_LAYERS), true);
    let last;
    pub.on(TrackEvent.UpdateSettings, (s) => { last = s; });
    pub.videoDimensionsAdaptiveStream = HUGE;
    pub.requestedVideoDimensions = { width: 640, height: 360 };   // manual 360p in effect

    pub.emitTrackUpdate();
    expect({ width: last.width, height: last.height }).toEqual({ width: 640, height: 360 });

    pub.setVideoQuality(LKVideoQuality.HIGH);                     // back to Auto
    expect(pub.requestedVideoDimensions).toBeUndefined();
    expect(last.width).toBe(0);
    expect(last.quality).toBe(HIGH);
  });
});

describe("Auto stays selected", () => {
  const LADDERS = [SEVEN_TWENTY_LADDER, THREE_LAYERS, FOUR_K_LADDER];

  it("resolves to Auto against every ladder, including none at all", () => {
    LADDERS.forEach((layers) => {
      expect(resolveQuality(describeLayers(pubWith(layers)), "auto")).toBe("auto");
    });
    expect(resolveQuality([], "auto")).toBe("auto");
    expect(resolveQuality(undefined, "auto")).toBe("auto");
  });

  it("survives the publisher ladder changing underneath it", () => {
    // Host swaps camera 1080p -> 4K -> 720p -> single-rendition screen share. Auto is not a
    // rendition, so nothing can make it stale and there is nothing to step down to.
    [THREE_LAYERS, FOUR_K_LADDER, SEVEN_TWENTY_LADDER, [{ quality: HIGH, width: 1920, height: 1080 }]]
      .forEach((layers) => {
        expect(resolveQuality(describeLayers(pubWith(layers)), "auto")).toBe("auto");
      });
  });

  it("is re-armed on the replacement publication after a reconnect", () => {
    // onSubscribed re-applies preferenceRef.current to whatever publication arrives next, and
    // a reconnect produces a NEW publication, so Auto has to be re-asserted on it.
    const before = pubWith(THREE_LAYERS);
    applyQuality(before, "auto");
    expect(before.setVideoQuality).toHaveBeenCalledWith(HIGH);

    const afterReconnect = pubWith(THREE_LAYERS);
    applyQuality(afterReconnect, "auto");
    expect(afterReconnect.setVideoQuality).toHaveBeenCalledWith(HIGH);
    expect(afterReconnect.setVideoDimensions).not.toHaveBeenCalled();
  });

  it("re-arms Auto even when the reconnect comes back on a different ladder", () => {
    const afterReconnect = pubWith(FOUR_K_LADDER);
    applyQuality(afterReconnect, "auto");

    expect(afterReconnect.setVideoQuality).toHaveBeenCalledWith(HIGH);
    expect(afterReconnect.setVideoDimensions).not.toHaveBeenCalled();
  });

  it("never sends a dimension request for Auto, on any ladder", () => {
    // Auto must not name a rendition — that is exactly what would cap it below what adaptive
    // streaming wants to give the viewer.
    LADDERS.forEach((layers) => {
      const pub = pubWith(layers);
      applyQuality(pub, "auto");
      expect(pub.setVideoDimensions).not.toHaveBeenCalled();
      expect(pub.setVideoQuality.mock.calls).toEqual([[HIGH]]);
    });
  });

  it("keeps the checkmark on Auto while the ladder changes beneath the menu", () => {
    // The requirement that the checkmark must NOT follow LiveKit's internal layer switching:
    // it is driven by the viewer's preference, which no track event touches.
    [THREE_LAYERS, FOUR_K_LADDER, SEVEN_TWENTY_LADDER].forEach((layers) => {
      mountQualityMenu({ quality: "auto", videoLayers: describeLayers(pubWith(layers)) });
      expect(checkedOn("Auto")).toBe(true);
      cleanup();
    });
  });

  it("reports Auto on the main settings row", () => {
    mountQualityMenu({ quality: "auto", videoLayers: LADDER, openQuality: false });
    expect(screen.getByRole("button", { name: /^Quality/ }).textContent).toContain("Auto");
  });
});

describe("the manual rungs are untouched by any of this", () => {
  it("still requests each rendition by its own real dimensions", () => {
    const cases = [
      [THREE_LAYERS, "1080p", { width: 1920, height: 1080 }],
      [THREE_LAYERS, "720p", { width: 1280, height: 720 }],
      [THREE_LAYERS, "360p", { width: 640, height: 360 }],
      [FOUR_K_LADDER, "2160p (4K)", { width: 3840, height: 2160 }],
      [SEVEN_TWENTY_LADDER, "720p", { width: 1280, height: 720 }],
    ];

    cases.forEach(([layers, label, expected]) => {
      const pub = pubWith(layers);
      applyQuality(pub, label);
      expect(pub.setVideoDimensions).toHaveBeenCalledWith(expected);
      expect(pub.setVideoQuality).not.toHaveBeenCalled();
    });
  });

  it("still steps a vanished rendition down rather than back to Auto", () => {
    expect(resolveQuality(describeLayers(pubWith(SEVEN_TWENTY_LADDER)), "2160p (4K)")).toBe("720p");
    expect(resolveQuality(describeLayers(pubWith(THREE_LAYERS)), "2160p (4K)")).toBe("1080p");
    expect(resolveQuality(describeLayers(pubWith(SEVEN_TWENTY_LADDER)), "1080p")).toBe("720p");
  });
});
