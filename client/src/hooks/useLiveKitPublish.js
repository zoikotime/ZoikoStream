import { useEffect, useRef, useState } from "react";
import { Room, Track } from "livekit-client";

// Publishes the host's ALREADY-ACQUIRED camera/mic tracks (from useMediaPreview) into the
// LiveKit room once the broadcast actually goes live. Deliberately does not call
// getUserMedia itself — reusing the same tracks the preview already holds means muting
// camera/mic (useMediaPreview's `t.enabled = false`) is instantly reflected to viewers too,
// with no separate mute plumbing.
//
// `url`/`token` come straight from the socket snapshot (services/broadcast.snapshot_extra):
// the backend already mints a publish token for any host, unconditionally, specifically so
// this wiring could be a drop-in later. This is that drop-in.
export default function useLiveKitPublish({ enabled, url, token, streamRef }) {
  const roomRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [publishError, setPublishError] = useState(null);

  useEffect(() => {
    if (!enabled || !url || !token || !streamRef.current) return undefined;

    let cancelled = false;
    const room = new Room();
    roomRef.current = room;

    (async () => {
      try {
        await room.connect(url, token);
        if (cancelled) {
          room.disconnect();
          return;
        }
        const stream = streamRef.current;
        const video = stream?.getVideoTracks()[0];
        const audio = stream?.getAudioTracks()[0];
        if (video) await room.localParticipant.publishTrack(video, { source: Track.Source.Camera });
        if (audio) await room.localParticipant.publishTrack(audio, { source: Track.Source.Microphone });
        if (!cancelled) {
          setConnected(true);
          setPublishError(null);
        }
      } catch (e) {
        if (!cancelled) setPublishError(e?.message || "Couldn't publish to the stream");
      }
    })();

    return () => {
      cancelled = true;
      setConnected(false);
      room.disconnect();
      roomRef.current = null;
    };
  }, [enabled, url, token, streamRef]);

  return { connected, publishError };
}
