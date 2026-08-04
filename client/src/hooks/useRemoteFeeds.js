import { useCallback, useEffect, useState } from "react";

/**
 * Collect every remote publisher from a LiveKit Room into render-ready feeds.
 *
 * A hook rather than a helper because it has to re-run on LiveKit's events, and the Room itself
 * must stay a ref (it is an imperative handle, not a render input — holding it in state re-renders
 * the page on every track event).
 *
 * A participant publishing BOTH a camera and a screen share becomes TWO feeds. That is the point:
 * collapsing them would mean choosing, and the choice is exactly what the layout is for.
 */
export default function useRemoteFeeds(roomRef, sdkRef, phase) {
  const [feeds, setFeeds] = useState([]);

  const sync = useCallback(() => {
    const room = roomRef.current;
    const sdk = sdkRef.current;
    if (!room || !sdk) {
      setFeeds([]);
      return;
    }
    const out = [];
    room.remoteParticipants.forEach((p) => {
      const audio = [...p.trackPublications.values()].find((pub) => pub.kind === "audio");
      const videos = [...p.trackPublications.values()].filter((pub) => pub.kind === "video" && pub.track);
      const base = {
        // The publisher connects with a "#host" suffix (services/broadcast.publisher_identity);
        // strip it so the label reads as a person, not a connection.
        name: p.name || (p.identity || "").split("#")[0],
        speaking: p.isSpeaking,
        audioMuted: !!audio?.isMuted,
      };
      if (!videos.length) {
        // Audio-only or camera-off: still a feed. Somebody talking with their camera off is on the
        // stage, and dropping them would make the room look emptier than it is.
        out.push({ ...base, key: p.identity, videoTrack: null, videoMuted: true, isScreenShare: false });
        return;
      }
      for (const pub of videos) {
        const isShare = pub.source === sdk.Track.Source.ScreenShare;
        out.push({
          ...base,
          key: `${p.identity}${isShare ? ":screen" : ""}`,
          videoTrack: pub.track,
          videoMuted: !!pub.isMuted,
          isScreenShare: isShare,
        });
      }
    });
    // A screen share first, then whoever is speaking — so the default spotlight pick is stable
    // rather than dependent on subscription order.
    out.sort((a, b) => Number(b.isScreenShare) - Number(a.isScreenShare)
      || Number(b.speaking) - Number(a.speaking));
    setFeeds(out);
  }, [roomRef, sdkRef]);

  // Re-sync when playback starts or stops; the room's own event handlers call `sync` directly.
  useEffect(() => {
    if (phase !== "playing") {
      const id = setTimeout(() => setFeeds([]), 0);
      return () => clearTimeout(id);
    }
    sync();
    return undefined;
  }, [phase, sync]);

  return { feeds, syncFeeds: sync };
}
