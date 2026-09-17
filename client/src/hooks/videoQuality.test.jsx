// Manual video quality: the menu offers only renditions that actually exist, and choosing
// one really changes this viewer's LiveKit subscription.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// The Quality menu was a hardcoded array — ["Auto","1080p","720p","480p","360p"] — with
// `disabled={quality !== "Auto"}` written as a constant and the checkmark pinned to "Auto".
// Its onClick only closed the menu. It had never been connected to LiveKit at all.
//
// It was NOT a simulcast problem: livekit-client publishes with simulcast:true by default,
// so the host was already sending layers. Nothing asked for them. Note also that LiveKit
// simulcast is THREE levels (VideoQuality LOW/MEDIUM/HIGH), so the four-option list could
// never have mapped onto real layers even if it had been wired up.
//
// These exercise the real exported helpers rather than a copy of them.
import { describe, expect, it, vi } from "vitest";

import { applyQuality, describeLayers } from "./useLiveKitViewer";

// VideoQuality from livekit-client: LOW 0, MEDIUM 1, HIGH 2.
const LOW = 0;
const MEDIUM = 1;
const HIGH = 2;

const pubWith = (layers) => ({
  trackInfo: layers ? { layers } : undefined,
  setVideoQuality: vi.fn(),
});

// What a 1080p camera really yields under LiveKit simulcast.
const THREE_LAYERS = [
  { quality: HIGH, width: 1920, height: 1080 },
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
    // A 720p webcam yields 720/360/180. The old fixed list would have shown 1080p and 480p
    // with nothing behind either — exactly the "upscale and call it 1080p" failure.
    const labels = describeLayers(pubWith([
      { quality: HIGH, width: 1280, height: 720 },
      { quality: MEDIUM, width: 640, height: 360 },
      { quality: LOW, width: 320, height: 180 },
    ])).map((l) => l.label);

    expect(labels).toEqual(["720p", "360p", "180p"]);
    expect(labels).not.toContain("1080p");
    expect(labels).not.toContain("480p");
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
  it("requests the matching layer, not a CSS resize", () => {
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, MEDIUM);            // the 720p row
    expect(pub.setVideoQuality).toHaveBeenCalledWith(MEDIUM);
  });

  it("requests the low layer for the lowest rendition", () => {
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, LOW);
    expect(pub.setVideoQuality).toHaveBeenCalledWith(LOW);
  });

  it("restores adaptive selection for Auto", () => {
    // setVideoQuality sets requestedMaxQuality — a CAP, not a pin. Capping at HIGH is no
    // cap at all, which hands the choice back to LiveKit's adaptive/dynacast selection.
    const pub = pubWith(THREE_LAYERS);
    applyQuality(pub, "auto");
    expect(pub.setVideoQuality).toHaveBeenCalledWith(HIGH);
  });

  it("is inert when there is no publication to act on", () => {
    expect(() => applyQuality(null, MEDIUM)).not.toThrow();
    expect(() => applyQuality({}, MEDIUM)).not.toThrow();
  });

  it("survives a publication that refuses the call", () => {
    // LiveKit's isManualOperationAllowed() returns false once a track is no longer
    // subscribed. The next subscribe re-applies the preference, so this must not throw.
    const pub = {
      trackInfo: { layers: THREE_LAYERS },
      setVideoQuality: vi.fn(() => { throw new Error("not subscribed"); }),
    };
    expect(() => applyQuality(pub, MEDIUM)).not.toThrow();
  });
});

describe("one viewer's choice is only their own", () => {
  it("acts on that viewer's publication and nothing else", () => {
    // Two browsers, two subscriptions. setVideoQuality is a per-subscription request, so
    // viewer A picking 360p cannot alter what viewer B receives — nor what the host sends.
    const viewerA = pubWith(THREE_LAYERS);
    const viewerB = pubWith(THREE_LAYERS);

    applyQuality(viewerA, LOW);

    expect(viewerA.setVideoQuality).toHaveBeenCalledWith(LOW);
    expect(viewerB.setVideoQuality).not.toHaveBeenCalled();
  });
});

describe("the publication can change underneath the control", () => {
  it("re-applies the standing preference to a replacement publication", () => {
    // Host turns the camera off and on, swaps device, or the room reconnects: a NEW
    // publication arrives and the viewer's choice has to follow it rather than stay bound
    // to the old one. This is what onSubscribed does with preferenceRef.
    const preference = MEDIUM;

    const first = pubWith(THREE_LAYERS);
    applyQuality(first, preference);
    expect(first.setVideoQuality).toHaveBeenCalledWith(MEDIUM);

    const replacement = pubWith(THREE_LAYERS);
    applyQuality(replacement, preference);
    expect(replacement.setVideoQuality).toHaveBeenCalledWith(MEDIUM);
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
