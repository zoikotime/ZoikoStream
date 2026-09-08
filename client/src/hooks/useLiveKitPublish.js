import { useCallback, useEffect, useRef, useState } from "react";
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
// stopOnUnpublish=false, and the Room is constructed with stopLocalTrackOnUnpublish:false so
// livekit-client's own INVOLUNTARY teardown path (Room.on(EngineEvent.Disconnected) ->
// handleDisconnect(this.options.stopLocalTrackOnUnpublish)) can't kill the host's camera
// either. useMediaPreview/Dashboard own stop(), not LiveKit.
//
// `url`/`token` come straight from the socket snapshot (services/broadcast.snapshot_extra):
// the backend mints a publish token (can_publish=true, room = services/livekit.py's
// room_for_event) for any host.
//
// ── TWO SEPARATE FACTS, AND WHY ─────────────────────────────────────────────────────────
// `connected` means the Room's signalling session is up. `publishing` means LiveKit has
// ACKED at least one of our expected tracks and still holds the publication. This hook used
// to report only `connected`, and StudioStage/Dashboard consumed that as "isPublishing" —
// so:
//
//   * a connect that succeeded while publishCurrentTracks() published NOTHING (streamRef
//     emptied by a preview restart between the two awaits) still reported "Live — this feed
//     is being published to viewers" over zero published tracks, and
//   * the backend, which flips Event.status to "live" on the click itself, had nothing to
//     contradict it.
//
// A host seeing their own camera proves only that getUserMedia worked. `publishing` is
// derived from room.localParticipant's real publications (verifyPublications below) and is
// what Dashboard reports back to the server as the authoritative media state.
//
// ── THE RECONNECT/EVICTION LOOP THIS FIXES ──────────────────────────────────────────────
// connectOnce() used to do `new Room()` + `roomRef.current = room` WITHOUT disposing the
// room it was replacing, and the effect cleanup's `disconnect()` was never awaited. Since
// LiveKit permits exactly one connection per identity and EVICTS the older one, every retry
// added a second live connection under the same identity (services/livekit.py's
// secondary(id, "host")). The eviction landed on the previous room as
// DisconnectReason.DUPLICATE_IDENTITY, whose handler called scheduleRetry() again, which
// built yet another room — a self-sustaining loop in which the console repeatedly evicts
// itself, never sustains a publication, and settles on the past-threshold banner:
// "Not publishing — Lost connection to the stream — still trying to reconnect in the
// background." Nothing outside the client could see the cause; the backend just saw no
// track_published webhook ever arrive.
//
// The fix is ownership: disposeRoom() fully tears down the current room and AWAITS it
// before any new connect, and a monotonic `generation` makes every async continuation
// (connect, publish, verify, retry timer) a no-op once it is no longer the newest attempt.
// There is never more than one Room object alive for this hook.
//
// Reconnection: livekit-client retries transient ICE/network blips itself (surfaced as
// `reconnecting` via RoomEvent.Reconnecting/Reconnected). It only emits Disconnected when it
// gives up or the server closed the room. On Reconnected we no longer assume our tracks
// survived — a full reconnect can come back with an empty publication set — so publications
// are re-verified and only the MISSING sources are republished (republishMissing), which is
// what keeps a recovery from duplicating tracks. Retrying never stops permanently (the
// backend's sampler patiently waits out a producer drop rather than ending the event — see
// services/broadcast.py mark_degraded/mark_recovered); past SLOW_ATTEMPTS_AFTER the message
// becomes honest about it, and `retry()` is exposed for an explicit immediate attempt.
const BASE_RECONNECT_DELAY_MS = 1500;
const MAX_RECONNECT_DELAY_MS = 15000;
const SLOW_ATTEMPTS_AFTER = 5;
const jitter = (ms) => ms * (0.7 + Math.random() * 0.6);

// Development builds only, and never under the test runner (which also sets DEV) —
// diagnostics are for a real console session, not for cluttering test output.
const DEV = typeof import.meta !== "undefined"
  && Boolean(import.meta.env?.DEV)
  && import.meta.env?.MODE !== "test";

// Never logs the token — only room/identity/track facts, and only in development builds so
// a production console can't leak participant identities either.
const logPublications = (label, room) => {
  if (!DEV || !room) return;
  try {
    const published = [...room.localParticipant.trackPublications.values()].map((p) => ({
      source: p.source,
      kind: p.kind,
      trackSid: p.trackSid,
      muted: p.isMuted,
      hasTrack: Boolean(p.track),
      readyState: p.track?.mediaStreamTrack?.readyState ?? null,
    }));
    console.log("[publish] " + label, {
      room: room.name,
      identity: room.localParticipant?.identity,
      state: room.state,
      published,
    });
  } catch {
    // Diagnostics must never break publishing.
  }
};

// A publication only counts if LiveKit gave it a trackSid (i.e. the server acked it) AND we
// still hold the local track. `isMuted` deliberately does NOT disqualify it: a host with the
// camera toggled off is publishing a muted track, which viewers handle — that is a different
// state from "no publication exists", and conflating them is what made a muted camera look
// like a broken stream.
const liveVideoPublications = (room) =>
  [...room.localParticipant.videoTrackPublications.values()].filter(
    (p) => p.trackSid && p.track,
  );

const liveAudioPublications = (room) =>
  [...room.localParticipant.audioTrackPublications.values()].filter(
    (p) => p.trackSid && p.track,
  );

export default function useLiveKitPublish({
  enabled, url, token, streamRef, screenTrack, screenAudioTrack, videoTrack,
}) {
  const roomRef = useRef(null);
  // The published video track's publication, so screen share can unpublish/republish it
  // by reference instead of guessing what's currently live.
  const cameraPubRef = useRef(null);
  const screenPubRef = useRef(null);
  const micPubRef = useRef(null);
  // The shared tab/window's own sound, published alongside (never instead of) the mic —
  // a viewer's <video> attaches every subscribed track, so both are simply audible at once.
  const screenAudioPubRef = useRef(null);
  const [connected, setConnected] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [publishError, setPublishError] = useState(null);
  // Confirmed against room.localParticipant's real publications — see the header.
  const [publishing, setPublishing] = useState(false);
  const [publishedVideo, setPublishedVideo] = useState(false);
  const [publishedAudio, setPublishedAudio] = useState(false);

  // Bumped on every (re)connect attempt and on teardown. Every async continuation captures
  // the generation it started in and bails if it is no longer current — this is what
  // guarantees a single owned Room even when connect/publish/disconnect overlap.
  const genRef = useRef(0);
  // Set by the effect so retry() (called from render-land, outside the effect closure) can
  // trigger an immediate attempt without remounting the hook.
  const retryNowRef = useRef(null);

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

    // Every closure below compares against this. Incrementing it in the cleanup is what
    // makes a torn-down effect's in-flight connect/publish/verify harmless.
    genRef.current += 1;
    const myGen = genRef.current;
    const current = () => genRef.current === myGen;

    let attempt = 0;
    let retryTimer = null;

    // Fully release the room we currently own, and WAIT for it. Awaiting matters: LiveKit
    // permits one connection per identity, so a new connect() racing the previous room's
    // in-flight disconnect is exactly what triggers the DUPLICATE_IDENTITY eviction loop
    // described in the header. stopTracks=false — useMediaPreview owns those tracks.
    const disposeRoom = async () => {
      const room = roomRef.current;
      roomRef.current = null;
      cameraPubRef.current = null;
      screenPubRef.current = null;
      screenAudioPubRef.current = null;
      micPubRef.current = null;
      if (!room) return;
      room.removeAllListeners();
      try {
        await room.disconnect(false);
      } catch {
        // Already gone; nothing left to release.
      }
    };

    // The single place that turns real publications into the state the UI and the backend
    // read. Returns whether anything is genuinely published.
    const verifyPublications = (room) => {
      if (!room || room.state !== "connected") {
        if (current()) {
          setPublishing(false);
          setPublishedVideo(false);
          setPublishedAudio(false);
        }
        return false;
      }
      const video = liveVideoPublications(room).length > 0;
      const audio = liveAudioPublications(room).length > 0;
      // "At least the required publication": whichever kinds the host's stream actually
      // has. An audio-only broadcast (camera blocked or absent — see useMediaPreview's
      // acquireWithFallback) is a legitimately publishing broadcast, not a failure.
      const ok = video || audio;
      if (current()) {
        setPublishedVideo(video);
        setPublishedAudio(audio);
        setPublishing(ok);
      }
      logPublications("verify", room);
      return ok;
    };

    // Publishes only the sources that are NOT already published, so a Reconnected that came
    // back with some publications intact can't produce duplicates.
    const republishMissing = async (room) => {
      const stream = streamRef.current;
      const video = stream?.getVideoTracks()[0];
      const audio = stream?.getAudioTracks()[0];
      // Screen share may already be running by the time (re)connect happens — e.g. a
      // reconnect mid-share — so publish whichever video source is actually active rather
      // than always defaulting back to the camera.
      const activeScreen = screenTrackRef.current;

      const published = (source) =>
        [...room.localParticipant.trackPublications.values()]
          .some((p) => p.source === source && p.trackSid && p.track);

      if (activeScreen) {
        if (!published(Track.Source.ScreenShare)) {
          screenPubRef.current = await room.localParticipant.publishTrack(
            activeScreen, { source: Track.Source.ScreenShare },
          );
        }
        if (screenAudioTrackRef.current && !published(Track.Source.ScreenShareAudio)) {
          screenAudioPubRef.current = await room.localParticipant.publishTrack(
            screenAudioTrackRef.current, { source: Track.Source.ScreenShareAudio },
          );
        }
      } else if (video && !published(Track.Source.Camera)) {
        cameraPubRef.current = await room.localParticipant.publishTrack(
          video, { source: Track.Source.Camera },
        );
      }
      if (audio && !published(Track.Source.Microphone)) {
        micPubRef.current = await room.localParticipant.publishTrack(
          audio, { source: Track.Source.Microphone },
        );
      }
    };

    const connectOnce = async () => {
      // One owned room: whatever we had is released, and awaited, BEFORE we connect again.
      await disposeRoom();
      if (!current()) return;

      const room = new Room({ stopLocalTrackOnUnpublish: false });
      roomRef.current = room;

      room.on(RoomEvent.Reconnecting, () => {
        if (!current()) return;
        setReconnecting(true);
        // Publication state is unknown mid-reconnect; claiming it still holds is how a
        // dropped producer kept showing the green "being published to viewers" banner.
        setPublishing(false);
      });
      room.on(RoomEvent.Reconnected, () => {
        if (!current()) return;
        setReconnecting(false);
        setConnected(true);
        setPublishError(null);
        // A full reconnect can come back with an empty publication set. Restore only what
        // is missing, then report the verified result — never assume recovery.
        (async () => {
          try {
            await republishMissing(room);
          } catch (e) {
            if (current()) setPublishError(e?.message || "Couldn't republish after reconnecting");
          }
          if (current()) verifyPublications(room);
        })();
      });
      // LiveKit's own acknowledgement that a local track went up or came down — the only
      // signal that reflects the SFU's view rather than our intent.
      room.on(RoomEvent.LocalTrackPublished, () => verifyPublications(room));
      room.on(RoomEvent.LocalTrackUnpublished, () => verifyPublications(room));
      room.on(RoomEvent.Disconnected, (reason) => {
        if (!current()) return;
        setConnected(false);
        setPublishing(false);
        setPublishedVideo(false);
        setPublishedAudio(false);
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
      if (!current()) {
        await disposeRoom();
        return;
      }
      setConnected(true);
      await republishMissing(room);
      if (!current()) {
        await disposeRoom();
        return;
      }
      // Only a verified publication clears the error and resets the backoff. A connect that
      // published nothing is NOT success — it retries, instead of reporting a healthy live
      // feed that no viewer can see.
      const ok = verifyPublications(room);
      if (!current()) return;
      if (!ok) {
        throw new Error("Connected, but no camera or microphone track could be published");
      }
      attempt = 0;
      setReconnecting(false);
      setPublishError(null);
    };

    const scheduleRetry = () => {
      if (!current()) return;
      attempt += 1;
      // Same reconnecting/publishError contract StudioStage.jsx's publish banner already
      // reads (isReconnecting -> amber "reconnecting", publishError with !isReconnecting ->
      // rose "not publishing"). Crossing this threshold does not end the loop: retries keep
      // going at the capped/jittered cadence while the rose message shows, and retry() (or
      // clicking Go Live) attempts immediately instead of waiting for the next tick.
      const stillFast = attempt <= SLOW_ATTEMPTS_AFTER;
      setReconnecting(stillFast);
      setPublishError(
        stillFast ? null
          : "Lost connection to the stream — still trying to reconnect in the background. "
            + "You can click Go Live again to retry immediately."
      );
      const delay = jitter(Math.min(BASE_RECONNECT_DELAY_MS * 2 ** (attempt - 1), MAX_RECONNECT_DELAY_MS));
      if (retryTimer) clearTimeout(retryTimer);
      retryTimer = setTimeout(() => {
        if (!current()) return;
        connectOnce().catch(() => scheduleRetry());
      }, delay);
    };

    // Explicit immediate retry, exposed as `retry()`. Cancels the pending backoff timer so
    // an operator's click can't race a scheduled attempt into a second connection.
    retryNowRef.current = () => {
      if (!current()) return;
      if (retryTimer) clearTimeout(retryTimer);
      attempt = 0;
      setPublishError(null);
      setReconnecting(true);
      connectOnce().catch(() => scheduleRetry());
    };

    connectOnce().catch((e) => {
      if (!current()) return;
      setPublishError(e?.message || "Couldn't publish to the stream");
      scheduleRetry();
    });

    return () => {
      // Invalidates every in-flight continuation above before anything is torn down.
      genRef.current += 1;
      retryNowRef.current = null;
      if (retryTimer) clearTimeout(retryTimer);
      setConnected(false);
      setReconnecting(false);
      setPublishing(false);
      setPublishedVideo(false);
      setPublishedAudio(false);
      // Fire-and-forget is safe here only because genRef has already moved on: a later
      // connect for a NEW generation calls disposeRoom() itself and awaits it.
      void disposeRoom();
    };
  }, [enabled, url, token, streamRef]);

  const retry = useCallback(() => {
    retryNowRef.current?.();
  }, []);

  // Swaps the published video track when screen share toggles WHILE already connected —
  // the block above only decides what to publish at connect time. Camera and screen share
  // are mutually exclusive here (one video track live at a time), matching the viewer side
  // (useLiveKitViewer attaches every subscribed track to one <video> element, so two
  // simultaneous video tracks would fight over it rather than showing both).
  useEffect(() => {
    const room = roomRef.current;
    if (!room || !connected) return undefined;
    const myGen = genRef.current;
    const current = () => genRef.current === myGen && roomRef.current === room;

    (async () => {
      if (screenTrack && !screenPubRef.current) {
        if (cameraPubRef.current) {
          await room.localParticipant.unpublishTrack(cameraPubRef.current.track, false);
          cameraPubRef.current = null;
        }
        if (current()) {
          screenPubRef.current = await room.localParticipant.publishTrack(screenTrack, { source: Track.Source.ScreenShare });
        }
        if (current() && screenAudioTrack && !screenAudioPubRef.current) {
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
        if (video && current()) {
          cameraPubRef.current = await room.localParticipant.publishTrack(video, { source: Track.Source.Camera });
        }
      }
    })().catch(() => {
      // A failed swap leaves the previous source published; LocalTrackPublished/
      // LocalTrackUnpublished have already re-derived the real state either way.
    });
    return undefined;
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

    const myGen = genRef.current;
    const current = () => genRef.current === myGen && roomRef.current === room;

    (async () => {
      const prevPub = cameraPubRef.current;
      cameraPubRef.current = null;
      if (prevPub) await room.localParticipant.unpublishTrack(prevPub.track, false);
      if (current()) {
        cameraPubRef.current = await room.localParticipant.publishTrack(videoTrack, { source: Track.Source.Camera });
      }
    })().catch(() => {
      // Same reasoning as the screen-share swap above.
    });
    return undefined;
  }, [videoTrack, connected]);

  return {
    connected, reconnecting, publishError,
    publishing, publishedVideo, publishedAudio, retry,
  };
}
