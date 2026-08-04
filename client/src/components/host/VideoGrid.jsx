import { useMemo } from "react";
import { cx } from "../../ui/tokens";
import VideoTile from "./VideoTile";

// The stage. Four layouts over one participant list, so the host directs the show by choosing a
// composition rather than by dragging tiles.
//
// The layout and the pin live in the BROADCAST SETTINGS (server-side, validated by
// clean_settings) — not in local state. That is what makes them shared: every console watching
// the event receives settings.update and converges on the same composition, and it survives a
// refresh because it is on the session row.
//
// Composition rules, in priority order:
//   spotlight     — exactly one feed, full frame. The pinned participant, else the speaker.
//   presentation  — one large feed + a rail of the rest. A live screen share wins the large slot
//                   over any pin, because if somebody is presenting that IS the show.
//   gallery       — every feed equal, sized to fit without scrolling.
//   grid          — the default: equal tiles in a responsive grid.

/** Columns for N equal tiles. Chosen so the last row is never a single stranded tile. */
function gridCols(n) {
  if (n <= 1) return "grid-cols-1";
  if (n <= 4) return "grid-cols-2";
  if (n <= 9) return "grid-cols-3";
  return "grid-cols-4";
}

export default function VideoGrid({
  layout = "grid",
  pinnedIdentity = "",
  // [{ key, participant, videoTrack, audioTrack, label, isLocal, isScreenShare, speaking }]
  feeds,
  onPin,
  className = "",
}) {
  const { primary, rest } = useMemo(() => {
    if (!feeds.length) return { primary: null, rest: [] };

    // A live screen share always claims the large slot in presentation — that is the whole
    // point of the layout, and a pin set before someone started presenting should not hide it.
    const share = feeds.find((f) => f.isScreenShare);
    const pinned = pinnedIdentity
      ? feeds.find((f) => f.participant?.identity === pinnedIdentity || f.key === pinnedIdentity)
      : null;
    const speaking = feeds.find((f) => f.speaking);

    if (layout === "presentation") {
      const lead = share || pinned || speaking || feeds[0];
      return { primary: lead, rest: feeds.filter((f) => f !== lead) };
    }
    if (layout === "spotlight") {
      // Falls back deliberately: a pin whose participant has left must not blank the stage.
      const lead = pinned || share || speaking || feeds[0];
      return { primary: lead, rest: [] };
    }
    return { primary: null, rest: feeds };
  }, [feeds, layout, pinnedIdentity]);

  if (!feeds.length) {
    return (
      <div
        className={cx(
          "grid aspect-video w-full place-items-center rounded-2xl border border-dashed",
          "border-slate-300 bg-slate-50 text-center dark:border-slate-700 dark:bg-slate-900/40",
          className
        )}
      >
        <div className="px-6">
          <p className="text-sm font-medium text-slate-600 dark:text-slate-300">Nothing on stage yet</p>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            Start your preview to see your own camera, or invite a speaker from the People panel.
          </p>
        </div>
      </div>
    );
  }

  if (primary) {
    return (
      <div className={cx("space-y-3", className)}>
        <VideoTile {...primary} onPin={onPin} pinned={primary.participant?.identity === pinnedIdentity}
                   className="aspect-video w-full" />
        {rest.length > 0 && (
          <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-6">
            {rest.map((f) => (
              <VideoTile
                key={f.key}
                {...f}
                onPin={onPin}
                pinned={f.participant?.identity === pinnedIdentity}
                className="aspect-video"
              />
            ))}
          </div>
        )}
      </div>
    );
  }

  // gallery fits everything into one frame; grid keeps a fixed tile aspect and lets the page
  // scroll, which reads better with only two or three people.
  const gallery = layout === "gallery";
  return (
    <div
      className={cx(
        "grid gap-2",
        gridCols(feeds.length),
        gallery ? "aspect-video w-full" : "",
        className
      )}
    >
      {feeds.map((f) => (
        <VideoTile
          key={f.key}
          {...f}
          onPin={onPin}
          pinned={f.participant?.identity === pinnedIdentity}
          className={gallery ? "h-full" : "aspect-video"}
        />
      ))}
    </div>
  );
}
