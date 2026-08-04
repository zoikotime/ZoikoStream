import { useCallback, useEffect, useRef, useState } from "react";

// The host's LiveKit PUBLISHER — the piece that makes the control room actually broadcast.
// Everything around media (tokens, room control, recording rows, health, analytics) was already
// built and waiting; this is the missing attachment point.
//
// Design decisions that are load-bearing, each learned the hard way:
//
//  * ONE camera acquisition. hooks/useMediaPreview owns getUserMedia (device pick,
//    applyConstraints, measured resolution) and now exposes its MediaStream; this hook WRAPS
//    those existing tracks. Calling room.localParticipant.setCameraEnabled(true) instead would
//    open the device a second time — two hardware handles, two indicator lights, and a black
//    frame on machines that allow only one consumer.
//
//  * The publisher connects with a SUFFIXED identity ("<uid>#host", minted server-side).
//    LiveKit permits one connection per identity per room and disconnects the older one, and the
//    attendee playback token uses the bare uid — so a host who opened the watch page in another
//    tab would kick their own broadcast off air.
//
//  * unpublishTrack(track, false). The `false` is stopOnUnpublish: LiveKit would otherwise stop
//    the underlying MediaStreamTrack, which is useMediaPreview's, killing the local monitor too.
//
//  * The connect effect keys on the ROOM, never on the token. A socket reconnect mints a fresh
//    publish_token on every snapshot; keying on it would tear down a live broadcast on every
//    transient network blip.
//
//  * adaptiveStream is FALSE here. A host must see every stage feed to direct the show;
//    adaptive would pause the tiles scrolled out of view, which is right for an audience member
//    and wrong for the person running it. dynacast stays on — that governs what we SEND.

const STATS_MS = 2000;
// Telemetry is reported to the server far less often than it is measured. The socket allows 30
// actions / 10s per connection (routers/live.py), and the server fans every participant.state
// to every subscriber — so a 2s cadence from one host would burn a fifth of the budget and spam
// the room. Measure often, report rarely.
const REPORT_MS = 10000;

const EMPTY_STATS = {
  bitrateKbps: null, packetLoss: null, rttMs: null, fps: null, width: null, height: null,
  qualityLimitation: null,
};

/** Bitrate is a DELTA, not a gauge: bytesSent is cumulative, so it only means anything against
 *  the previous sample. */
function deriveOutbound(prev, curr) {
  if (!prev || !curr || curr.timestamp <= prev.timestamp) return null;
  const seconds = (curr.timestamp - prev.timestamp) / 1000;
  const bytes = curr.bytesSent - prev.bytesSent;
  const packets = curr.packetsSent - prev.packetsSent;
  const lost = (curr.packetsLost || 0) - (prev.packetsLost || 0);
  return {
    bitrateKbps: Math.max(0, Math.round((bytes * 8) / seconds / 1000)),
    packetLoss: packets > 0 ? Math.max(0, Math.round((lost / packets) * 100)) : 0,
  };
}

export default function usePublisher({
  enabled, token, url, media, camera, mic,
  onError,
}) {
  const roomRef = useRef(null);
  const cameraPubRef = useRef(null);
  const micPubRef = useRef(null);
  const screenPubRef = useRef(null);
  const sdkRef = useRef(null);          // the dynamically imported module
  const prevSampleRef = useRef(null);

  const [status, setStatus] = useState("idle"); // idle|connecting|connected|reconnecting|failed
  const [error, setError] = useState(null);
  const [stats, setStats] = useState(EMPTY_STATS);
  const [remotes, setRemotes] = useState([]);   // [{ identity, name, videoTrack, audioTrack, speaking, source }]
  const [screenSharing, setScreenSharing] = useState(false);
  // State, not a ref read at render time: which tracks are live is a render input.
  const [publishing, setPublishing] = useState(false);

  const { streamRef, streamVersion } = media || {};

  // Latest token without making it a connect dependency — see the header note. Assigned in an
  // effect rather than during render: a ref write on the render path is unsafe under concurrent
  // rendering, and the connect path only reads it inside an async callback.
  const tokenRef = useRef(token);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  const fail = useCallback((e, label) => {
    const message = e?.message || String(e);
    setError(`${label}: ${message}`);
    setStatus("failed");
    onError?.(`${label}: ${message}`);
  }, [onError]);

  /** Remote participants, rebuilt from the room. Kept as a snapshot in state so React renders
   *  it; the Room itself stays a ref because it is an imperative handle, not a render input. */
  const syncRemotes = useCallback(() => {
    const room = roomRef.current;
    if (!room) return;
    const out = [];
    room.remoteParticipants.forEach((p) => {
      let videoTrack = null;
      let audioTrack = null;
      let source = "camera";
      p.trackPublications.forEach((pub) => {
        if (!pub.track) return;
        if (pub.kind === "video") {
          // A screen share outranks a camera in the grid: if someone is presenting, that is
          // what the room is looking at.
          const isShare = pub.source === sdkRef.current?.Track.Source.ScreenShare;
          if (isShare || !videoTrack) {
            videoTrack = pub.track;
            source = isShare ? "screen_share" : "camera";
          }
        } else if (pub.kind === "audio") {
          audioTrack = pub.track;
        }
      });
      out.push({
        identity: p.identity,
        // Strip the publisher suffix so a tile matches its presence record.
        baseIdentity: (p.identity || "").split("#")[0],
        name: p.name || p.identity,
        videoTrack,
        audioTrack,
        source,
        speaking: p.isSpeaking,
      });
    });
    setRemotes(out);
  }, []);

  // ── connect / disconnect ────────────────────────────────────────────────────
  useEffect(() => {
    if (!enabled || !url) return undefined;

    let cancelled = false;
    let room;

    (async () => {
      try {
        setStatus("connecting");
        setError(null);
        // Dynamic import for the same reason the viewer does it: livekit-client is ~150KB and a
        // host who never opens the studio should not pay for it.
        const sdk = await import("livekit-client");
        if (cancelled) return;
        sdkRef.current = sdk;
        const { Room, RoomEvent } = sdk;

        room = new Room({
          adaptiveStream: false,   // the host must see every feed — see the header note
          dynacast: true,
        });
        roomRef.current = room;

        room
          .on(RoomEvent.TrackSubscribed, syncRemotes)
          .on(RoomEvent.TrackUnsubscribed, (track) => { track.detach(); syncRemotes(); })
          .on(RoomEvent.ParticipantConnected, syncRemotes)
          .on(RoomEvent.ParticipantDisconnected, syncRemotes)
          .on(RoomEvent.ActiveSpeakersChanged, syncRemotes)
          .on(RoomEvent.Reconnecting, () => setStatus("reconnecting"))
          .on(RoomEvent.Reconnected, () => { setStatus("connected"); syncRemotes(); })
          .on(RoomEvent.Disconnected, (reason) => {
            roomRef.current = null;
            cameraPubRef.current = micPubRef.current = screenPubRef.current = null;
            setPublishing(false);
            setStatus("idle");
            setRemotes([]);
            // DUPLICATE_IDENTITY means something else connected as this identity. Saying so
            // beats "disconnected", because the cause is almost always a second tab.
            if (reason === sdk.DisconnectReason?.DUPLICATE_IDENTITY) {
              setError("Disconnected — this host identity connected somewhere else.");
            }
          });

        if (!tokenRef.current) {
          throw new Error("no publisher token — you may not be a host of this event");
        }
        await room.connect(url, tokenRef.current);
        if (cancelled) {
          await room.disconnect();
          return;
        }
        setStatus("connected");
        syncRemotes();
      } catch (e) {
        if (!cancelled) fail(e, "Couldn't join the broadcast room");
      }
    })();

    return () => {
      cancelled = true;
      // Unpublish with stopOnUnpublish=false so useMediaPreview keeps its tracks and the local
      // monitor survives leaving the room.
      const r = roomRef.current;
      if (r) {
        for (const ref of [cameraPubRef, micPubRef, screenPubRef]) {
          const t = ref.current;
          if (t) {
            try {
              r.localParticipant.unpublishTrack(t, false);
            } catch {
              /* already gone — disconnect below cleans up regardless */
            }
          }
          ref.current = null;
        }
        r.disconnect();
      }
      roomRef.current = null;
      setPublishing(false);
      setStatus("idle");
      setRemotes([]);
    };
    // Deliberately NOT keyed on `token`: see the header note about reconnect storms.
  }, [enabled, url, syncRemotes, fail]);

  // ── publish the preview's tracks ────────────────────────────────────────────
  // Runs on connect and whenever useMediaPreview REPLACES its tracks (device switch), which is
  // what streamVersion signals. Uses replaceTrack on an existing publication so the sender's
  // encodings are re-derived — swapping the raw track underneath would leave LiveKit advertising
  // simulcast layers that no longer exist.
  useEffect(() => {
    const room = roomRef.current;
    const stream = streamRef?.current;
    if (status !== "connected" || !room || !stream || !sdkRef.current) return;

    let cancelled = false;
    (async () => {
      const { LocalVideoTrack, LocalAudioTrack, Track } = sdkRef.current;
      try {
        const rawVideo = stream.getVideoTracks()[0];
        const rawAudio = stream.getAudioTracks()[0];

        if (rawVideo) {
          if (cameraPubRef.current) {
            await cameraPubRef.current.replaceTrack(rawVideo);
          } else {
            const t = new LocalVideoTrack(rawVideo);
            await room.localParticipant.publishTrack(t, { source: Track.Source.Camera });
            if (!cancelled) {
              cameraPubRef.current = t;
              setPublishing(true);
            }
          }
        }
        if (rawAudio) {
          if (micPubRef.current) {
            await micPubRef.current.replaceTrack(rawAudio);
          } else {
            const t = new LocalAudioTrack(rawAudio);
            await room.localParticipant.publishTrack(t, { source: Track.Source.Microphone });
            if (!cancelled) micPubRef.current = t;
          }
        }
      } catch (e) {
        if (!cancelled) fail(e, "Couldn't publish your camera or microphone");
      }
    })();

    return () => { cancelled = true; };
  }, [status, streamRef, streamVersion, fail]);

  // ── camera / mic toggles ────────────────────────────────────────────────────
  // mute(), not unpublish(). Muting keeps the transceiver and tells every subscriber
  // explicitly, which is what the repo's presence model already reports (participant.state
  // carries `muted`); unpublishing would renegotiate and read to viewers as "left the stage".
  useEffect(() => {
    const t = cameraPubRef.current;
    if (!t) return;
    camera ? t.unmute() : t.mute();
  }, [camera, status]);

  useEffect(() => {
    const t = micPubRef.current;
    if (!t) return;
    mic ? t.unmute() : t.mute();
  }, [mic, status]);

  // ── screen share ────────────────────────────────────────────────────────────
  // A SECOND video track alongside the camera, not a replacement: the grid's presentation
  // layout shows the share large with the camera inset, which is impossible if the share
  // replaced the camera.
  const startScreenShare = useCallback(async () => {
    const room = roomRef.current;
    const sdk = sdkRef.current;
    if (!room || !sdk || screenPubRef.current) return false;
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      const raw = stream.getVideoTracks()[0];
      if (!raw) {
        stream.getTracks().forEach((t) => t.stop());
        return false;
      }
      const track = new sdk.LocalVideoTrack(raw);
      await room.localParticipant.publishTrack(track, { source: sdk.Track.Source.ScreenShare });
      screenPubRef.current = track;
      setScreenSharing(true);
      // The browser's own "Stop sharing" button ends the track without telling us, so mirror it.
      raw.addEventListener("ended", () => {
        const r = roomRef.current;
        if (r && screenPubRef.current) {
          // stopOnUnpublish=true here: this track IS ours, unlike the preview's.
          r.localParticipant.unpublishTrack(screenPubRef.current, true);
        }
        screenPubRef.current = null;
        setScreenSharing(false);
      });
      return true;
    } catch {
      // The host cancelled the picker — not an error worth surfacing.
      return false;
    }
  }, []);

  const stopScreenShare = useCallback(async () => {
    const room = roomRef.current;
    const track = screenPubRef.current;
    screenPubRef.current = null;
    setScreenSharing(false);
    if (room && track) {
      try {
        await room.localParticipant.unpublishTrack(track, true);
      } catch {
        /* already gone */
      }
    }
  }, []);

  // ── outbound stats ──────────────────────────────────────────────────────────
  // Real numbers from the peer connection: this is the ONLY place outbound bitrate, packet loss
  // and encoder resolution exist. The server has no access to them, which is why it accepts them
  // over participant.state instead of inventing them.
  useEffect(() => {
    if (status !== "connected") {
      prevSampleRef.current = null;
      // Clear on the NEXT tick rather than synchronously in the effect body — a sync setState
      // here cascades a second render for a value nothing is reading yet.
      const id = setTimeout(() => setStats(EMPTY_STATS), 0);
      return () => clearTimeout(id);
    }
    let alive = true;
    const sample = async () => {
      const track = cameraPubRef.current;
      if (!track) return;
      try {
        const report = await track.getRTCStatsReport?.();
        if (!report || !alive) return;
        let outbound = null;
        let candidate = null;
        report.forEach((s) => {
          if (s.type === "outbound-rtp" && s.kind === "video") {
            // With simulcast there are several outbound-rtp entries; the highest layer is the
            // one whose numbers describe the broadcast.
            if (!outbound || (s.bytesSent || 0) > (outbound.bytesSent || 0)) outbound = s;
          } else if (s.type === "candidate-pair" && s.nominated) {
            candidate = s;
          }
        });
        if (!outbound) return;
        // packetsLost lives on the REMOTE inbound report; fall back to 0 when absent.
        let remoteLost = 0;
        report.forEach((s) => {
          if (s.type === "remote-inbound-rtp" && s.kind === "video") remoteLost = s.packetsLost || 0;
        });
        const curr = {
          timestamp: outbound.timestamp,
          bytesSent: outbound.bytesSent || 0,
          packetsSent: outbound.packetsSent || 0,
          packetsLost: remoteLost,
        };
        const delta = deriveOutbound(prevSampleRef.current, curr);
        prevSampleRef.current = curr;
        if (!alive) return;
        setStats({
          bitrateKbps: delta?.bitrateKbps ?? null,
          packetLoss: delta?.packetLoss ?? null,
          rttMs: candidate?.currentRoundTripTime != null
            ? Math.round(candidate.currentRoundTripTime * 1000) : null,
          fps: outbound.framesPerSecond != null ? Math.round(outbound.framesPerSecond) : null,
          width: outbound.frameWidth ?? null,
          height: outbound.frameHeight ?? null,
          // "cpu" or "bandwidth" when the encoder is being held back — the honest reason a
          // stream looks soft, which no invented number would explain.
          qualityLimitation: outbound.qualityLimitationReason &&
            outbound.qualityLimitationReason !== "none" ? outbound.qualityLimitationReason : null,
        });
      } catch {
        /* stats are best-effort; a browser that won't report them shows no number */
      }
    };
    sample();
    const id = setInterval(sample, STATS_MS);
    return () => { alive = false; clearInterval(id); };
  }, [status]);

  return {
    status,
    error,
    stats,
    remotes,
    screenSharing,
    startScreenShare,
    stopScreenShare,
    publishing: status === "connected" && publishing,
    // Cadence for the caller's reporting effect, so the socket budget lives in one place.
    reportIntervalMs: REPORT_MS,
  };
}

export { EMPTY_STATS, deriveOutbound };
