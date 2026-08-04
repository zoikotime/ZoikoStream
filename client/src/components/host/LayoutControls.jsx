import { FiCrosshair, FiGrid, FiMonitor, FiUsers, FiWifi, FiWifiOff } from "react-icons/fi";
import Badge from "../../ui/Badge";
import { cx, focusRing } from "../../ui/tokens";
import { LAYOUTS } from "../../data/host";

// Stage composition + the publisher's real condition, in one strip above the video.
//
// Choosing a layout sends broadcast.settings, so it persists on the session row and every other
// console receives settings.update and converges. It is a shared directorial decision, not a
// local view preference — which is exactly why it is not component state.

const LAYOUT_ICON = { grid: FiGrid, gallery: FiUsers, presentation: FiMonitor, spotlight: FiCrosshair };

/** Publisher condition, stated from measurements rather than assumed from "are we live". */
function PublishStatus({ publisher }) {
  const { status, publishing, stats } = publisher || {};
  if (status === "idle") {
    return <Badge tone="neutral" size="sm">Not publishing</Badge>;
  }
  if (status === "connecting") {
    return <Badge tone="info" size="sm" dot>Joining room…</Badge>;
  }
  if (status === "reconnecting") {
    return <Badge tone="warning" size="sm" dot>Reconnecting…</Badge>;
  }
  if (status === "failed") {
    return (
      <Badge tone="danger" size="sm" icon={FiWifiOff}>Publish failed</Badge>
    );
  }
  if (!publishing) {
    return <Badge tone="warning" size="sm" dot>Connected, no media</Badge>;
  }
  // Thresholds are about what a VIEWER would notice, not round numbers: past ~3% loss WebRTC
  // starts visibly degrading, and past 300ms round-trip interaction stops feeling live.
  const loss = stats?.packetLoss ?? 0;
  const rtt = stats?.rttMs ?? 0;
  const tone = loss > 5 || rtt > 400 ? "danger" : loss > 2 || rtt > 250 ? "warning" : "success";
  const label = tone === "success" ? "Publishing" : tone === "warning" ? "Publishing · degraded" : "Publishing · poor";
  return <Badge tone={tone} size="sm" icon={FiWifi}>{label}</Badge>;
}

export default function LayoutControls({
  layout, pinnedIdentity, participants = [], publisher, onLayout, onPin,
}) {
  const pinned = participants.find((p) => p.identity === pinnedIdentity);

  return (
    <div className="flex flex-wrap items-center gap-3 rounded-xl border border-slate-200 bg-white px-3 py-2 dark:border-slate-800 dark:bg-slate-900">
      <div role="group" aria-label="Stage layout" className="flex items-center gap-1">
        {LAYOUTS.map(({ value, label, hint }) => {
          const Icon = LAYOUT_ICON[value] || FiGrid;
          const active = layout === value;
          return (
            <button
              key={value}
              type="button"
              onClick={() => onLayout(value)}
              aria-pressed={active}
              title={`${label} — ${hint}`}
              className={cx(
                "inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition",
                focusRing,
                active
                  ? "bg-violet-600 text-white"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"
              )}
            >
              <Icon className="text-sm" aria-hidden="true" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          );
        })}
      </div>

      {pinnedIdentity && (
        <span className="inline-flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
          <FiCrosshair aria-hidden="true" />
          Pinned:{" "}
          <strong className="font-semibold text-slate-700 dark:text-slate-200">
            {pinned?.name || "someone who has left"}
          </strong>
          <button
            type="button"
            onClick={() => onPin("")}
            className={cx("rounded px-1 font-semibold text-violet-600 hover:underline dark:text-violet-400", focusRing)}
          >
            clear
          </button>
        </span>
      )}

      <span className="ml-auto flex items-center gap-2">
        {publisher?.screenSharing && (
          <Badge tone="success" size="sm" icon={FiMonitor}>Sharing screen</Badge>
        )}
        <PublishStatus publisher={publisher} />
      </span>
    </div>
  );
}
