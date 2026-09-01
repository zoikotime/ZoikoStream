import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { useEffect, useState } from "react";

import useMediaPreview from "./useMediaPreview";

function fakeTrack() {
  const listeners = {};
  return {
    stop: vi.fn(),
    enabled: true,
    readyState: "live",
    getSettings: () => ({}),
    applyConstraints: vi.fn().mockResolvedValue(undefined),
    // The hook subscribes to "ended" on every acquired track so a device that goes away
    // mid-preview is noticed; a stand-in without these would fail acquisition outright.
    addEventListener: (ev, fn) => { listeners[ev] = fn; },
    removeEventListener: (ev) => { delete listeners[ev]; },
    emit: (ev) => listeners[ev]?.(),
  };
}

function fakeStream() {
  const tracks = [fakeTrack()];
  return {
    getTracks: () => tracks,
    getVideoTracks: () => tracks,
    getAudioTracks: () => tracks,
  };
}

// Mounts the <video> element on a delay — reproducing client/src/pages/speaker/Backstage.jsx,
// which only renders <video ref={videoRef}> once its join-window snapshot arrives, a
// condition unrelated to (and often later than) `enabled` flipping true here.
function DelayedVideoHarness({ mountDelayMs }) {
  const [mounted, setMounted] = useState(false);
  const { videoRef } = useMediaPreview({ enabled: true, camera: true, mic: true, settings: {} });
  useEffect(() => {
    const t = setTimeout(() => setMounted(true), mountDelayMs);
    return () => clearTimeout(t);
  }, [mountDelayMs]);
  return mounted ? <video ref={videoRef} data-testid="preview" /> : null;
}

function ImmediateVideoHarness() {
  const { videoRef } = useMediaPreview({ enabled: true, camera: true, mic: true, settings: {} });
  return <video ref={videoRef} data-testid="preview" />;
}

// Reports the hook's phase so the acquiring window is assertable on its own.
function PhaseHarness({ enabled }) {
  const { videoRef, phase, error } = useMediaPreview({
    enabled, camera: true, mic: true, settings: {},
  });
  return (
    <>
      <span data-testid="phase">{phase}</span>
      <span data-testid="error">{error || ""}</span>
      <video ref={videoRef} />
    </>
  );
}

describe("useMediaPreview", () => {
  let stream;

  beforeEach(() => {
    stream = fakeStream();
    navigator.mediaDevices = {
      getUserMedia: vi.fn().mockResolvedValue(stream),
      enumerateDevices: vi.fn().mockResolvedValue([]),
    };
  });

  // Regression test: a real E2E run against the contributor Backstage preflight screen found
  // that camera/mic acquisition succeeded (device labels populated, no error shown) but the
  // visible preview stayed permanently blank. Root cause: the hook assigned
  // `videoRef.current.srcObject` only inside the acquisition effect; on Backstage the <video>
  // tag mounts later (gated on a separate async condition), so `videoRef.current` was still
  // null when acquisition finished and the assignment silently no-op'd, with nothing to
  // re-trigger it once the element actually appeared.
  it("attaches the stream once the <video> element mounts, even when that happens after getUserMedia resolves", async () => {
    render(<DelayedVideoHarness mountDelayMs={30} />);

    const video = await screen.findByTestId("preview");
    await waitFor(() => expect(video.srcObject).toBe(stream));
  });

  it("still attaches immediately when the <video> element is mounted from the start", async () => {
    render(<ImmediateVideoHarness />);

    const video = await screen.findByTestId("preview");
    await waitFor(() => expect(video.srcObject).toBe(stream));
  });

  // THE BUG these cover: the hook exposed only `active`, so "the permission prompt is on
  // screen" was indistinguishable from "preview was never started". The studio rendered that
  // shared state as "Preview is off" WITH a "Start preview" button wired to the toggle — so a
  // real host, told their preview was off, clicked it and turned their own pending request
  // OFF. Their camera and mic then never came on. Fake devices auto-grant in ~0ms, so only
  // real hardware ever sat in this window long enough to hit it.
  describe("phase", () => {
    it("is idle before the preview is armed", async () => {
      render(<PhaseHarness enabled={false} />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("idle"));
    });

    it("is acquiring while getUserMedia is still pending, not idle", async () => {
      let release;
      navigator.mediaDevices.getUserMedia = vi.fn(
        () => new Promise((res) => { release = () => res(stream); })
      );
      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("acquiring"));
      expect(screen.getByTestId("error")).toHaveTextContent("");

      release();
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("ready"));
    });

    it("is ready once tracks arrive", async () => {
      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("ready"));
    });

    it("is error, with the real reason, when the host blocks access", async () => {
      const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
      navigator.mediaDevices.getUserMedia = vi.fn().mockRejectedValue(denied);
      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("error"));
      expect(screen.getByTestId("error")).toHaveTextContent(/access was blocked/i);
    });

    it("is error when no camera or microphone exists", async () => {
      const missing = Object.assign(new Error("none"), { name: "NotFoundError" });
      navigator.mediaDevices.getUserMedia = vi.fn().mockRejectedValue(missing);
      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("error"));
      expect(screen.getByTestId("error")).toHaveTextContent(/no camera or microphone/i);
    });

    // THE BUG: getUserMedia is all-or-nothing. Asking for video+audio meant a host whose
    // webcam was missing, blocked or busy got NEITHER — the whole promise rejected and they
    // could not broadcast at all, microphone included. Fake devices always supply both, so
    // automation never saw it.
    it("falls back to audio only when the camera cannot be opened", async () => {
      const audioTrack = fakeTrack();
      const audioOnly = {
        getTracks: () => [audioTrack],
        getVideoTracks: () => [],
        getAudioTracks: () => [audioTrack],
      };
      navigator.mediaDevices.getUserMedia = vi.fn(async (c) => {
        if (c.video) throw Object.assign(new Error("no cam"), { name: "NotFoundError" });
        return audioOnly;
      });

      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("ready"));
      expect(screen.getByTestId("error")).toHaveTextContent("");
      // Asked for both first, then retried without video.
      expect(navigator.mediaDevices.getUserMedia.mock.calls[0][0].video).toBeTruthy();
      expect(navigator.mediaDevices.getUserMedia.mock.calls[1][0].video).toBe(false);
    });

    it("falls back to video only when the microphone cannot be opened", async () => {
      const videoTrack = fakeTrack();
      const videoOnly = {
        getTracks: () => [videoTrack],
        getVideoTracks: () => [videoTrack],
        getAudioTracks: () => [],
      };
      navigator.mediaDevices.getUserMedia = vi.fn(async (c) => {
        if (c.audio) throw Object.assign(new Error("no mic"), { name: "NotFoundError" });
        return videoOnly;
      });

      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("ready"));
      expect(screen.getByTestId("error")).toHaveTextContent("");
    });

    it("reports the original failure when neither camera nor microphone can be opened", async () => {
      navigator.mediaDevices.getUserMedia = vi.fn().mockRejectedValue(
        Object.assign(new Error("blocked"), { name: "NotAllowedError" })
      );
      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("error"));
      expect(screen.getByTestId("error")).toHaveTextContent(/access was blocked/i);
      // Both single-kind fallbacks were attempted before giving up.
      expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledTimes(3);
    });

    // THE BUG: nothing listened for a track whose SOURCE dies (webcam unplugged, OS privacy
    // switch, permission revoked mid-broadcast). `active` stayed true forever, so the stage
    // kept the "On air" chip and the green publishing banner over a frozen frame, with no
    // explanation and no recovery short of a page reload.
    it("drops out of ready with a recoverable message when a device stops mid-preview", async () => {
      const track = fakeTrack();
      navigator.mediaDevices.getUserMedia = vi.fn().mockResolvedValue({
        getTracks: () => [track],
        getVideoTracks: () => [track],
        getAudioTracks: () => [track],
      });

      render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("ready"));

      track.emit("ended");   // the device went away
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("error"));
      expect(screen.getByTestId("error")).toHaveTextContent(/camera or microphone stopped/i);
    });

    // A stale error used to outlive the preview it belonged to, and "Camera unavailable" is
    // the one stage branch with NO retry affordance — so a host whose first attempt failed
    // had no way back to "Start preview" short of reloading the page.
    it("clears a previous failure when the preview is turned off, so it can be retried", async () => {
      const busy = Object.assign(new Error("busy"), { name: "NotReadableError" });
      navigator.mediaDevices.getUserMedia = vi.fn().mockRejectedValue(busy);
      const { rerender } = render(<PhaseHarness enabled />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("error"));

      rerender(<PhaseHarness enabled={false} />);
      await waitFor(() => expect(screen.getByTestId("phase")).toHaveTextContent("idle"));
      expect(screen.getByTestId("error")).toHaveTextContent("");
    });
  });
});
