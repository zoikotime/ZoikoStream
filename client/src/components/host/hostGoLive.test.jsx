import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import ControlBar from "./ControlBar";
import { reducer, INITIAL_LIVE_STATE, pickBroadcastable } from "../../hooks/useLiveEvent";

// Regression cover for the two reported host-console failures — the Go Live button never
// becoming clickable, and the broadcast silently dropping out of "live". Both were traced to
// state that conflated distinct conditions; these lock in the distinctions.

const deck = (props = {}) =>
  render(
    <ControlBar
      broadcast={null}
      recording={null}
      canHost
      previewOn={false}
      camera
      mic
      screenShare={false}
      media={{}}
      onTogglePreview={vi.fn()}
      onToggleCamera={vi.fn()}
      onFlipCamera={vi.fn()}
      onToggleMic={vi.fn()}
      onToggleScreen={vi.fn()}
      onGoLive={vi.fn()}
      onPause={vi.fn()}
      onResume={vi.fn()}
      onEnd={vi.fn()}
      onEmergencyStop={vi.fn()}
      onCountdown={vi.fn()}
      onRecord={vi.fn()}
      onPauseRecord={vi.fn()}
      onStopRecord={vi.fn()}
      onInvite={vi.fn()}
      onSettings={vi.fn()}
      {...props}
    />
  );

const transport = () =>
  screen.getByRole("button", { name: /^(Go Live|Starting…|Connecting…|Pause|Ended)$/ });

describe("Go Live readiness", () => {
  // THE BUG: canHost starts false in the reducer's EMPTY state and only becomes true when
  // the opening snapshot lands. Until then the deck reported a REFUSAL it had no basis for —
  // an org_admin host sat looking at a disabled Go Live button whose tooltip said
  // "Only the event host can start or pause the broadcast". Measured at ~6s on a clean load,
  // and unbounded whenever the socket is slow to connect.
  it("says it is still connecting — not that the host lacks permission — before the snapshot lands", () => {
    deck({ ready: false, canHost: false });
    const btn = transport();
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("Connecting…");
    expect(btn.getAttribute("title")).toMatch(/connecting to the studio/i);
    expect(btn.getAttribute("title")).not.toMatch(/only the event host/i);
  });

  it("reports a real permission refusal once the console is in sync", () => {
    deck({ ready: true, canHost: false });
    const btn = transport();
    expect(btn).toBeDisabled();
    expect(btn.getAttribute("title")).toMatch(/only the event host/i);
  });

  it("becomes clickable as soon as the console is in sync and the user may host", () => {
    deck({ ready: true, canHost: true });
    const btn = transport();
    expect(btn).toBeEnabled();
    expect(btn).toHaveTextContent("Go Live");
  });

  it("distinguishes an in-flight go-live request from a not-yet-connected console", () => {
    deck({ ready: true, canHost: true, goLivePending: true });
    const btn = transport();
    expect(btn).toBeDisabled();
    expect(btn).toHaveTextContent("Starting…");
  });
});

describe("live-state reducer", () => {
  const snapshot = (over = {}) =>
    reducer(INITIAL_LIVE_STATE, {
      channel: "moderator", type: "snapshot",
      data: { can_host: true, broadcast: { status: "live" }, ...over },
    });

  // THE BUG: services/broadcast.py::_preview wrote status "preview" unconditionally and this
  // case hard-coded it, so arming a preview mid-broadcast flipped `live` false — which is
  // exactly what useLiveKitPublish gates on, so the publisher tore down and viewers lost
  // audio and video while the host still looked live.
  it("never demotes a live broadcast when a preview is armed", () => {
    const live = snapshot();
    expect(live.broadcast.status).toBe("live");
    const after = reducer(live, {
      channel: "broadcast", type: "broadcast.preview",
      data: { status: "live", settings: {}, publish_token: "t", livekit_url: "u" },
    });
    expect(after.broadcast.status).toBe("live");
  });

  it("still applies a preview to a broadcast that is not on air", () => {
    const idle = snapshot({ broadcast: { status: "preview" } });
    const after = reducer(idle, {
      channel: "broadcast", type: "broadcast.preview",
      data: { status: "preview", settings: { a: 1 } },
    });
    expect(after.broadcast.status).toBe("preview");
    expect(after.broadcast.settings).toEqual({ a: 1 });
  });

  it("resolves a pending Go Live when the broadcast actually moves", () => {
    const pending = reducer(snapshot(), { channel: "local", type: "golive.start" });
    expect(pending.goLivePending).toBe(true);
    const after = reducer(pending, {
      channel: "broadcast", type: "broadcast.update", data: { status: "live" },
    });
    expect(after.goLivePending).toBe(false);
  });

  it("resolves a pending Go Live when the server refuses it, carrying the real reason", () => {
    const pending = reducer(snapshot(), { channel: "local", type: "golive.start" });
    const after = reducer(pending, {
      channel: "host", type: "broadcast.error",
      data: { error: "Slow down — too many actions", code: "action_rejected" },
    });
    expect(after.goLivePending).toBe(false);
    expect(after.goLiveError.message).toBe("Slow down — too many actions");
  });
});

describe("host console event resolution", () => {
  // THE BUG: this asked the API for status "live" and took the first row — an exact match, so
  // it could only ever find an event that was ALREADY live, i.e. the one case where Go Live
  // is not needed. A host who had not gone live yet resolved nothing, and Dashboard
  // short-circuits a null resolution to "No event to broadcast", so the deck never mounted
  // and there was no Go Live button at all. /host/dashboard is the host role's own landing
  // page, so every host hit this on first login.
  it("resolves an event the host has not taken live yet", () => {
    expect(pickBroadcastable([{ id: "e1", status: "published" }])?.id).toBe("e1");
    expect(pickBroadcastable([{ id: "e2", status: "scheduled" }])?.id).toBe("e2");
  });

  it("prefers an event already on air over one merely scheduled", () => {
    const picked = pickBroadcastable([
      { id: "scheduled", status: "scheduled" },
      { id: "onair", status: "live" },
    ]);
    expect(picked.id).toBe("onair");
  });

  it("prefers an armed event over a merely published one", () => {
    const picked = pickBroadcastable([
      { id: "published", status: "published" },
      { id: "armed", status: "armed" },
    ]);
    expect(picked.id).toBe("armed");
  });

  it("ignores events that can never be broadcast", () => {
    expect(pickBroadcastable([
      { id: "d", status: "draft" }, { id: "e", status: "ended" }, { id: "c", status: "cancelled" },
    ])).toBeNull();
    expect(pickBroadcastable([])).toBeNull();
  });
});
