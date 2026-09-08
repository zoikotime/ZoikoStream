import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import { fatalDisconnect } from "./livekitDisconnect";

// Subscribes to whatever is being published in the room and attaches every remote track
// (video AND audio, from the host AND from any promoted speaker) to ONE <video> element —
// track.attach() adds to the element's existing srcObject rather than replacing it, so every
// publisher ends up mixed into one combined stream, same as any normal <video> with sound.
//
// There is deliberately no "which participant is the host" logic here, and no
// participants[0]: the room only ever contains publishers the backend has authorised to
// publish (services/livekit.py mints can_publish=false for every audience token, and the
// host's promote/demote goes through livekit.set_stage), so "whatever is published" IS the
// presenter set. Picking a participant by index would break the moment a promoted speaker
// connected before the host.
//
// Mostly viewer-only — the token itself is subscribe-only (server-issued, can_publish=
// false) — but `canPublish` is the one exception: when the host promotes this viewer to
// speaker, services/moderation.py pushes a LIVE LiveKit permission update to the room for
// this identity (livekit.set_stage), which an ALREADY-CONNECTED client can act on without a
// new token — LiveKit's server-side UpdateParticipant call is exactly what makes a stale
// "can't publish" token stop being true mid-session. This hook is what actually captures the
// mic and calls publishTrack() once that permission lands, and tears it back down the moment
// the host demotes the participant again.
//
// ── WHY ATTACHING IS DEFERRED TO AN EFFECT ──────────────────────────────────────────────
// TrackSubscribed used to attach inline: `if (mediaRef.current) track.attach(...)` followed
// unconditionally by setHasVideo(true). When the <video> element wasn't in the DOM yet (or
// had just been remounted), the guard skipped the attach but the flag still flipped — so the
// placeholder was dismissed over an element that had never been given a track, and nothing
// ever retried. Subscribed tracks are now held in a Set and attached from an effect, which
// re-runs both when the track set changes AND when the element mounts, so the two can arrive
// in either order. hasVideo/hasAudio are derived from that same Set rather than set by hand,
// which also fixes one publisher unsubscribing clearing the flag while another was still
// sending.
//
// Reconnection: livekit-client already retries transient ICE/network blips on its own
// (surfaced here as `reconnecting` via RoomEvent.Reconnecting/Reconnected). It only emits
// Disconnected when it gives up or the server actively closed the room — mirrors
// useLiveKitPublish's handling of the same event on the host side, including that hook's
// single-owned-room discipline: a new Room is never created until the previous one has been
// disconnected AND awaited, because two connections under one identity make LiveKit evict
// the older (DisconnectReason.DUPLICATE_IDENTITY), whose handler would then schedule another
// retry — a loop that evicts itself and never recovers.
//
// Retries continue indefinitely at a capped, jittered cadence (matching
// hooks/useEventStream.js's proven reconnect loop); past SLOW_ATTEMPTS_AFTER the message says
// so honestly instead of claiming the stream is unrecoverable.
const BASE_RECONNECT_DELAY_MS = 1500;
const MAX_RECONNECT_DELAY_MS = 15000;
const SLOW_ATTEMPTS_AFTER = 5;
const jitter = (ms) => ms * (0.7 + Math.random() * 0.6);

// Development builds only, and never under the test runner (which also sets DEV) —
// diagnostics are for a real console session, not for cluttering test output.
const DEV = typeof import.meta !== "undefined"
  && Boolean(import.meta.env?.DEV)
  && import.meta.env?.MODE !== "test";

// Room/participant/track facts only — never the token.
const logSubscription = (label, room, extra) => {
  if (!DEV) return;
  try {
    console.log("[viewer] " + label, {
      room: room?.name,
      identity: room?.localParticipant?.identity,
      state: room?.state,
      remoteParticipants: room ? [...room.remoteParticipants.values()].map((p) => ({
        identity: p.identity,
        tracks: [...p.trackPublications.values()].map((t) => ({
          source: t.source, kind: t.kind, sid: t.trackSid, subscribed: t.isSubscribed, muted: t.isMuted,
        })),
      })) : [],
      ...extra,
    });
  } catch {
    // Diagnostics must never break playback.
  }
};

export default function useLiveKitViewer({ enabled, url, token, canPublish = false }) {
  const roomRef = useRef(null);

  // A callback ref that ALSO exposes `.current`, so consumers can keep doing both
  // `ref={mediaRef}` and `mediaRef.current.play()` (components/watch/VideoPlayer.jsx does
  // both) while this hook still learns the instant the element mounts or unmounts — which is
  // what lets the attach effect below re-run for an element that appeared after the track.
  const [elVersion, setElVersion] = useState(0);
  const mediaRef = useMemo(() => {
    const ref = (node) => {
      if (ref.current === node) return;
      ref.current = node;
      setElVersion((n) => n + 1);
    };
    ref.current = null;
    return ref;
  }, []);

  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [error, setError] = useState(null);

  // Every remote track we are currently subscribed to. The single source of truth for both
  // "what should be attached to the element" and hasVideo/hasAudio.
  //
  // `tracks` mirrors that Set as STATE: `version` drives the attach effect, and the two
  // booleans are recomputed here — inside bumpTracks, which only ever runs from an event
  // handler or an effect — rather than derived from tracksRef during render, which would be
  // reading a ref at render time (React can't know to re-render on a ref mutation, so the
  // flags could paint stale; react-hooks/refs rejects it outright).
  const tracksRef = useRef(new Set());
  const [tracks, setTracks] = useState({ version: 0, hasVideo: false, hasAudio: false });
  const bumpTracks = useCallback(() => {
    let hasVideo = false;
    let hasAudio = false;
    tracksRef.current.forEach((t) => {
      if (t.kind === Track.Kind.Video) hasVideo = true;
      else if (t.kind === Track.Kind.Audio) hasAudio = true;
    });
    setTracks((prev) => ({ version: prev.version + 1, hasVideo, hasAudio }));
  }, []);

  // This participant's OWN mic, published only while promoted to speaker (canPublish).
  // Separate from hasAudio/hasVideo, which describe REMOTE tracks this viewer is
  // watching/listening to — micOn describes what this viewer is sending.
  const micStreamRef = useRef(null);
  const micPubRef = useRef(null);
  const [micOn, setMicOn] = useState(false);
  const [micError, setMicError] = useState(null);

  // Generation guard: identical purpose to useLiveKitPublish's. Any async continuation that
  // is no longer the newest attempt must not touch state or leave a room connected.
  const genRef = useRef(0);

  useEffect(() => {
    if (!enabled || !url || !token) return undefined;

    genRef.current += 1;
    const myGen = genRef.current;
    const current = () => genRef.current === myGen;

    let attempt = 0;
    let retryTimer = null;

    const clearTracks = () => {
      tracksRef.current.forEach((t) => {
        try {
          t.detach();
        } catch {
          // Already detached.
        }
      });
      tracksRef.current.clear();
      bumpTracks();
    };

    const disposeRoom = async () => {
      const room = roomRef.current;
      roomRef.current = null;
      if (!room) return;
      room.removeAllListeners();
      try {
        await room.disconnect();
      } catch {
        // Already gone.
      }
    };

    const connectOnce = async () => {
      await disposeRoom();
      if (!current()) return;

      const room = new Room();
      roomRef.current = room;

      const onSubscribed = (track) => {
        if (!current()) return;
        tracksRef.current.add(track);
        // Attaching happens in the effect below — see this file's header for why doing it
        // here was unsafe.
        bumpTracks();
        logSubscription("track subscribed", room, { kind: track.kind, source: track.source });
      };
      const onUnsubscribed = (track) => {
        try {
          track.detach();
        } catch {
          // Already detached.
        }
        tracksRef.current.delete(track);
        if (!current()) return;
        bumpTracks();
      };

      room.on(RoomEvent.TrackSubscribed, onSubscribed);
      room.on(RoomEvent.TrackUnsubscribed, onUnsubscribed);
      // Not load-bearing for subscription (the token grants can_subscribe and the room is
      // created with autoSubscribe, so LiveKit subscribes to anything published after we
      // join by itself) — but they ARE the signals that prove a viewer who joined BEFORE the
      // host will still get the track, so they're observed explicitly rather than assumed.
      room.on(RoomEvent.ParticipantConnected, (p) => logSubscription("participant connected", room, { who: p.identity }));
      room.on(RoomEvent.TrackPublished, (pub, p) => logSubscription("remote track published", room, { who: p.identity, source: pub.source }));
      room.on(RoomEvent.ParticipantDisconnected, (p) => {
        // Drop anything that participant was sending, so a host who leaves doesn't leave a
        // frozen last frame attached to the element.
        p.trackPublications.forEach((pub) => {
          if (pub.track) onUnsubscribed(pub.track);
        });
        logSubscription("participant disconnected", room, { who: p.identity });
      });
      room.on(RoomEvent.Reconnecting, () => {
        if (current()) setReconnecting(true);
      });
      room.on(RoomEvent.Reconnected, () => {
        if (!current()) return;
        setReconnecting(false);
        setConnected(true);
        setError(null);
        // A full reconnect resubscribes from scratch; re-derive from what the room actually
        // holds now instead of trusting the pre-drop Set.
        clearTracks();
        room.remoteParticipants.forEach((p) => {
          p.trackPublications.forEach((pub) => {
            if (pub.track && pub.isSubscribed) tracksRef.current.add(pub.track);
          });
        });
        bumpTracks();
      });
      room.on(RoomEvent.Disconnected, (reason) => {
        clearTracks();
        if (!current()) return;
        setConnected(false);
        // ROOM_DELETED (the event genuinely ended) and DUPLICATE_IDENTITY (this viewer has
        // another tab open on the same event) are never fixed by retrying — see
        // livekitDisconnect.js. Stop and say so instead of retrying into a room that's gone.
        const fatal = fatalDisconnect(reason);
        if (fatal) {
          setReconnecting(false);
          setError(fatal);
          return;
        }
        scheduleRetry();
      });

      await room.connect(url, token);
      if (!current()) {
        await disposeRoom();
        return;
      }
      // Tracks published BEFORE we joined: autoSubscribe will fire TrackSubscribed for them,
      // but anything already subscribed by the time connect() resolves would otherwise be
      // missed entirely. Both orders are covered.
      room.remoteParticipants.forEach((p) => {
        p.trackPublications.forEach((pub) => {
          if (pub.track && pub.isSubscribed) tracksRef.current.add(pub.track);
        });
      });
      bumpTracks();
      attempt = 0;
      setConnected(true);
      setReconnecting(false);
      setError(null);
      logSubscription("connected", room);
    };

    const scheduleRetry = () => {
      if (!current()) return;
      attempt += 1;
      const stillFast = attempt <= SLOW_ATTEMPTS_AFTER;
      setReconnecting(stillFast);
      setError(
        stillFast ? null
          : "Lost connection to the stream — still trying to reconnect in the background. "
            + "Refreshing the page will also retry immediately."
      );
      const delay = jitter(Math.min(BASE_RECONNECT_DELAY_MS * 2 ** (attempt - 1), MAX_RECONNECT_DELAY_MS));
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(() => {
        if (!current()) return;
        connectOnce().catch(() => scheduleRetry());
      }, delay);
    };

    connectOnce().catch((e) => {
      if (!current()) return;
      setError(e?.message || "Couldn't connect to the stream");
      scheduleRetry();
    });

    return () => {
      genRef.current += 1;
      if (retryTimer) clearTimeout(retryTimer);
      clearTracks();
      setConnected(false);
      setReconnecting(false);
      void disposeRoom();
      // The mic itself is a real getUserMedia capture (see the effect below) — it does
      // NOT belong to LiveKit and isn't released by room.disconnect(), so it has to be
      // stopped here explicitly or the browser's "mic in use" indicator outlives the page.
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
      micPubRef.current = null;
      setMicOn(false);
    };
  }, [enabled, url, token, bumpTracks]);

  // Attach every subscribed track to the element, whenever either side changes. attach() is
  // idempotent per (track, element) in livekit-client — it checks whether the element is
  // already in the track's attachedElements — so re-running this cannot create duplicate
  // <video> elements or double-add a track.
  useEffect(() => {
    const el = mediaRef.current;
    if (!el) return;
    tracksRef.current.forEach((track) => {
      try {
        track.attach(el);
      } catch {
        // A track that ended between subscribe and attach; the next bump re-derives.
      }
    });
  }, [tracks.version, elVersion, mediaRef]);

  // Recomputed in bumpTracks (see above) whenever the Set changes, so they cannot desync
  // from what is actually attached — and so a second publisher leaving doesn't clear a flag
  // the first one is still satisfying.
  const { hasVideo, hasAudio } = tracks;

  // Publish/unpublish this participant's own mic as `canPublish` (the host's promote/
  // demote) flips, for as long as the connection above is live. Runs independently of the
  // connect effect so a mid-session promotion (no reconnect) picks it up immediately —
  // exactly the case a stage invite is.
  useEffect(() => {
    const room = roomRef.current;
    if (!room || !connected) return undefined;

    let cancelled = false;

    if (canPublish && !micPubRef.current) {
      (async () => {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          if (cancelled) {
            stream.getTracks().forEach((t) => t.stop());
            return;
          }
          micStreamRef.current = stream;
          const track = stream.getAudioTracks()[0];
          micPubRef.current = await room.localParticipant.publishTrack(track, {
            source: Track.Source.Microphone,
          });
          if (!cancelled) {
            setMicOn(true);
            setMicError(null);
          }
        } catch (e) {
          if (!cancelled) {
            // Most likely the browser mic permission prompt was denied — that's a normal,
            // recoverable outcome (not a connection error), so it's surfaced separately
            // from `error` above and the viewer can still watch/listen either way.
            setMicError(e?.name === "NotAllowedError"
              ? "Mic access was blocked — allow it in your browser to speak on stage."
              : (e?.message || "Couldn't access your microphone"));
          }
        }
      })();
    } else if (!canPublish && micPubRef.current) {
      const pub = micPubRef.current;
      micPubRef.current = null;
      room.localParticipant.unpublishTrack(pub.track, true).catch(() => {});
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
      setMicOn(false);
      setMicError(null);
    }

    return () => {
      cancelled = true;
    };
  }, [canPublish, connected]);

  // Mute/unmute without a full unpublish — cheaper, and mirrors the host console's own
  // mic toggle (useMediaPreview's `t.enabled = false`). Only meaningful while actually
  // publishing (canPublish + a live mic track); a no-op otherwise.
  const toggleMic = useCallback(() => {
    const track = micStreamRef.current?.getAudioTracks()[0];
    if (!track) return;
    track.enabled = !track.enabled;
    setMicOn(track.enabled);
  }, []);

  return {
    mediaRef, connected, reconnecting, hasVideo, hasAudio, error,
    micOn, micError, toggleMic,
  };
}
