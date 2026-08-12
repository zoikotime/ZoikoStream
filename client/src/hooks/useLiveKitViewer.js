import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

// Subscribes to whatever the host is publishing and attaches every remote track (video AND
// audio) to ONE <video> element — track.attach() adds to the element's existing srcObject
// rather than replacing it, so camera + mic end up as one combined stream, same as any
// normal <video> with sound. Viewer-only: the token itself is subscribe-only (server-issued,
// can_publish=false), this hook never asks to publish.
//
// Reconnection: livekit-client already retries transient ICE/network blips on its own
// (surfaced here as `reconnecting` via RoomEvent.Reconnecting/Reconnected). It only emits
// Disconnected when it gives up or the server actively closed the room — mirrors
// useLiveKitPublish's handling of the same event on the host side. Without this, a viewer
// whose connection drops (network blip, backgrounded tab) was stuck on a frozen frame
// forever with no recovery attempt.
const MAX_RECONNECT_ATTEMPTS = 5;
const BASE_RECONNECT_DELAY_MS = 1500;

export default function useLiveKitViewer({ enabled, url, token }) {
  const mediaRef = useRef(null);
  const roomRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const [hasAudio, setHasAudio] = useState(false);
  const [error, setError] = useState(null);

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
    };
  }, [enabled, url, token]);

  return { mediaRef, connected, reconnecting, hasVideo, hasAudio, error };
}
