import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

// Subscribes to whatever the host is publishing and attaches every remote track (video AND
// audio) to ONE <video> element — track.attach() adds to the element's existing srcObject
// rather than replacing it, so camera + mic end up as one combined stream, same as any
// normal <video> with sound. Viewer-only: the token itself is subscribe-only (server-issued,
// can_publish=false), this hook never asks to publish.
export default function useLiveKitViewer({ enabled, url, token }) {
  const mediaRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);
  const [hasAudio, setHasAudio] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!enabled || !url || !token) return undefined;

    let cancelled = false;
    const room = new Room();

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
    const onDisconnected = () => {
      if (!cancelled) setConnected(false);
    };

    room.on(RoomEvent.TrackSubscribed, onSubscribed);
    room.on(RoomEvent.TrackUnsubscribed, onUnsubscribed);
    room.on(RoomEvent.Disconnected, onDisconnected);

    room
      .connect(url, token)
      .then(() => {
        if (cancelled) {
          room.disconnect();
          return;
        }
        setConnected(true);
        setError(null);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || "Couldn't connect to the stream");
      });

    return () => {
      cancelled = true;
      room.off(RoomEvent.TrackSubscribed, onSubscribed);
      room.off(RoomEvent.TrackUnsubscribed, onUnsubscribed);
      room.off(RoomEvent.Disconnected, onDisconnected);
      room.disconnect();
      setConnected(false);
      setHasVideo(false);
      setHasAudio(false);
    };
  }, [enabled, url, token]);

  return { mediaRef, connected, hasVideo, hasAudio, error };
}
