import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";
import { fatalDisconnect } from "./livekitDisconnect";

// Publishes the host's ALREADY-ACQUIRED camera/mic tracks (from useMediaPreview) into the
// LiveKit room once the broadcast actually goes live. Deliberately does not call
// getUserMedia itself — reusing the same tracks the preview already holds means muting
// camera/mic (useMediaPreview's `t.enabled = false`) is instantly reflected to viewers too,
// with no separate mute plumbing.
//
// Because the tracks are shared with the local preview, LiveKit must never be the one to
// .stop() them — every disconnect()/unpublishTrack() call below passes stopTracks/
// stopOnUnpublish=false. livekit-client's default is to stop the underlying
// MediaStreamTrack on unpublish/disconnect (it assumes it owns whatever it's given); left
// at that default, any reconnect (a network blip, or React StrictMode's dev-only double-
// invoke of this effect) silently kills the host's own camera preview with no recovery —
// useMediaPreview never learns the track died. useMediaPreview/Dashboard own stop(), not
// LiveKit.
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
// the Room and republish with capped exponential backoff + jitter — same shape as
// hooks/useEventStream.js's proven reconnect loop for the chat/control socket (jitter matters
// at scale: without it, every host who drops on the same server blip retries in lockstep).
//
// Audit fix: this used to give up permanently after MAX_RECONNECT_ATTEMPTS and tell the host
// to "Go live again to resume" — a dead end for what could be a purely transient outage that
// the BACKEND is already patiently waiting out (services/broadcast.py's sampler marks the
// event "degraded", not "ended", and self-recovers once publishing resumes — see
// mark_degraded/mark_recovered). Retrying now never truly stops on its own; after
// SLOW_ATTEMPTS_AFTER the message changes from "reconnecting" to an honest "still trying,
// but you can go live again" rather than claiming recovery is impossible.
const BASE_RECONNECT_DELAY_MS = 1500;
const MAX_RECONNECT_DELAY_MS = 15000;
const SLOW_ATTEMPTS_AFTER = 5;
const jitter = (ms) => ms * (0.7 + Math.random() * 0.6);

export default function useLiveKitPublish({
  enabled, url, token, streamRef, screenTrack, screenAudioTrack, videoTrack,
}) {
  const roomRef = useRef(null);
  // The published video track's publication, so screen share can unpublish/republish it
  // by reference instead of guessing what's currently live.
  const cameraPubRef = useRef(null);
  const screenPubRef = useRef(null);
  // The shared tab/window's own sound, published alongside (never instead of) the mic —
  // a viewer's <video> attaches every subscribed track, so both are simply audible at once.
  const screenAudioPubRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [publishError, setPublishError] = useState(null);

  // Latest screenTrack/screenAudioTrack without making the connect effect below re-run on
  // every toggle — that effect only needs to know what's active the moment it (re)connects;
  // the swap effect further down handles a toggle while already connected.
  const screenTrackRef = useRef(screenTrack);
  const screenAudioTrackRef = useRef(screenAudioTrack);
  useEffect(() => {
    screenTrackRef.current = screenTrack;
    screenAudioTrackRef.current = screenAudioTrack;
  });

  useEffect(() => {
    if (!enabled || !url || !token || !streamRef.current) return undefined;

    let cancelled = false;
    let attempt = 0;
    let retryTimer = null;

    const publishCurrentTracks = async (room) => {
      const stream = streamRef.current;
      const video = stream?.getVideoTracks()[0];
      const audio = stream?.getAudioTracks()[0];
      // Screen share may already be running by the time (re)connect happens — e.g. a
      // reconnect mid-share — so publish whichever video source is actually active rather
      // than always defaulting back to the camera.
      const activeScreen = screenTrackRef.current;
      if (activeScreen) {
        screenPubRef.current = await room.localParticipant.publishTrack(activeScreen, { source: Track.Source.ScreenShare });
        if (screenAudioTrackRef.current) {
          screenAudioPubRef.current = await room.localParticipant.publishTrack(
            screenAudioTrackRef.current, { source: Track.Source.ScreenShareAudio },
          );
        }
      } else if (video) {
        cameraPubRef.current = await room.localParticipant.publishTrack(video, { source: Track.Source.Camera });
      }
      if (audio) await room.localParticipant.publishTrack(audio, { source: Track.Source.Microphone });
    };

    const connectOnce = async () => {
      // stopLocalTrackOnUnpublish:false is what actually makes the contract in this file's
      // header true. Passing `false` to disconnect()/unpublishTrack() below only covers the
      // calls WE make; livekit-client defaults this room option to true (roomOptionDefaults)
      // and its own INVOLUNTARY teardown path ignores our argument entirely —
      // Room.on(EngineEvent.Disconnected) calls handleDisconnect(this.options
      // .stopLocalTrackOnUnpublish), which does pub.track.stop() on the raw MediaStreamTrack
      // that useMediaPreview owns and still has on screen. So a real network drop killed the
      // host's camera and microphone outright: the OS camera light went out, the local
      // preview froze on its last frame, and the retry below then republished tracks whose
      // readyState was already "ended" while the stage showed the green "Live — this feed is
      // being published to viewers." useMediaPreview never learns, because nothing stops it.
      // Only real disconnects reach this path, which is why fake-media E2E never saw it.
      const room = new Room({ stopLocalTrackOnUnpublish: false });
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
      room.on(RoomEvent.Disconnected, (reason) => {
        if (cancelled) return;
        setConnected(false);
        // Some disconnect reasons are never fixed by retrying (see livekitDisconnect.js) —
        // most importantly DUPLICATE_IDENTITY, where retrying is actively counterproductive:
        // this same host running the console in two tabs (or the console + Backstage) each
        // evict the other's connection, so an infinite retry loop here would just keep
        // kicking itself. Stop immediately and say so instead of "still trying" forever.
        const fatal = fatalDisconnect(reason);
        if (fatal) {
          setReconnecting(false);
          setPublishError(fatal);
          return;
        }
        scheduleRetry();
      });

      await room.connect(url, token);
      if (cancelled) {
        room.disconnect(false);
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
      attempt += 1;
      // Same reconnecting/publishError contract StudioStage.jsx's publish banner already
      // reads (isReconnecting -> amber "reconnecting", publishError with !isReconnecting ->
      // rose "not publishing") — what changed is that crossing this threshold no longer ends
      // the loop below. Retries keep going in the background at the capped/jittered cadence
      // even while the rose message is showing; the host can also click Go Live at any time
      // to remount this hook (Dashboard.jsx gates `enabled` on broadcast.status) and retry
      // immediately instead of waiting for the next scheduled attempt.
      const stillFast = attempt <= SLOW_ATTEMPTS_AFTER;
      setReconnecting(stillFast);
      setPublishError(
        stillFast ? null
          : "Lost connection to the stream — still trying to reconnect in the background. "
            + "You can click Go Live again to retry immediately."
      );
      const delay = jitter(Math.min(BASE_RECONNECT_DELAY_MS * 2 ** (attempt - 1), MAX_RECONNECT_DELAY_MS));
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
      roomRef.current?.disconnect(false);
      roomRef.current = null;
      cameraPubRef.current = null;
      screenPubRef.current = null;
      screenAudioPubRef.current = null;
    };
  }, [enabled, url, token, streamRef]);

  // Swaps the published video track when screen share toggles WHILE already connected —
  // the block above only decides what to publish at connect time. Camera and screen share
  // are mutually exclusive here (one video track live at a time), matching the viewer side
  // (useLiveKitViewer attaches every subscribed track to one <video> element, so two
  // simultaneous video tracks would fight over it rather than showing both).
  useEffect(() => {
    const room = roomRef.current;
    if (!room || !connected) return undefined;

    let cancelled = false;
    (async () => {
      if (screenTrack && !screenPubRef.current) {
        if (cameraPubRef.current) {
          await room.localParticipant.unpublishTrack(cameraPubRef.current.track, false);
          cameraPubRef.current = null;
        }
        if (!cancelled) {
          screenPubRef.current = await room.localParticipant.publishTrack(screenTrack, { source: Track.Source.ScreenShare });
        }
        if (!cancelled && screenAudioTrack && !screenAudioPubRef.current) {
          screenAudioPubRef.current = await room.localParticipant.publishTrack(
            screenAudioTrack, { source: Track.Source.ScreenShareAudio },
          );
        }
      } else if (!screenTrack && screenPubRef.current) {
        await room.localParticipant.unpublishTrack(screenPubRef.current.track, false);
        screenPubRef.current = null;
        if (screenAudioPubRef.current) {
          await room.localParticipant.unpublishTrack(screenAudioPubRef.current.track, false);
          screenAudioPubRef.current = null;
        }
        const video = streamRef.current?.getVideoTracks()[0];
        if (video && !cancelled) {
          cameraPubRef.current = await room.localParticipant.publishTrack(video, { source: Track.Source.Camera });
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [screenTrack, screenAudioTrack, connected, streamRef]);

  // Swaps the published camera track when it changes identity WHILE already connected —
  // e.g. useMediaPreview's flipCamera, which tears down and reacquires a whole new
  // MediaStream/track rather than just muting the current one. Without this, the old
  // (by-then-stopped) track stays "published" and viewers freeze on its last frame.
  // Skipped while screen sharing owns the video slot — the block above already restores
  // the (by-then-current) camera track from streamRef once sharing ends.
  useEffect(() => {
    const room = roomRef.current;
    if (!room || !connected) return undefined;
    if (screenTrackRef.current || screenPubRef.current) return undefined;
    if (!videoTrack) return undefined;
    // .track.mediaStreamTrack, not .track: publishTrack() returns a LocalTrackPublication
    // whose .track is a livekit LocalVideoTrack WRAPPING the MediaStreamTrack we handed in,
    // so comparing it directly against the raw videoTrack was never equal. The guard could
    // therefore never short-circuit, and this effect unpublished and republished the camera
    // every time it ran — a visible blip for viewers on each reconnect, for a track that had
    // not actually changed.
    if (cameraPubRef.current?.track?.mediaStreamTrack === videoTrack) return undefined;

    let cancelled = false;
    (async () => {
      const prevPub = cameraPubRef.current;
      cameraPubRef.current = null;
      if (prevPub) await room.localParticipant.unpublishTrack(prevPub.track, false);
      if (!cancelled) {
        cameraPubRef.current = await room.localParticipant.publishTrack(videoTrack, { source: Track.Source.Camera });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [videoTrack, connected]);

  return { connected, reconnecting, publishError };
}
