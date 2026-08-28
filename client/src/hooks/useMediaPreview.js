import { useCallback, useEffect, useRef, useState } from "react";

// Real camera/mic preview for the host studio, on the native platform APIs — no SDK.
//
// This matters for honesty as much as for weight: `getUserMedia` reports what the hardware
// ACTUALLY granted (`track.getSettings()`), so the studio shows measured resolution and
// frame rate instead of echoing back the resolution the host asked for. Requested vs
// applied are surfaced separately, because a webcam asked for 4K that gives you 1080p is
// exactly the thing a producer needs to see before going live.
//
// Also genuinely native, no library required: echo cancellation, noise suppression and
// auto gain are standard MediaTrackConstraints, and switching device applies without
// re-acquiring the stream.
//
// ponytail: what is NOT here — publishing to LiveKit (needs livekit-client) and background
// blur / virtual background (needs a segmentation model). Both are reported as unavailable
// rather than shown as toggles that quietly do nothing.

export const RESOLUTIONS = {
  "720p": { width: 1280, height: 720 },
  "1080p": { width: 1920, height: 1080 },
  "2k": { width: 2560, height: 1440 },
  "4k": { width: 3840, height: 2160 },
};

export const supported = () =>
  typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia;

const videoConstraints = (settings, deviceId) => {
  const res = RESOLUTIONS[settings.resolution] || RESOLUTIONS["1080p"];
  return {
    // `ideal`, not `exact`: a camera that can't do 4K should downscale, not fail outright.
    width: { ideal: res.width },
    height: { ideal: res.height },
    frameRate: { ideal: settings.framerate || 30 },
    ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
  };
};

const audioConstraints = (settings, deviceId) => ({
  echoCancellation: settings.echo_cancellation !== false,
  noiseSuppression: settings.noise_cancellation !== false,
  autoGainControl: settings.auto_gain !== false,
  ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
});

// Acquires the best stream the hardware can actually give: both kinds if possible, otherwise
// whichever one works. Returns the stream, or throws the ORIGINAL both-kinds error when
// neither kind can be opened — that first error is the one that describes the real problem
// (blocked permission, no devices at all), whereas a fallback's error is a downstream symptom.
//
// NotAllowedError is deliberately still retried per-kind: a host can grant the microphone and
// deny the camera (or have the camera blocked at the OS level while audio is fine), and that
// combination must yield a working audio-only broadcast rather than nothing.
async function acquireWithFallback(cfg, cameraId, micId) {
  const video = () => videoConstraints(cfg, cameraId);
  const audio = () => audioConstraints(cfg, micId);
  try {
    return await navigator.mediaDevices.getUserMedia({ video: video(), audio: audio() });
  } catch (bothFailed) {
    for (const constraints of [{ video: false, audio: audio() }, { video: video(), audio: false }]) {
      try {
        return await navigator.mediaDevices.getUserMedia(constraints);
      } catch {
        // Try the other single-kind request before giving up.
      }
    }
    throw bothFailed;
  }
}

export default function useMediaPreview({ enabled, camera, mic, settings }) {
  // A callback ref, not a plain useRef: some consumers (e.g. the contributor Backstage
  // preflight screen) only mount the <video> tag once a *different* async condition settles
  // (their join-window/state snapshot), which can resolve AFTER acquisition below already
  // ran and tried to attach the stream to a ref that was still null at that moment — a real
  // bug found by an E2E run, not a hypothetical: acquisition succeeded (mic device labels
  // populated) but the video element stayed permanently blank because the assignment had
  // already silently no-op'd. A callback ref re-attaches the live stream the instant the
  // node actually mounts, regardless of which happens first.
  const streamRef = useRef(null);
  const videoElRef = useRef(null);
  const videoRef = useCallback((node) => {
    videoElRef.current = node;
    if (node) node.srcObject = streamRef.current;
  }, []);
  const [devices, setDevices] = useState({ cameras: [], mics: [], speakers: [] });
  const [picked, setPicked] = useState({ camera: null, mic: null });
  const [actual, setActual] = useState(null);   // what the hardware really gave us
  // Identity of the currently-acquired video track. Changes on every re-acquire (device
  // switch via flipCamera/selectCamera) — unlike mute (which flips `.enabled` on the SAME
  // track), a flip tears down and reacquires a whole new MediaStream/track. Exposed as
  // state (not just read off streamRef) so useLiveKitPublish can detect the swap and
  // republish; a ref wouldn't trigger its effect.
  const [videoTrack, setVideoTrack] = useState(null);
  // Unsupported is knowable at init, so start in that state instead of setting it from an
  // effect (which would render once claiming everything is fine).
  const [error, setError] = useState(() => {
    if (supported()) return null;
    // Distinguish "this browser cannot" from "this ORIGIN cannot", which is by far the more
    // common cause and is fixable: navigator.mediaDevices is undefined outside a secure
    // context, so opening the console over plain http on a LAN address (a normal thing to do
    // when testing from another machine) removes the API entirely. Blaming the browser sent
    // hosts hunting through settings that were never the problem.
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      return "Camera and microphone need a secure connection. Open this console over https:// "
        + "(or on localhost) and try again.";
    }
    return "This browser can't access camera or microphone (getUserMedia unavailable).";
  });
  const [active, setActive] = useState(false);

  // Latest settings without restarting acquisition on every keystroke in the settings panel.
  const cfg = useRef(settings);
  useEffect(() => {
    cfg.current = settings;
  });

  const readActual = useCallback(() => {
    const stream = streamRef.current;
    if (!stream) return;
    const video = stream.getVideoTracks()[0];
    const audio = stream.getAudioTracks()[0];
    const v = video?.getSettings?.() || {};
    setActual({
      width: v.width ?? null,
      height: v.height ?? null,
      frameRate: v.frameRate ? Math.round(v.frameRate) : null,
      cameraLabel: video?.label || null,
      micLabel: audio?.label || null,
      // Reported by the browser, so it reflects what was actually applied.
      echoCancellation: audio?.getSettings?.().echoCancellation ?? null,
      noiseSuppression: audio?.getSettings?.().noiseSuppression ?? null,
      autoGainControl: audio?.getSettings?.().autoGainControl ?? null,
    });
  }, []);

  const stop = useCallback(() => {
    streamRef.current?.getTracks().forEach((t) => t.stop());   // releases the camera light
    streamRef.current = null;
    if (videoElRef.current) videoElRef.current.srcObject = null;
    // Clear the previous failure too. A stale error outlived the preview it belonged to and
    // kept the stage on "Camera unavailable" — which is the one branch that renders NO retry
    // affordance — so a host whose first attempt failed (camera busy, permission dismissed)
    // had no way back to "Start preview" short of reloading the page. The next attempt sets
    // its own error if it fails again.
    setError(null);
    setActive(false);
    setActual(null);
    setVideoTrack(null);
  }, []);

  // Every acquisition attempt chains onto the previous one instead of firing independently.
  // Two `getUserMedia()` calls for the same camera racing each other (StrictMode's dev-only
  // double-invoke: mount -> cleanup -> mount, fired before the first call has even resolved
  // so cleanup's stop() has nothing to stop yet; the same race is reachable in production
  // from a fast preview off/on toggle) commonly fails one of them with
  // NotReadableError("Device in use") on Windows/Chrome. Chaining guarantees attempt N+1's
  // getUserMedia() only starts after attempt N has fully settled — including stopping its
  // own stream if it was cancelled in the meantime — so there is never a second in-flight
  // request for the same device.
  const acquireRef = useRef(Promise.resolve());

  // Acquire once, then adjust in place. Re-running on every toggle would flash the camera
  // light and re-prompt on some browsers.
  // Turning the preview off is handled by this effect's CLEANUP (below), not by an early
  // stop() call — that keeps teardown in one place and off the render path.
  useEffect(() => {
    if (!enabled || !supported()) return undefined;

    let cancelled = false;
    const task = acquireRef.current.catch(() => {}).then(async () => {
      if (cancelled) return;
      try {
        // getUserMedia is ALL-OR-NOTHING: ask for video+audio and a host whose webcam is
        // missing, blocked or already in use by another app gets NEITHER — the whole promise
        // rejects and they cannot broadcast at all, microphone included. Falling back to one
        // kind is what makes an audio-only broadcast possible on real hardware, and the rest
        // of the stack already supports it: useLiveKitPublish publishes whichever tracks the
        // stream actually has, and VideoPlayer renders an audio-only stream rather than
        // sitting on the camera placeholder. Fake devices always supply both, which is why
        // this never surfaced in automation.
        const stream = await acquireWithFallback(cfg.current, picked.camera, picked.mic);
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoElRef.current) videoElRef.current.srcObject = stream;
        setError(null);
        setActive(true);
        setVideoTrack(stream.getVideoTracks()[0] || null);
        readActual();

        // A track whose SOURCE dies — webcam unplugged, OS privacy switch flipped, another
        // app seizing the device, permission revoked mid-broadcast — fires "ended". Without
        // this the hook never learned: `active` stayed true, so the stage kept the "On air"
        // chip and the green publishing banner over a frozen last frame with no explanation
        // and no way back short of reloading the page. Note stop() below uses track.stop(),
        // which by spec does NOT fire "ended", so a deliberate teardown can't trip this.
        const onEnded = () => {
          if (cancelled) return;
          setActive(false);
          setVideoTrack(null);
          setError("Your camera or microphone stopped — check the device is still connected "
            + "and available, then start the preview again.");
        };
        stream.getTracks().forEach((t) => t.addEventListener("ended", onEnded));
        // Labels are only exposed after permission is granted, so enumerate now.
        const all = await navigator.mediaDevices.enumerateDevices();
        if (!cancelled) {
          setDevices({
            cameras: all.filter((d) => d.kind === "videoinput"),
            mics: all.filter((d) => d.kind === "audioinput"),
            speakers: all.filter((d) => d.kind === "audiooutput"),
          });
        }
      } catch (e) {
        // A previously chosen device that has gone away (unplugged, or claimed by another
        // app) is pinned by deviceId:{exact}, so every retry fails the same way forever and
        // the only escape was reloading the page. Drop the dead pick and let the effect
        // re-run against the system default — the one recovery the host cannot perform
        // themselves, since the host console exposes no device picker.
        if (!cancelled && (picked.camera || picked.mic)
            && (e?.name === "OverconstrainedError" || e?.name === "NotFoundError")) {
          setPicked({ camera: null, mic: null });
          return;
        }
        if (!cancelled) {
          setActive(false);
          // Reached only when NEITHER kind could be opened (see acquireWithFallback), so
          // naming both devices here is accurate rather than the guess it used to be.
          setError(
            e?.name === "NotAllowedError"
              ? "Camera and microphone access was blocked. Allow it in the browser, then start the preview again."
              : e?.name === "NotFoundError"
                ? "No camera or microphone found on this device."
                : e?.name === "NotReadableError" || e?.name === "TrackStartError"
                  ? "Your camera and microphone are in use by another app. Close it, then start the preview again."
                  : `Couldn't start the preview: ${e?.message || e?.name || "unknown error"}`
          );
        }
      }
    });
    acquireRef.current = task;

    return () => {
      cancelled = true;
      stop();
    };
  }, [enabled, picked.camera, picked.mic, readActual, stop]);

  // Toggling mute/camera disables the TRACK rather than dropping the stream — instant, and
  // it keeps the negotiated settings.
  useEffect(() => {
    streamRef.current?.getVideoTracks().forEach((t) => { t.enabled = !!camera; });
  }, [camera, active]);

  useEffect(() => {
    streamRef.current?.getAudioTracks().forEach((t) => { t.enabled = !!mic; });
  }, [mic, active]);

  // Resolution / frame rate / audio processing changes apply to the live track.
  //
  // Keyed on the five capture values themselves, NOT on the `settings` object. That object is
  // rebuilt on every broadcast update (Dashboard memoises it over state.broadcast), so having
  // it in the dependency list meant every unrelated setting — chat, slow mode, waiting room,
  // recording quality — reconfigured the host's live camera and microphone mid-broadcast.
  // applyConstraints on a real device is a genuine renegotiation, not a no-op: it can stall
  // or drop frames, and a settings panel with range sliders can fire it many times a second.
  // Fake devices absorb it silently, which is why only real hardware showed the damage.
  useEffect(() => {
    const stream = streamRef.current;
    if (!stream || !active) return;
    const apply = async () => {
      // Audio is applied even when video's constraints are rejected. Awaiting them in one
      // try block meant a camera that could not hit the requested mode skipped the audio
      // constraints entirely, so echo cancellation / noise suppression / auto gain silently
      // never applied — on the very hardware most likely to need them.
      try {
        await stream.getVideoTracks()[0]?.applyConstraints(videoConstraints(cfg.current, picked.camera));
      } catch {
        // A camera that can't hit the requested mode keeps its current one; `actual`
        // continues to report the truth, so nothing needs to be undone here.
      }
      try {
        await stream.getAudioTracks()[0]?.applyConstraints(audioConstraints(cfg.current, picked.mic));
      } catch {
        // Same contract as video above: the track keeps whatever it already had.
      }
      readActual();
    };
    apply();
  }, [settings.resolution, settings.framerate, settings.echo_cancellation,
      settings.noise_cancellation, settings.auto_gain, active, picked.camera, picked.mic,
      readActual]);

  // Monitor volume is a property of the preview element, not the stream.
  useEffect(() => {
    if (videoElRef.current) videoElRef.current.volume = Math.min(1, (settings.speaker_volume ?? 100) / 100);
  }, [settings.speaker_volume, active]);

  // The capture's real phase, rather than making every consumer re-derive it from two
  // booleans and get the middle state wrong. `active` alone cannot express "the request is
  // in flight": while the browser's permission prompt is on screen, enabled is true but
  // active is still false and error is still null — and a UI that reads only `active` calls
  // that "off", which is what it looks like when nothing was ever requested. Those are
  // different states with different correct actions, so they get different names here.
  //
  // Derived, not stored: an extra useState would have to be cleared on every success, every
  // failure and every cancellation path, and any missed one leaves a permanently wrong
  // phase. This cannot desync because it is computed from the same values it describes.
  const phase = error ? "error" : active ? "ready" : enabled ? "acquiring" : "idle";

  return {
    videoRef,
    // Exposed so useLiveKitPublish can grab the CURRENT tracks and publish them, instead of
    // acquiring a second, competing getUserMedia stream just to broadcast. A ref (not the
    // MediaStream itself) so reading it doesn't force this hook's consumers to re-render.
    streamRef,
    videoTrack,
    devices,
    picked,
    actual,
    error,
    active,
    phase,
    flipCamera: async () => {
  const stream = streamRef.current;
  const currentTrack = stream?.getVideoTracks?.()[0];

  if (!currentTrack) return;

    const currentDeviceId = currentTrack.getSettings?.().deviceId;

    // Mobile browsers normally expose front/back cameras as separate videoinput devices.
    const cameras = devices.cameras || [];

    if (cameras.length < 2) {
      setError("No second camera is available on this device.");
      return;
    }

    const currentIndex = cameras.findIndex(
      (device) => device.deviceId === currentDeviceId
    );

    const nextCamera =
      cameras[(currentIndex >= 0 ? currentIndex + 1 : 0) % cameras.length];

    if (!nextCamera?.deviceId) return;

    setPicked((p) => ({ ...p, camera: nextCamera.deviceId }));
  },
    selectCamera: (deviceId) => setPicked((p) => ({ ...p, camera: deviceId || null })),
    selectMic: (deviceId) => setPicked((p) => ({ ...p, mic: deviceId || null })),
  };
}
