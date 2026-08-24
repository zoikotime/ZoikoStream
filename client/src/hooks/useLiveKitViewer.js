import { useCallback, useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

// Subscribes to whatever is being published in the room and attaches every remote track
// (video AND audio, from the host AND from any promoted speaker) to ONE <video> element —
// track.attach() adds to the element's existing srcObject rather than replacing it, so every
// publisher ends up mixed into one combined stream, same as any normal <video> with sound.
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
// Reconnection: livekit-client already retries transient ICE/network blips on its own
// (surfaced here as `reconnecting` via RoomEvent.Reconnecting/Reconnected). It only emits
// Disconnected when it gives up or the server actively closed the room — mirrors
// useLiveKitPublish's handling of the same event on the host side. Without this, a viewer
// whose connection drops (network blip, backgrounded tab) was stuck on a frozen frame
// forever with no recovery attempt.
const MAX_RECONNECT_ATTEMPTS = 5;
const BASE_RECONNECT_DELAY_MS = 1500;

export default function useLiveKitViewer({ enabled, url, token, canPublish = false }) {
  const mediaRef = useRef(null);
  const roomRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const [hasAudio, setHasAudio] = useState(false);
  const [error, setError] = useState(null);

  // This participant's OWN mic, published only while promoted to speaker (canPublish).
  // Separate from hasAudio/hasVideo above, which describe REMOTE tracks this viewer is
  // watching/listening to — micOn describes what this viewer is sending.
  const micStreamRef = useRef(null);
  const micPubRef = useRef(null);
  const [micOn, setMicOn] = useState(false);
  const [micError, setMicError] = useState(null);

  useEffect(() => {
    if (!enabled || !url || !token) return undefined;

    let cancelled = false;
    let attempt = 0;
    let retryTimer = null;

    const connectOnce = async () => {
      const room = new Room();
      roomRef.current = room;

      const onSubscribed = (track) => {
        if (mediaRef.current) track.attach(mediaRef.current);
        if (track.kind === Track.Kind.Video) setHasVideo(true);
        else if (track.kind === Track.Kind.Audio) setHasAudio(true);
      };
      const onUnsubscribed = (track) => {
        track.detach();
        if (track.kind === Track.Kind.Video) setHasVideo(false);
        else if (track.kind === Track.Kind.Audio) setHasAudio(false);
      };

      room.on(RoomEvent.TrackSubscribed, onSubscribed);
      room.on(RoomEvent.TrackUnsubscribed, onUnsubscribed);
      room.on(RoomEvent.Reconnecting, () => {
        if (!cancelled) setReconnecting(true);
      });
      room.on(RoomEvent.Reconnected, () => {
        if (!cancelled) {
          setReconnecting(false);
          setConnected(true);
          setError(null);
        }
      });
      room.on(RoomEvent.Disconnected, () => {
        room.off(RoomEvent.TrackSubscribed, onSubscribed);
        room.off(RoomEvent.TrackUnsubscribed, onUnsubscribed);
        setHasVideo(false);
        setHasAudio(false);
        if (cancelled) return;
        setConnected(false);
        scheduleRetry();
      });

      await room.connect(url, token);
      if (cancelled) {
        room.disconnect();
        return;
      }
      attempt = 0;
      setConnected(true);
      setReconnecting(false);
      setError(null);
    };

    const scheduleRetry = () => {
      if (cancelled) return;
      if (attempt >= MAX_RECONNECT_ATTEMPTS) {
        setReconnecting(false);
        setError("Lost connection to the stream and couldn't reconnect. Try refreshing the page.");
        return;
      }
      attempt += 1;
      setReconnecting(true);
      const delay = BASE_RECONNECT_DELAY_MS * 2 ** (attempt - 1);
      retryTimer = setTimeout(() => {
        if (cancelled) return;
        connectOnce().catch(() => scheduleRetry());
      }, delay);
    };

    connectOnce().catch((e) => {
      if (cancelled) return;
      setError(e?.message || "Couldn't connect to the stream");
      scheduleRetry();
    });

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      roomRef.current?.disconnect();
      roomRef.current = null;
      setConnected(false);
      setReconnecting(false);
      setHasVideo(false);
      setHasAudio(false);
      // The mic itself is a real getUserMedia capture (see the effect below) — it does
      // NOT belong to LiveKit and isn't released by room.disconnect(), so it has to be
      // stopped here explicitly or the browser's "mic in use" indicator outlives the page.
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
      micPubRef.current = null;
      setMicOn(false);
    };
  }, [enabled, url, token]);

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