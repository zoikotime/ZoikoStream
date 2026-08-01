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

export default function useMediaPreview({ enabled, camera, mic, settings }) {
  const videoRef = useRef(null);
  const streamRef = useRef(null);
  const [devices, setDevices] = useState({ cameras: [], mics: [], speakers: [] });
  const [picked, setPicked] = useState({ camera: null, mic: null });
  const [actual, setActual] = useState(null);   // what the hardware really gave us
  // Unsupported is knowable at init, so start in that state instead of setting it from an
  // effect (which would render once claiming everything is fine).
  const [error, setError] = useState(() =>
    supported() ? null : "This browser can't access camera or microphone (getUserMedia unavailable).");
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
    if (videoRef.current) videoRef.current.srcObject = null;
    setActive(false);
    setActual(null);
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
        const stream = await navigator.mediaDevices.getUserMedia({
          video: videoConstraints(cfg.current, picked.camera),
          audio: audioConstraints(cfg.current, picked.mic),
        });
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        streamRef.current = stream;
        if (videoRef.current) videoRef.current.srcObject = stream;
        setError(null);
        setActive(true);
        readActual();
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
        if (!cancelled) {
          setActive(false);
          setError(
            e?.name === "NotAllowedError"
              ? "Camera and microphone access was blocked. Allow it in the browser to preview."
              : e?.name === "NotFoundError"
                ? "No camera or microphone found on this device."
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
  useEffect(() => {
    const stream = streamRef.current;
    if (!stream || !active) return;
    const apply = async () => {
      try {
        await stream.getVideoTracks()[0]?.applyConstraints(videoConstraints(settings, picked.camera));
        await stream.getAudioTracks()[0]?.applyConstraints(audioConstraints(settings, picked.mic));
        readActual();
      } catch {
        // A camera that can't hit the requested mode keeps its current one; `actual`
        // continues to report the truth, so nothing needs to be undone here.
        readActual();
      }
    };
    apply();
  }, [settings.resolution, settings.framerate, settings.echo_cancellation,
      settings.noise_cancellation, settings.auto_gain, active, picked.camera, picked.mic,
      readActual, settings]);

  // Monitor volume is a property of the preview element, not the stream.
  useEffect(() => {
    if (videoRef.current) videoRef.current.volume = Math.min(1, (settings.speaker_volume ?? 100) / 100);
  }, [settings.speaker_volume, active]);

  return {
    videoRef,
    // Exposed so useLiveKitPublish can grab the CURRENT tracks and publish them, instead of
    // acquiring a second, competing getUserMedia stream just to broadcast. A ref (not the
    // MediaStream itself) so reading it doesn't force this hook's consumers to re-render.
    streamRef,
    devices,
    picked,
    actual,
    error,
    active,
    selectCamera: (deviceId) => setPicked((p) => ({ ...p, camera: deviceId || null })),
    selectMic: (deviceId) => setPicked((p) => ({ ...p, mic: deviceId || null })),
  };
}
