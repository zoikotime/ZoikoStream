// client/src/components/common/SpeakerTile.jsx
// One on-stage video tile: attaches a participant's LiveKit video track to a <video>
// element, falling back to initials while the track isn't publishing yet. Shared by
// the viewer watch page (VideoPlayer) and the host studio (StudioStage) filmstrips.
import { useEffect, useRef } from "react";

export default function SpeakerTile({ participant, initials }) {
  const videoElRef = useRef(null);

  useEffect(() => {
    const track = participant.videoTrack;
    const el = videoElRef.current;
    if (track && el) track.attach(el);
    return () => track?.detach(el);
  }, [participant.videoTrack]);

  return (
    <div className="relative aspect-video overflow-hidden rounded-xl bg-slate-800 ring-1 ring-white/10">
      <video ref={videoElRef} autoPlay playsInline muted={participant.isLocal} className="absolute inset-0 h-full w-full object-cover" />
      {!participant.videoTrack && (
        <div className="absolute inset-0 grid place-items-center">
          <span className="grid h-9 w-9 place-items-center rounded-full bg-white/15 text-xs font-semibold text-white">
            {initials(participant.name)}
          </span>
        </div>
      )}
      <span className="absolute inset-x-0 bottom-0 truncate bg-gradient-to-t from-black/70 to-transparent px-2 py-1 text-[11px] font-medium text-white">
        {participant.name}
      </span>
    </div>
  );
}
