import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

// Publishes the host's ALREADY-ACQUIRED camera/mic tracks (from useMediaPreview) into the
// LiveKit room once the broadcast actually goes live. Deliberately does not call
// getUserMedia itself — reusing the same tracks the preview already holds means muting
// camera/mic (useMediaPreview's `t.enabled = false`) is instantly reflected to viewers too,
// with no separate mute plumbing.
//
// `url`/`token` come straight from the socket snapshot (services/broadcast.snapshot_extra):
// the backend already mints a publish token for any host, unconditionally, specifically so
// this wiring could be a drop-in later. This is that drop-in.
//
// Reconnection: livekit-client already retries transient ICE/network blips on its own
// (surfaced here as `reconnecting` via RoomEvent.Reconnecting/Reconnected). It only emits
// Disconnected when it gives up or the server actively closed the room — without handling
// that, a host who drops mid-broadcast (network blip, backgrounded tab) is left thinking
// they're live while nothing reaches viewers, with no recovery. On Disconnected we rebuild
// the Room and republish with capped exponential backoff.
const MAX_RECONNECT_ATTEMPTS = 5;
const BASE_RECONNECT_DELAY_MS = 1500;

export default function useLiveKitPublish({ enabled, url, token, streamRef }) {
  const roomRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [publishError, setPublishError] = useState(null);

  useEffect(() => {
    if (!enabled || !url || !token || !streamRef.current) return undefined;

    let cancelled = false;
    let attempt = 0;
    let retryTimer = null;

    const publishCurrentTracks = async (room) => {
      const stream = streamRef.current;
      const video = stream?.getVideoTracks()[0];
      const audio = stream?.getAudioTracks()[0];
      if (video) await room.localParticipant.publishTrack(video, { source: Track.Source.Camera });
      if (audio) await room.localParticipant.publishTrack(audio, { source: Track.Source.Microphone });
    };

    const connectOnce = async () => {
      const room = new Room();
      roomRef.current = room;

      room.on(RoomEvent.Reconnecting, () => {
        if (!cancelled) setReconnecting(true);
      });
      room.on(RoomEvent.Reconnected, () => {
        if (!cancelled) {
          setReconnecting(false);
          setConnected(true);
          setPublishError(null);
        }
      });
      room.on(RoomEvent.Disconnected, () => {
        if (cancelled) return;
        setConnected(false);
        scheduleRetry();
      });

      await room.connect(url, token);
      if (cancelled) {
        room.disconnect();
        return;
      }
      await publishCurrentTracks(room);
      if (!cancelled) {
        attempt = 0;
        setConnected(true);
        setReconnecting(false);
        setPublishError(null);
      }
    };

    const scheduleRetry = () => {
      if (cancelled) return;
      if (attempt >= MAX_RECONNECT_ATTEMPTS) {
        setReconnecting(false);
        setPublishError("Lost connection to the stream and couldn't reconnect. Go live again to resume.");
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
      setPublishError(e?.message || "Couldn't publish to the stream");
      scheduleRetry();
    });

    return () => {
      cancelled = true;
      if (retryTimer) clearTimeout(retryTimer);
      setConnected(false);
      setReconnecting(false);
      roomRef.current?.disconnect();
      roomRef.current = null;
    };
  }, [enabled, url, token, streamRef]);

  return { connected, reconnecting, publishError };
}
