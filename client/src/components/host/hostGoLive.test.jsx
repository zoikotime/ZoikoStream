import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import ControlBar from "./ControlBar";
import { reducer, INITIAL_LIVE_STATE } from "../../hooks/useLiveEvent";

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
  // The console no longer resolves an event by itself, and that is the fix.
  //
  // It used to: `pickBroadcastable` chose the highest-ranked broadcastable event from the
  // organization's list whenever the URL carried no ?event=<id>. Combined with
  // /host/dashboard being the host ACCOUNT role's landing page, that meant every
  // host-persona login opened the Producer Console attached to an arbitrary event — one the
  // user usually had no EventAssignment for, so the backend refused broadcast control and
  // the console rendered "No host assigned" / "View only" / "You aren't assigned to run this
  // event."
  //
  // An account role is not an event assignment. The event id now always comes from a person
  // choosing one (/events/mine) or from an assignment link, so there is nothing left to
  // guess and no helper to guess with.
  it("no longer exports a way to substitute an event", async () => {
    const hook = await import("../../hooks/useLiveEvent");
    expect(hook.pickBroadcastable).toBeUndefined();
  });

  it("keeps no ranking table that could reintroduce the guess", async () => {
    const { readFileSync } = await import("node:fs");
    const { resolve } = await import("node:path");
    // Same convention as the other static guards in this repo (Contact.static.test.js).
    const source = readFileSync(resolve(process.cwd(), "src/hooks/useLiveEvent.js"), "utf8");
    const code = source
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "");
    expect(code).not.toContain("BROADCASTABLE");
    // And it must not go looking for the organization's events to pick from: the hook
    // imports no api client at all any more.
    expect(code).not.toContain("from \"../api\"");
    expect(code).not.toContain("/events");
  });
});
