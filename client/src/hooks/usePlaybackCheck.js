import { useCallback, useEffect, useState } from "react";
import api from "../api";

// "Before you watch" — the four readiness checks on the attendee landing page.
//
// Every one of these MEASURES something. A pre-flight panel that always reports green is
// worse than no panel: the attendee whose browser genuinely can't decode the stream is
// told they're ready, and then blames the event. So each check probes a real browser
// capability or takes a real timing, and reports "unknown" when it honestly cannot tell
// (Safari and Firefox don't implement the Network Information API, for instance).
//
// Camera and microphone are NOT checked. This is a watch-only page — an attendee never
// publishes, so prompting for device permissions would be both useless and hostile.

const STATE = { OK: "ok", WARN: "warn", FAIL: "fail", UNKNOWN: "unknown", CHECKING: "checking" };

// Round-trip thresholds (ms) against our own API. Chosen against what live video actually
// needs, not round numbers: under 150ms is comfortable for sub-second WebRTC, and past
// 400ms an attendee will notice stalls no matter how good the encoder is.
const RTT_GOOD = 150;
const RTT_FAIR = 400;

const PROBES = 3;

/** Median of the round-trip time to our own API. Median, not mean: one GC pause or one
 *  scheduler hiccup shouldn't downgrade a healthy connection to "poor". */
async function measureRtt() {
  const samples = [];
  for (let i = 0; i < PROBES; i += 1) {
    const t0 = performance.now();
    try {
      // Cache-busted so a 304 or a disk hit doesn't report as a 2ms network.
      await api.get("/health", { params: { _: `${t0}` } });
      samples.push(performance.now() - t0);
    } catch {
      return null; // unreachable — the caller reports that as a failure, not a slow link
    }
  }
  return samples.sort((a, b) => a - b)[Math.floor(samples.length / 2)];
}

async function checkConnection() {
  const rtt = await measureRtt();
  if (rtt == null) {
    return { state: STATE.FAIL, detail: "can't reach ZoikoStream" };
  }
  // Downlink estimate where the browser offers one (Chromium only). Additive: it sharpens
  // the verdict, it is never the only evidence.
  const link = navigator.connection;
  const downlink = typeof link?.downlink === "number" ? link.downlink : null;
  const tooSlow = downlink != null && downlink < 1.5; // Mbps — below SD live video

  if (rtt > RTT_FAIR || tooSlow) {
    return {
      state: STATE.WARN,
      detail: tooSlow ? `${downlink} Mbps — may buffer` : `${Math.round(rtt)}ms — may buffer`,
    };
  }
  const label = rtt <= RTT_GOOD ? "good" : "fair";
  return { state: rtt <= RTT_GOOD ? STATE.OK : STATE.WARN, detail: `${label} · ${Math.round(rtt)}ms` };
}

/** WebRTC transport + a decoder for at least one codec LiveKit actually publishes. */
function checkBrowser() {
  if (typeof RTCPeerConnection === "undefined" || typeof WebSocket === "undefined") {
    return { state: STATE.FAIL, detail: "WebRTC unavailable" };
  }
  // getCapabilities is the only way to ask what this build can DECODE. Absent on older
  // Safari, where the presence of RTCPeerConnection is the best evidence available.
  const caps = window.RTCRtpReceiver?.getCapabilities?.("video");
  if (!caps) return { state: STATE.OK, detail: "supported" };

  const codecs = caps.codecs.map((c) => c.mimeType.toLowerCase());
  const usable = ["video/h264", "video/vp8", "video/vp9", "video/av1"].filter((c) => codecs.includes(c));
  if (!usable.length) return { state: STATE.FAIL, detail: "no compatible video codec" };
  return { state: STATE.OK, detail: `supported · ${usable[0].split("/")[1].toUpperCase()}` };
}

/** Whether this document is even allowed to play protected media, before hardware. */
function checkDevice() {
  // getUserMedia and the whole media stack are gated on a secure context. Over plain HTTP
  // (a LAN IP, say) playback fails in a way that looks like a broken stream, so name it.
  if (!window.isSecureContext) {
    return { state: STATE.FAIL, detail: "needs a secure (https) connection" };
  }
  if (!("mediaDevices" in navigator)) {
    return { state: STATE.WARN, detail: "limited media support" };
  }
  const cores = navigator.hardwareConcurrency;
  // Two cores can decode 1080p on modern hardware but will struggle with a busy tab.
  if (typeof cores === "number" && cores <= 2) {
    return { state: STATE.WARN, detail: `${cores} cores — prefer a lower quality` };
  }
  return { state: STATE.OK, detail: "supported" };
}

/** Encrypted Media Extensions capability.
 *
 *  This reports what the BROWSER can do, not that this event is DRM-protected — no DRM
 *  provider is configured on this platform. It is labelled "protected playback" because
 *  that is the honest claim: the stream is DTLS-SRTP encrypted in transit (the server
 *  reports that separately), and this says whether the browser could also handle a
 *  DRM-protected source if one were ever served. */
async function checkProtectedPlayback() {
  if (!navigator.requestMediaKeySystemAccess) {
    return { state: STATE.UNKNOWN, detail: "not reported by this browser" };
  }
  const config = [{
    initDataTypes: ["cenc"],
    videoCapabilities: [{ contentType: 'video/mp4;codecs="avc1.42E01E"' }],
  }];
  for (const keySystem of ["com.widevine.alpha", "com.apple.fps", "com.microsoft.playready"]) {
    try {
      await navigator.requestMediaKeySystemAccess(keySystem, config);
      return { state: STATE.OK, detail: "ready" };
    } catch {
      // This key system isn't available; try the next.
    }
  }
  return { state: STATE.WARN, detail: "unavailable in this browser" };
}

const CHECKS = [
  { id: "connection", label: "Connection", run: checkConnection },
  { id: "browser", label: "Browser", run: async () => checkBrowser() },
  { id: "device", label: "Device", run: async () => checkDevice() },
  { id: "protected", label: "Protected playback", run: checkProtectedPlayback },
];

const pending = () => CHECKS.map((c) => ({ id: c.id, label: c.label, state: STATE.CHECKING, detail: null }));

// Concurrent: `connection` is the only slow check (three network probes), and queuing the
// three synchronous capability checks behind it would triple how long the panel sits in
// its skeleton state for no gain. A check that throws is a failed check, not a crashed page.
const runAll = () =>
  Promise.all(
    CHECKS.map((c) =>
      c.run()
        .catch(() => ({ state: STATE.UNKNOWN, detail: "check failed" }))
        .then((r) => ({ id: c.id, label: c.label, ...r }))
    )
  );

/**
 * Runs the four checks on mount and re-runs them on demand ("Run check again").
 *
 *   const { checks, running, run } = usePlaybackCheck();
 */
export default function usePlaybackCheck() {
  const [checks, setChecks] = useState(pending);
  const [running, setRunning] = useState(true);

  const run = useCallback(async () => {
    setRunning(true);
    setChecks(pending);
    setChecks(await runAll());
    setRunning(false);
  }, []);

  useEffect(() => {
    let alive = true;
    // The panel is above the fold, so a navigation away mid-probe would otherwise set
    // state on an unmounted component. The manual run() above needs no such guard —
    // it only fires from a click, which can only happen while mounted.
    runAll().then((results) => {
      if (!alive) return;
      setChecks(results);
      setRunning(false);
    });
    return () => { alive = false; };
  }, []);

  return { checks, running, run };
}

export { STATE as CHECK_STATE };
