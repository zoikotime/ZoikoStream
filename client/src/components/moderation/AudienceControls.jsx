// client/src/components/moderation/AudienceControls.jsx
// The moderator's levers over the ROOM, as opposed to over one person: chat on/off, slow mode,
// the automatic filters, the waiting room, hand-raising.
//
// Every switch here is a real, server-ENFORCED setting. They travel on the same
// broadcast.settings action the host's settings modal uses, and the server filters that patch
// by role (services/broadcast.MODERATOR_SETTINGS) — so this cannot become a way to change the
// host's encoder targets, and none of these toggles is a decoration.
//
// The option lists come from data/host.js unchanged: the host's settings modal and this block
// must never disagree about what "Spam & link filter" means.
import { FiSliders } from "react-icons/fi";
import { Select, Switch, Label } from "../../ui/forms";
import Panel from "./Panel";
import { CHAT_CONTROLS, STAGE_CONTROLS, SLOW_MODE_OPTIONS } from "../../data/host";

export default function AudienceControls({ settings, canModerate, send, className }) {
  const s = settings || {};
  const set = (patch) => send("broadcast.settings", { settings: patch });

  return (
    <Panel title="Audience controls" scroll={false} className={className}>
      {!canModerate && (
        <p className="mb-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          You're viewing this console read-only.
        </p>
      )}
      <div className="space-y-3">
        <div className="space-y-1.5">
          <p className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            <FiSliders aria-hidden="true" /> Chat
          </p>
          {CHAT_CONTROLS.map((t) => (
            <Switch
              key={t.key}
              checked={s[t.key] !== false}
              onChange={(v) => set({ [t.key]: v })}
              label={t.label}
              accent="emerald"
              className={canModerate ? "" : "pointer-events-none opacity-50"}
            />
          ))}
          <div>
            <Label variant="console">Slow mode</Label>
            <Select
              variant="console"
              value={s.slow_mode_seconds ?? 0}
              disabled={!canModerate}
              onChange={(e) => set({ slow_mode_seconds: Number(e.target.value) })}
              aria-label="Slow mode"
            >
              {SLOW_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </Select>
          </div>
        </div>

        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Participation
          </p>
          {STAGE_CONTROLS.map((t) => (
            <Switch
              key={t.key}
              checked={s[t.key] !== false}
              onChange={(v) => set({ [t.key]: v })}
              label={t.label}
              accent="emerald"
              className={canModerate ? "" : "pointer-events-none opacity-50"}
            />
          ))}
        </div>

        <p className="text-[11px] text-slate-400">
          Video quality, the stage layout and the broadcast itself are the host's — those
          controls aren't shown here because the server would refuse them.
        </p>
      </div>
    </Panel>
  );
}
