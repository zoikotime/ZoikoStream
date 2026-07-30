// client/src/lib/useLiveKitRoom.js
// Shared LiveKit plumbing for the viewer watch page and the host studio: connecting
// (viewer side), and deriving a live "who's on stage with what tracks" roster from
// whatever Room is currently connected (either one this hook connected, or one the
// caller already has -- e.g. the host's broadcast connection from Dashboard.jsx).
import { useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

// Connects a subscriber Room for `liveToken` ({ token, livekit_url }) and tears it
// down on unmount / token change. Used by viewers -- the host's Room is created
// separately in Dashboard.jsx (it also needs to *publish* its own camera/mic).
export function useLiveKitRoom(liveToken) {
  const [room, setRoom] = useState(null);
  const [connected, setConnected] = useState(false);
  const roomRef = useRef(null);

  useEffect(() => {
    if (!liveToken) return;
    // React StrictMode double-invokes effects in dev: mount -> cleanup -> mount again.
    // Without this `cancelled` guard, the FIRST run's r.connect() can still resolve
    // after its own cleanup already disconnected it -- overwriting `room` with a dead
    // connection the SECOND (real) run never gets to correct, leaving the viewer stuck
    // on "Connecting..." forever even though a room genuinely connected underneath.
    let cancelled = false;
    const r = new Room();

    r.on(RoomEvent.Connected, () => { if (!cancelled) setConnected(true); });
    r.on(RoomEvent.Disconnected, () => { if (!cancelled) setConnected(false); });

    r.connect(liveToken.livekit_url, liveToken.token)
      .then(() => {
        if (cancelled) {
          r.disconnect();
          return;
        }
        roomRef.current = r;
        setRoom(r);
      })
      .catch(() => { if (!cancelled) setConnected(false); });

    return () => {
      cancelled = true;
      r.disconnect();
      if (roomRef.current === r) roomRef.current = null;
      setRoom(null);
      setConnected(false);
    };
  }, [liveToken]);

  return { room, connected };
}

// Given an already-connected Room (from useLiveKitRoom above, or an externally-owned
// one like the host's broadcast connection), derive a live identity -> tile map plus
// whether the local participant currently has publish rights.
export function useRoomParticipants(room) {
  const [participants, setParticipants] = useState({});
  const [localCanPublish, setLocalCanPublish] = useState(false);

  useEffect(() => {
    if (!room) return undefined;

    const upsert = (participant) => {
      setParticipants((prev) => ({
        ...prev,
        [participant.identity]: {
          identity: participant.identity,
          name: participant.name || participant.identity,
          isLocal: participant === room.localParticipant,
          videoTrack: prev[participant.identity]?.videoTrack ?? null,
          audioTrack: prev[participant.identity]?.audioTrack ?? null,
        },
      }));
    };

    const remove = (participant) => {
      setParticipants((prev) => {
        const next = { ...prev };
        delete next[participant.identity];
        return next;
      });
    };

    const onTrackSubscribed = (track, _pub, participant) => {
      upsert(participant);
      setParticipants((prev) => {
        const tile = prev[participant.identity];
        if (!tile) return prev;
        const key = track.kind === Track.Kind.Video ? "videoTrack" : "audioTrack";
        return { ...prev, [participant.identity]: { ...tile, [key]: track } };
      });
    };

    const onTrackUnsubscribed = (track, _pub, participant) => {
      setParticipants((prev) => {
        const tile = prev[participant.identity];
        if (!tile) return prev;
        const key = track.kind === Track.Kind.Video ? "videoTrack" : "audioTrack";
        return { ...prev, [participant.identity]: { ...tile, [key]: null } };
      });
    };

    const onPermissionsChanged = (_prev, participant) => {
      if (participant === room.localParticipant) {
        setLocalCanPublish(!!participant.permissions?.canPublish);
      }
    };

    // TrackSubscribed only fires for tracks subscribed AFTER this listener attaches --
    // by the time this effect runs, the room may already have been connected for a
    // while (the host went live before this viewer joined), so its tracks were already
    // subscribed at the LiveKit level and that event already fired and is gone. Without
    // this, the tile is created with videoTrack/audioTrack stuck at null forever: the
    // participant shows up, but no video ever appears -- "Connecting..." indefinitely.
    const seedTracks = (participant) => {
      participant.trackPublications.forEach((pub) => {
        if (!pub.track) return;
        setParticipants((prev) => {
          const tile = prev[participant.identity];
          if (!tile) return prev;
          const key = pub.track.kind === Track.Kind.Video ? "videoTrack" : "audioTrack";
          return { ...prev, [participant.identity]: { ...tile, [key]: pub.track } };
        });
      });
    };

    room.on(RoomEvent.ParticipantConnected, upsert);
    room.on(RoomEvent.ParticipantDisconnected, remove);
    room.on(RoomEvent.TrackSubscribed, onTrackSubscribed);
    room.on(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
    room.on(RoomEvent.ParticipantPermissionsChanged, onPermissionsChanged);
    room.on(RoomEvent.LocalTrackPublished, () => upsert(room.localParticipant));

    upsert(room.localParticipant);
    seedTracks(room.localParticipant);
    onPermissionsChanged(null, room.localParticipant);
    room.remoteParticipants.forEach((p) => {
      upsert(p);
      seedTracks(p);
    });

    return () => {
      room.off(RoomEvent.ParticipantConnected, upsert);
      room.off(RoomEvent.ParticipantDisconnected, remove);
      room.off(RoomEvent.TrackSubscribed, onTrackSubscribed);
      room.off(RoomEvent.TrackUnsubscribed, onTrackUnsubscribed);
      room.off(RoomEvent.ParticipantPermissionsChanged, onPermissionsChanged);
      setParticipants({});
      setLocalCanPublish(false);
    };
  }, [room]);

  return { participants: Object.values(participants), localCanPublish };
}
