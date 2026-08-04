import { useMemo } from "react";

// The stage roster, built from REAL tracks: this machine's own monitor, every remote feed LiveKit
// is delivering, and this machine's screen share as its own tile.
//
// Extracted from components/host/StudioStage so the SPEAKER console renders the same stage from
// the same rules. It was the one non-trivial piece of the host stage that a second console would
// otherwise have had to reimplement — and two copies of "which tile is which" is how the two
// consoles end up disagreeing about who is on screen.
//
// A participant with no published video gets an avatar tile rather than being hidden: "connected
// with the camera off" is a state everyone on the stage needs to see, not an absence.

export default function useStageFeeds({
  participants = [], publisher, stream, previewActive, camera, mic, selfLabel = "You",
}) {
  // Presence keyed by identity, so a tile can carry that person's mute/quality/hand state.
  const byIdentity = useMemo(
    () => Object.fromEntries(participants.map((p) => [p.identity, p])),
    [participants]
  );

  return useMemo(() => {
    const out = [];
    // The publisher connects with a SUFFIXED identity ("<uid>#host"); presence is keyed on the
    // bare id, so `me` is looked up by the suffixed one only because that is what the publisher
    // reports. base_identity stripping happens on the remote side below.
    const me = participants.find((p) => p.identity === publisher?.identity) || null;

    if (previewActive && stream) {
      out.push({
        key: "self",
        participant: me || { identity: publisher?.identity, name: selfLabel, muted: !mic },
        stream: camera ? stream : null,
        label: me?.name || selfLabel,
        isLocal: true,
        speaking: false,
      });
    }

    for (const r of publisher?.remotes || []) {
      out.push({
        key: r.identity,
        participant: byIdentity[r.baseIdentity] || byIdentity[r.identity]
          || { identity: r.baseIdentity, name: r.name },
        videoTrack: r.videoTrack,
        audioTrack: r.audioTrack,
        label: r.name,
        isScreenShare: r.source === "screen_share",
        speaking: r.speaking,
      });
    }

    if (publisher?.screenSharing) {
      out.push({
        key: "self-screen",
        participant: { identity: `${publisher?.identity}-screen`, name: "Your screen" },
        label: "Your screen",
        isLocal: true,
        isScreenShare: true,
        // No local element for the share: LiveKit holds the track and re-attaching it here would
        // need a second video element for no benefit. The tile's "Screen" badge is the
        // confirmation that it is going out.
      });
    }
    return out;
  }, [participants, publisher, previewActive, stream, camera, mic, byIdentity, selfLabel]);
}
