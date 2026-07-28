// client/src/components/moderation/ParticipantsPanel.jsx
// Left panel — participant list (name, joined time, status) with mute / remove.
import { FiMic, FiMicOff, FiUserX } from "react-icons/fi";
import { cx, ACCENT } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import Panel, { ActionButton } from "./Panel";
import { initials } from "../../data/moderation";

const STATUS_TONE = { Active: "success", Muted: "error", Speaker: "info" };
// Displayed status derived from role + mute state (mute never overwrites role).
const statusOf = (p) => (p.muted ? "Muted" : p.role === "Speaker" ? "Speaker" : "Active");

export default function ParticipantsPanel({ participants, onMute, onRemove, className }) {
  return (
    <Panel title="Participants" count={participants.length} className={className}>
      <div className="space-y-1">
        {participants.map((p) => {
          const status = statusOf(p);
          return (
          <div key={p.id} className="group flex items-center gap-3 rounded-xl px-2 py-2 hover:bg-slate-50 dark:hover:bg-slate-800/60">
            <span className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-full text-sm font-semibold", ACCENT[p.accent].chip)}>
              {initials(p.name)}
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">{p.name}</p>
              <p className="truncate text-xs text-slate-400">{p.email}</p>
              <p className="text-xs text-slate-400">Joined {p.joined}</p>
            </div>
            <Badge status={STATUS_TONE[status]}>{status}</Badge>
            <div className="flex items-center gap-0.5 opacity-100 sm:opacity-0 sm:transition sm:group-hover:opacity-100">
              <ActionButton
                icon={p.muted ? FiMic : FiMicOff}
                title={p.muted ? "Unmute" : "Mute"}
                tone="amber"
                onClick={() => onMute(p.id)}
              />
              <ActionButton icon={FiUserX} title="Remove" tone="rose" onClick={() => onRemove(p.id)} />
            </div>
          </div>
          );
        })}
        {participants.length === 0 && <p className="px-2 py-8 text-center text-sm text-slate-400">No participants.</p>}
      </div>
    </Panel>
  );
}
