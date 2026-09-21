import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Room, RoomEvent, Track, VideoQuality } from "livekit-client";
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
// Label a simulcast layer by the height the publisher actually encoded, so the menu can
// never advertise a rendition that does not exist. Against the publisher's h360/h720 ladder
// a 720p webcam yields 720p/360p — there is simply no 1080p entry to offer, and inventing
// one would be the "upscale and call it 1080p" the brief rules out.
export function describeLayers(publication) {
  const layers = publication?.trackInfo?.layers;
  if (!Array.isArray(layers) || layers.length < 2) return [];
  // Two layers can share a height. The publisher's mid preset is h720 while the top layer is
  // always the real capture resolution, so a camera that grants exactly 720p encodes 720p
  // twice (see useLiveKitPublish's ladder note). Both are genuine layers, but listing "720p"
  // twice offers a choice with no visible difference, so collapse by height and keep the
  // highest VideoQuality at each — that is the layer with the bandwidth headroom behind it.
  const byHeight = new Map();
  layers
    .filter((l) => l?.height > 0)
    .forEach((l) => {
      const seen = byHeight.get(l.height);
      if (!seen || l.quality > seen.quality) {
        byHeight.set(l.height, { quality: l.quality, width: l.width, height: l.height, label: `${l.height}p` });
      }
    });
  const distinct = [...byHeight.values()].sort((a, b) => b.height - a.height);
  // Re-applied after collapsing: one distinct rendition is what Auto already does, so
  // offering it as a manual option would be a menu entry that changes nothing.
  return distinct.length < 2 ? [] : distinct;
}

/** Apply a preference to a publication. "auto" caps at HIGH, i.e. no cap at all, which is
 *  what hands the choice back to LiveKit's adaptive/dynacast selection.
 *
 *  setVideoQuality sets requestedMaxQuality, and that is a CEILING, not a pin. With adaptive
 *  streaming on, RemoteTrackPublication.emitTrackUpdate takes the SMALLER of the adaptive
 *  dimensions and the requested layer (livekit-client 2.x), so:
 *
 *    * picking a rendition BELOW what adaptive would choose is honoured exactly — this is
 *      the case that matters, a viewer on a metered or congested link choosing 360p;
 *    * picking the TOP rendition asks for the ceiling to be lifted, but the layer that
 *      actually arrives is still bounded by the player's rendered size x pixelDensity.
 *      A small player therefore keeps receiving the layer that fits it.
 *
 *  That bound is deliberate — it is what stops 1080p being pushed into a 400px box — and it
 *  cannot be lifted per-track in this SDK version; setVideoDimensions is clamped the same
 *  way. Reaching the top layer is a matter of the player being large enough to warrant it
 *  (fullscreen, a wide viewport, or a HiDPI screen now that pixelDensity is "screen"), not
 *  of asking harder here. */
export function applyQuality(publication, preference) {
  if (!publication?.setVideoQuality) return;
  try {
    publication.setVideoQuality(
      preference === "auto" ? VideoQuality.HIGH : preference
    );
  } catch {
    // A publication that is no longer subscribed refuses the call; the next subscribe
    // re-applies the preference anyway.
  }
}

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

export default function useLiveKitViewer({ enabled, url, token, canPublish = false,
                                          onMuteChange }) {
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
  // ── Manual video quality ────────────────────────────────────────────────────────────
  //
  // The publication is held so the quality menu can read the layers the publisher ACTUALLY
  // sent (pub.trackInfo.layers) rather than offer a fixed list. LiveKit simulcast is three
  // levels — VideoQuality LOW/MEDIUM/HIGH — so a hardcoded 1080p/720p/480p/360p menu could
  // never have mapped onto it even if it had been wired up.
  //
  // `preferenceRef` is the viewer's own choice and is re-applied whenever the publication
  // changes (host toggles camera, swaps device, switches to screen share, or the room
  // reconnects), so the control never stays bound to a publication that is gone.
  // The publication is held in a REF, and the derived layers in STATE.
  //
  // That split matters: RemoteTrackPublication.updateInfo() mutates the SAME object in
  // place when the server sends layer metadata, and it emits no event. Deriving the menu
  // from `useMemo([publication])` therefore never recomputed — layers that arrived after
  // TrackSubscribed were invisible for the life of the track, and the menu said "single
  // rendition" forever. Layers are now re-derived on every surrounding track event and
  // stored by value.
  const videoPubRef = useRef(null);
  const [videoLayers, setVideoLayers] = useState([]);
  // Distinct from "one layer": no publication at all, which is the Starting soon / PREVIEW
  // state. Saying "single rendition" there would describe a stream that is not arriving.
  const [hasVideoPublication, setHasVideoPublication] = useState(false);
  const [quality, setQuality] = useState("auto");
  const preferenceRef = useRef("auto");

  const [micOn, setMicOn] = useState(false);
  // Whether a mic track is actually PUBLISHED. State, not a read of micPubRef during render:
  // the prompt that hides itself once publishing begins only hides if this re-renders.
  const [micLive, setMicLive] = useState(false);
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

      // `adaptiveStream: true` is shorthand for `{}`, and an unset pixelDensity means
      // livekit-client sizes the subscription in CSS pixels: it uses 1 on every display
      // whose devicePixelRatio is <= 2 (getPixelDensity, livekit-client 2.x). On a HiDPI
      // screen that asks the SFU for roughly half the pixels the panel is physically
      // painting, and the layer chosen to satisfy it is then upscaled by the browser —
      // soft video on exactly the displays best able to show a sharp picture.
      //
      // "screen" uses the device's real devicePixelRatio, so the requested dimensions
      // describe physical pixels. This does not force a high layer on anyone: adaptive
      // streaming still derives its request from the player's rendered size, and the SFU
      // still drops layers under congestion. It only stops the request being understated.
      const room = new Room({
        adaptiveStream: { pixelDensity: "screen" },
        dynacast: true,
      });
      roomRef.current = room;

      // Re-read the CURRENT publication and refresh the menu from it. Called on every event
      // that can change layer metadata, because updateInfo() mutates in place and announces
      // nothing — a single snapshot at subscribe time is not enough.
      const refreshLayers = () => {
        if (!current()) return;
        const pub = videoPubRef.current;
        const next = describeLayers(pub);
        setHasVideoPublication(Boolean(pub));
        // Compare by value: the publication object is stable, so identity tells us nothing,
        // and setting a fresh array every event would re-render for no reason.
        setVideoLayers((prev) =>
          prev.length === next.length &&
          prev.every((l, i) => l.quality === next[i].quality && l.height === next[i].height)
            ? prev
            : next
        );
      };

      const onSubscribed = (track, publication) => {
        if (!current()) return;
        tracksRef.current.add(track);
        if (track.kind === Track.Kind.Video && publication) {
          videoPubRef.current = publication;
          // Re-apply this viewer's standing preference to the NEW publication. Scoped to
          // this browser's subscription only — it changes nothing for the publisher or for
          // any other viewer.
          applyQuality(publication, preferenceRef.current);
          refreshLayers();
        }
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
        if (track.kind === Track.Kind.Video) {
          videoPubRef.current = null;
          refreshLayers();      // clears the menu rather than leaving stale layers behind
        }
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
      room.on(RoomEvent.TrackPublished, (pub, p) => {
        // Layer metadata usually lands HERE, before (or without) a subscribe. Adopting the
        // publication at this point is what lets the menu populate on its own once the host
        // actually goes live, with no page refresh.
        if (pub?.kind === Track.Kind.Video) {
          videoPubRef.current = pub;
          refreshLayers();
        }
        logSubscription("remote track published", room, { who: p.identity, source: pub.source });
      });
      // Dynacast pausing/resuming a layer changes what is actually available.
      room.on(RoomEvent.TrackStreamStateChanged, refreshLayers);
      room.on(RoomEvent.TrackUnpublished, (pub) => {
        if (pub?.kind === Track.Kind.Video && videoPubRef.current === pub) {
          videoPubRef.current = null;
          refreshLayers();
        }
      });
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

  // ── Publishing this participant's own microphone is EXPLICIT ──────────────────────────
  //
  // This used to call getUserMedia() the instant `canPublish` flipped, so a host promoting
  // somebody reached straight into their browser and opened their microphone. On a site that
  // had already been granted microphone permission there was no prompt at all: the first the
  // person knew of it was their own voice in the room. A host can grant the RIGHT to speak;
  // only the person in front of the microphone can turn it on.
  //
  // So promotion merely ARMS this. The page offers a prompt, and enableMic() runs from that
  // click — which is also the only thing browsers reliably honour for a capture.
  const enableMic = useCallback(async () => {
    const room = roomRef.current;
    if (!room || !connected) {
      setMicError("You're not connected to the event yet. Try again in a moment.");
      return false;
    }
    if (!canPublish) {
      // Not a permission this client may grant itself: the publish right is the host's to
      // give (services/moderation.py -> livekit.set_stage) and LiveKit enforces it anyway.
      setMicError("You're not on stage yet — the host has to invite you first.");
      return false;
    }
    if (micPubRef.current) return true;   // already publishing

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStreamRef.current = stream;
      const track = stream.getAudioTracks()[0];
      micPubRef.current = await room.localParticipant.publishTrack(track, {
        source: Track.Source.Microphone,
      });
      setMicLive(true);
      setMicOn(true);
      setMicError(null);
      onMuteChange?.(false);   // publishing begins unmuted; the host should see that
      return true;
    } catch (e) {
      // A denied prompt is a normal, recoverable outcome rather than a connection error, so
      // it is surfaced separately from `error` and the person can still watch and listen.
      micStreamRef.current?.getTracks().forEach((t) => t.stop());
      micStreamRef.current = null;
      setMicError(e?.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow microphone access in your browser and try again."
        : (e?.message || "Couldn't access your microphone"));
      return false;
    }
  }, [canPublish, connected, onMuteChange]);

  // Losing the right to publish DOES stop publishing, automatically and without asking.
  // Consent governs turning a microphone on, never leaving it on after the grant is gone.
  useEffect(() => {
    const room = roomRef.current;
    if (!room || canPublish || !micPubRef.current) return;
    const pub = micPubRef.current;
    micPubRef.current = null;
    room.localParticipant.unpublishTrack(pub.track, true).catch(() => {});
    micStreamRef.current?.getTracks().forEach((t) => t.stop());
    micStreamRef.current = null;
    setMicLive(false);
    setMicOn(false);
    setMicError(null);
    onMuteChange?.(true);   // no track any more — not "unmuted"
  }, [canPublish, connected, onMuteChange]);

  // Mute/unmute the PUBLISHED track, through LiveKit.
  //
  // This used to set `enabled = false` on the raw MediaStreamTrack. That silences the audio
  // locally and nothing else: LiveKit never learns, so it emits no mute event, the track
  // stays published and unmuted as far as the room is concerned, and the host console went
  // on showing a live, unmuted speaker who was in fact silent. LocalAudioTrack.mute() is the
  // supported call (livekit-client 2.x) and it signals — which is the whole point.
  //
  // The result is then REPORTED to the server (participant.state) rather than assumed by the
  // host: presence is what the host renders, and nothing else may write it.
  const toggleMic = useCallback(async () => {
    const track = micPubRef.current?.track;
    if (!track) return;
    const next = !micOn;
    try {
      if (next) await track.unmute();
      else await track.mute();
    } catch (e) {
      setMicError(e?.message || "Couldn't change your microphone state");
      return;   // state unchanged, and nothing is reported — the host sees the truth
    }
    setMicOn(next);
    onMuteChange?.(!next);
  }, [micOn, onMuteChange]);

  const selectQuality = useCallback((preference) => {
    preferenceRef.current = preference;
    setQuality(preference);
    applyQuality(videoPubRef.current, preference);
  }, []);

  return {
    mediaRef, connected, reconnecting, hasVideo, hasAudio, error,
    // Quality: the layers the publisher really sent, whether a video publication exists at
    // all (distinct from "one layer"), this viewer's choice, and the setter.
    videoLayers, hasVideoPublication, quality, selectQuality,
    // `micLive` is whether a track is actually published — distinct from micOn, which is
    // whether that track is currently enabled. The console learns the same fact from
    // LiveKit's own track webhook, so neither side is guessing.
    micOn, micError, toggleMic, enableMic, micLive,
  };
}
