import { useCallback, useEffect, useRef, useState } from "react";
import api from "../api";

// "Before you go live" — the host's pre-flight, the publisher-side counterpart to
// hooks/usePlaybackCheck.js (which is the VIEWER's). Same rule as that file, and it is the
// whole reason this exists: every check MEASURES something. A pre-flight that always reports
// green is worse than none, because the host whose microphone is muted at the OS level is told
// they are ready and then broadcasts silence.
//
// This deliberately acquires its OWN short-lived camera/mic and releases it when done, rather
// than reading useMediaPreview's stream. Two reasons: the check must be runnable before the
// preview is started (that is the point of a pre-flight), and holding a second handle on the
// same device while the preview owns one is how you get a black frame on the machines that
// only allow one consumer.
//
// Deliberately NOT checked: CPU. No browser exposes process or system CPU to a page, so the
// header reports measured UI frame budget instead and labels it as such (see useSystemStats).

const STATE = { OK: "ok", WARN: "warn", FAIL: "fail", UNKNOWN: "unknown", CHECKING: "checking" };

// Round-trip thresholds (ms) to our own API. A publisher is more sensitive than a viewer: it
// is the one whose frames have to arrive on time, so these are tighter than the viewer's
// 150/400 in usePlaybackCheck.
const RTT_GOOD = 120;
const RTT_FAIR = 300;
const PROBES = 3;

// Below this, 720p at a usable frame rate is not going to hold up.
const UPLINK_MIN_MBPS = 2;

/** Median RTT to our own API. Median, not mean: one GC pause must not downgrade a good link. */
async function measureRtt() {
  const samples = [];
  for (let i = 0; i < PROBES; i += 1) {
    const t0 = performance.now();
    try {
      await api.get("/health", { params: { _: `${t0}` } });
      samples.push(performance.now() - t0);
    } catch {
      return null;
    }
  }
  return samples.sort((a, b) => a - b)[Math.floor(samples.length / 2)];
}

/** Permissions, where the browser will tell us without prompting. Firefox and Safari do not
 *  implement navigator.permissions for camera/microphone, so "unknown" is a real answer. */
async function checkPermissions() {
  if (!navigator.permissions?.query) {
    return { state: STATE.UNKNOWN, detail: "not reported by this browser" };
  }
  const results = {};
  for (const name of ["camera", "microphone"]) {
    try {
      results[name] = (await navigator.permissions.query({ name })).state;
    } catch {
      results[name] = "unknown";
    }
  }
  const denied = Object.entries(results).filter(([, s]) => s === "denied").map(([k]) => k);
  if (denied.length) {
    return { state: STATE.FAIL, detail: `${denied.join(" and ")} blocked in the browser` };
  }
  if (Object.values(results).every((s) => s === "granted")) {
    return { state: STATE.OK, detail: "camera and microphone allowed" };
  }
  return { state: STATE.WARN, detail: "you'll be prompted when the preview starts" };
}

/** Secure context + a media stack at all. Everything else is pointless without this. */
function checkEnvironment() {
  if (!window.isSecureContext) {
    return { state: STATE.FAIL, detail: "needs a secure (https) connection" };
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    return { state: STATE.FAIL, detail: "this browser cannot capture camera or microphone" };
  }
  if (typeof RTCPeerConnection === "undefined") {
    return { state: STATE.FAIL, detail: "WebRTC unavailable — publishing is impossible" };
  }
  return { state: STATE.OK, detail: "supported" };
}

async function checkNetwork() {
  const rtt = await measureRtt();
  if (rtt == null) return { state: STATE.FAIL, detail: "can't reach ZoikoStream" };
  const link = navigator.connection;
  const up = typeof link?.downlink === "number" ? link.downlink : null;
  // downlink is the only figure browsers expose; there is no uplink estimate anywhere, and a
  // publisher's uplink is what actually matters. Said plainly rather than implied.
  const slow = up != null && up < UPLINK_MIN_MBPS;
  if (rtt > RTT_FAIR || slow) {
    return {
      state: STATE.WARN,
      detail: slow ? `${up} Mbps down — may not sustain video` : `${Math.round(rtt)}ms — may stutter`,
      note: "Browsers don't expose an uplink estimate; this is round-trip and downlink only.",
    };
  }
  return {
    state: rtt <= RTT_GOOD ? STATE.OK : STATE.WARN,
    detail: `${rtt <= RTT_GOOD ? "good" : "fair"} · ${Math.round(rtt)}ms`,
  };
}

/** Camera: acquire, read what the hardware ACTUALLY granted, release. The requested resolution
 *  is a request — a webcam asked for 1080p that yields 640x480 is exactly what a host needs to
 *  know before going live, and only the granted settings reveal it. */
async function checkCamera(want) {
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { width: { ideal: want.width }, height: { ideal: want.height }, frameRate: { ideal: want.fps } },
    });
    const track = stream.getVideoTracks()[0];
    const s = track?.getSettings?.() || {};
    const label = track?.label || "camera";
    if (!s.width) return { state: STATE.WARN, detail: `${label} — resolution not reported` };
    const short = Math.min(s.width, s.height);
    const fps = s.frameRate ? Math.round(s.frameRate) : null;
    const detail = `${s.width}×${s.height}${fps ? ` @ ${fps}fps` : ""}`;
    // Under 720p short-edge, the stream will look soft at any bitrate.
    if (short < 720) {
      return { state: STATE.WARN, detail: `${detail} — below 720p`, meta: { ...s, label } };
    }
    return { state: STATE.OK, detail, meta: { ...s, label } };
  } catch (e) {
    return { state: STATE.FAIL, detail: describeMediaError(e, "camera") };
  } finally {
    stream?.getTracks().forEach((t) => t.stop());   // releases the camera light
  }
}

/** Microphone: acquire and MEASURE the input level over a short window. A muted-at-the-OS mic
 *  reports a live, enabled track with perfect silence — a presence check alone would call that
 *  healthy, which is the single most common "why couldn't anyone hear me" cause. */
async function checkMicrophone({ echo = true, noise = true, gain = true } = {}) {
  let stream;
  let ctx;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: echo, noiseSuppression: noise, autoGainControl: gain },
    });
    const track = stream.getAudioTracks()[0];
    const applied = track?.getSettings?.() || {};
    ctx = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    ctx.createMediaStreamSource(stream).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);

    let peak = 0;
    const started = performance.now();
    while (performance.now() - started < 1200) {
      analyser.getFloatTimeDomainData(buf);
      for (let i = 0; i < buf.length; i += 1) peak = Math.max(peak, Math.abs(buf[i]));
      await new Promise((r) => setTimeout(r, 100));
    }
    const level = Math.round(peak * 100);
    const processing = [
      applied.echoCancellation ? "echo cancel" : null,
      applied.noiseSuppression ? "noise suppress" : null,
      applied.autoGainControl ? "auto gain" : null,
    ].filter(Boolean).join(" · ");

    if (level < 1) {
      return {
        state: STATE.WARN,
        detail: "no sound detected — say something and re-run",
        note: "The track is live but silent, which usually means the mic is muted in the operating system.",
        meta: { level, processing },
      };
    }
    return { state: STATE.OK, detail: `picking up audio · ${processing || "no processing"}`, meta: { level, processing } };
  } catch (e) {
    return { state: STATE.FAIL, detail: describeMediaError(e, "microphone") };
  } finally {
    stream?.getTracks().forEach((t) => t.stop());
    ctx?.close?.();
  }
}

/** Speaker: are there any outputs, and can this browser route audio to a chosen one.
 *  Deliberately does NOT claim the host actually heard anything — that needs a human. The
 *  component pairs this with a "play a test tone" button, which is the only honest test. */
async function checkSpeaker() {
  if (!navigator.mediaDevices?.enumerateDevices) {
    return { state: STATE.UNKNOWN, detail: "not reported by this browser" };
  }
  const outputs = (await navigator.mediaDevices.enumerateDevices())
    .filter((d) => d.kind === "audiooutput");
  if (!outputs.length) {
    // Firefox exposes no audiooutput devices at all; absence is not proof of a problem.
    return { state: STATE.UNKNOWN, detail: "no outputs listed — play the test tone to confirm" };
  }
  const selectable = typeof HTMLMediaElement !== "undefined"
    && "setSinkId" in HTMLMediaElement.prototype;
  return {
    state: STATE.OK,
    detail: `${outputs.length} output${outputs.length === 1 ? "" : "s"}${selectable ? "" : " · not switchable here"}`,
    meta: { outputs: outputs.length, selectable },
  };
}

function describeMediaError(e, device) {
  switch (e?.name) {
    case "NotAllowedError": return `${device} access was blocked — allow it in the browser`;
    case "NotFoundError": return `no ${device} found on this device`;
    case "NotReadableError": return `${device} is in use by another application`;
    case "OverconstrainedError": return `${device} can't meet the requested settings`;
    default: return `couldn't start the ${device}: ${e?.message || e?.name || "unknown error"}`;
  }
}

/**
 * The host's pre-flight.
 *
 *   const { checks, running, run, ready, blocking } = useHostChecks({ settings });
 *
 * `ready` is true when nothing FAILED — warnings are informative, not blocking, because a host
 * with a 480p webcam should still be allowed to go live having been told.
 */
export default function useHostChecks({ settings } = {}) {
  const [checks, setChecks] = useState([]);
  const [running, setRunning] = useState(false);
  // Latest settings without making `run` a new function on every keystroke in the settings panel.
  // Assigned in an effect, not during render: a ref write on the render path is not safe under
  // concurrent rendering, and `run` only reads it from a click handler anyway.
  const cfg = useRef(settings);
  useEffect(() => {
    cfg.current = settings;
  }, [settings]);

  const run = useCallback(async () => {
    const s = cfg.current || {};
    const want = {
      width: s.resolution === "4k" ? 3840 : s.resolution === "2k" ? 2560 : s.resolution === "720p" ? 1280 : 1920,
      height: s.resolution === "4k" ? 2160 : s.resolution === "2k" ? 1440 : s.resolution === "720p" ? 720 : 1080,
      fps: s.framerate || 30,
    };

    const plan = [
      { id: "environment", label: "Browser & security", run: async () => checkEnvironment() },
      { id: "permissions", label: "Permissions", run: checkPermissions },
      { id: "camera", label: "Camera", run: () => checkCamera(want) },
      {
        id: "microphone",
        label: "Microphone",
        run: () => checkMicrophone({
          echo: s.echo_cancellation !== false,
          noise: s.noise_cancellation !== false,
          gain: s.auto_gain !== false,
        }),
      },
      { id: "speaker", label: "Speakers", run: checkSpeaker },
      { id: "network", label: "Network", run: checkNetwork },
    ];

    setRunning(true);
    setChecks(plan.map((c) => ({ id: c.id, label: c.label, state: STATE.CHECKING, detail: null })));

    // SEQUENTIAL, unlike the viewer's pre-flight which runs its four concurrently. Camera and
    // microphone both acquire hardware, and on many machines a concurrent grab of the same
    // device fails or returns a black frame — so the honest measurement costs a few seconds.
    const done = [];
    for (const c of plan) {
      const result = await c.run().catch(() => ({ state: STATE.UNKNOWN, detail: "check failed" }));
      done.push({ id: c.id, label: c.label, ...result });
      setChecks([...done, ...plan.slice(done.length).map((n) => ({
        id: n.id, label: n.label, state: STATE.CHECKING, detail: null,
      }))]);
    }
    setRunning(false);
    return done;
  }, []);

  const blocking = checks.filter((c) => c.state === STATE.FAIL);
  return {
    checks,
    running,
    run,
    // Nothing has been measured yet, so this is false until a run completes.
    ready: checks.length > 0 && !running && blocking.length === 0,
    blocking,
  };
}

export { STATE as HOST_CHECK_STATE };
