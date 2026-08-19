// client/src/components/host/FeatureModal.jsx
// One modal, three faces — Invite to stage / Broadcast settings / Recording log — driven by
// the `modal` key from the control deck. Reuses the shared Modal + form system.
//
// Every switch here maps to a setting the SERVER enforces (services/broadcast.SETTING_SPECS
// and moderation.chat_gate). Controls the browser genuinely can't apply are shown disabled
// with the reason, rather than as a toggle that silently does nothing.
import { FiAlertTriangle, FiDownload, FiVideo, FiCheck, FiClock } from "react-icons/fi";
import Modal from "../../ui/Modal";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import EmptyState from "../organization/OrganizationEmptyState";
import { Input, Select, Switch, Label } from "../../ui/forms";
import { downloadCsv } from "../../utils/export";
import { hhmm } from "../../data/host";
import {
  RESOLUTION_OPTIONS, FRAMERATE_OPTIONS, BITRATE_GUIDE, PROCESSING_TOGGLES,
  BACKGROUND_OPTIONS, CHAT_CONTROLS, SLOW_MODE_OPTIONS, STAGE_CONTROLS, RECORDING_TONE,
} from "../../data/host";
import InviteViewersModal from "../../pages/organization/InviteViewersModal";
const section = "text-[11px] font-semibold uppercase tracking-wide text-slate-400";

// One labelled switch bound to a server-enforced setting. Module-level so it isn't
// re-created on every render of the settings body.
function ToggleRow({ toggle, settings, disabled, onChange }) {
  return (
    <Switch
      checked={settings[toggle.key] !== false}
      onChange={(v) => onChange({ [toggle.key]: v })}
      label={toggle.label}
      accent="emerald"
      className={disabled ? "pointer-events-none opacity-50" : ""}
    />
  );
}

// ── invite to stage ───────────────────────────────────────────────────────────
// Promotes someone already connected. Emailing an outside speaker is the existing
// org invitation flow (/organization/users) — duplicating it here would be a second
// ── broadcast settings ────────────────────────────────────────────────────────

function SettingsBody({ settings, media, canHost, send }) {
  const s = settings || {};
  const set = (patch) => send("broadcast.settings", { settings: patch });
  const disabled = !canHost;
  const actual = media?.actual;
  return (
    <div className="space-y-5">
      {disabled && (
        <p className="flex items-center gap-2 rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
          <FiAlertTriangle aria-hidden="true" /> Only the event host can change these. You're viewing them read-only.
        </p>
      )}

      {/* Video */}
      <div className="space-y-2">
        <p className={section}>Video</p>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label variant="console">Resolution (target)</Label>
            <Select variant="console" value={s.resolution || "1080p"} disabled={disabled}
                    onChange={(e) => set({ resolution: e.target.value })} aria-label="Resolution">
              {RESOLUTION_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
            </Select>
          </div>
          <div>
            <Label variant="console">Frame rate</Label>
            <Select variant="console" value={s.framerate || 30} disabled={disabled}
                    onChange={(e) => set({ framerate: Number(e.target.value) })} aria-label="Frame rate">
              {FRAMERATE_OPTIONS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
            </Select>
          </div>
        </div>

        {/* Requested vs granted — the difference is the point. */}
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {actual?.width
            ? <>Camera is actually delivering <span className="font-semibold text-slate-700 dark:text-slate-200">{actual.width}×{actual.height}{actual.frameRate ? ` @ ${actual.frameRate}fps` : ""}</span>{" "}
              {actual.height && s.resolution && actual.height < ({ "720p": 720, "1080p": 1080, "2k": 1440, "4k": 2160 }[s.resolution] || 0) && (
                <Badge tone="warning" size="sm">below target</Badge>
              )}</>
            : "Start the preview to see what your camera actually delivers."}
        </p>

        <div>
          <Label variant="console">Bitrate target (kbps)</Label>
          <Input
            variant="console"
            type="number"
            min={500}
            max={20000}
            step={100}
            disabled={disabled}
            value={s.bitrate_kbps ?? 4500}
            onChange={(e) => set({ bitrate_kbps: Number(e.target.value) })}
            aria-label="Bitrate target"
          />
          <p className="mt-1 text-xs text-slate-400">
            Recommended for {s.resolution || "1080p"}: {(BITRATE_GUIDE[s.resolution] || 4500).toLocaleString()} kbps.
            Applied by the media server when publishing.
          </p>
        </div>
      </div>

      {/* Audio + processing */}
      <div className="space-y-2">
        <p className={section}>Audio processing</p>
        {PROCESSING_TOGGLES.map((t) => (
          <div key={t.key}>
            <ToggleRow toggle={t} settings={s} disabled={disabled} onChange={set} />
            {!t.native && <p className="mt-0.5 pl-1 text-[11px] text-slate-400">{t.note}</p>}
          </div>
        ))}
        <div className="grid grid-cols-2 gap-2">
          <div>
            <Label variant="console">Mic gain</Label>
            <Input variant="console" type="range" min={0} max={200} disabled={disabled}
                   value={s.mic_gain ?? 100}
                   onChange={(e) => set({ mic_gain: Number(e.target.value) })} aria-label="Mic gain" />
          </div>
          <div>
            <Label variant="console">Monitor volume</Label>
            <Input variant="console" type="range" min={0} max={100} disabled={disabled}
                   value={s.speaker_volume ?? 100}
                   onChange={(e) => set({ speaker_volume: Number(e.target.value) })} aria-label="Monitor volume" />
          </div>
        </div>
        {actual && (
          <p className="text-[11px] text-slate-400">
            Browser reports: echo cancellation {String(actual.echoCancellation)}, noise suppression{" "}
            {String(actual.noiseSuppression)}, auto gain {String(actual.autoGainControl)}.
          </p>
        )}
      </div>

      {/* Background — honest about what isn't wired */}
      <div className="space-y-1">
        <p className={section}>Background</p>
        <Select variant="console" value={s.background || "none"} disabled
                aria-label="Background" onChange={() => {}}>
          {BACKGROUND_OPTIONS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
        </Select>
        <p className="flex items-center gap-1.5 text-[11px] text-amber-600 dark:text-amber-400">
          <FiAlertTriangle aria-hidden="true" />
          Blur and virtual backgrounds need a segmentation model that isn't installed, so this is
          disabled rather than pretending to apply.
        </p>
      </div>

      {/* Chat control — enforced server-side */}
      <div className="space-y-2">
        <p className={section}>Chat control</p>
        {CHAT_CONTROLS.map((t) => <ToggleRow key={t.key} toggle={t} settings={s} disabled={disabled} onChange={set} />)}
        <div>
          <Label variant="console">Slow mode</Label>
          <Select variant="console" value={s.slow_mode_seconds ?? 0} disabled={disabled}
                  onChange={(e) => set({ slow_mode_seconds: Number(e.target.value) })} aria-label="Slow mode">
            {SLOW_MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </Select>
        </div>
      </div>

      {/* Stage + audience */}
      <div className="space-y-2">
        <p className={section}>Stage &amp; audience</p>
        {STAGE_CONTROLS.map((t) => <ToggleRow key={t.key} toggle={t} settings={s} disabled={disabled} onChange={set} />)}
      </div>

      {/* Recording */}
      <div className="space-y-2">
        <p className={section}>Recording</p>
        <div>
          <Label variant="console">Recording quality</Label>
          <Select variant="console" value={s.recording_quality || "1080p"} disabled={disabled}
                  onChange={(e) => set({ recording_quality: e.target.value })} aria-label="Recording quality">
            {RESOLUTION_OPTIONS.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </Select>
        </div>
        <ToggleRow toggle={{ key: "auto_upload", label: "Auto-upload when the recording stops" }} settings={s} disabled={disabled} onChange={set} />
      </div>
    </div>
  );
}

// ── recording log ─────────────────────────────────────────────────────────────

function RecordingLog({ recordings }) {
  if (!recordings?.length) {
    return <EmptyState icon={FiVideo} title="No recordings yet" description="Each start-to-stop cycle appears here with its timings." className="py-10" />;
  }
  const secs = (r) => {
    if (!r.started_at) return "—";
    const end = r.stopped_at ? new Date(r.stopped_at) : new Date();
    const s = Math.max(0, Math.floor((end - new Date(r.started_at) - (r.paused_ms || 0)) / 1000));
    return `${Math.floor(s / 60)}m ${s % 60}s`;
  };
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <Button
          appearance="console"
          variant="secondary"
          size="sm"
          leftIcon={FiDownload}
          onClick={() => downloadCsv("recordings.csv", recordings, [
            ["Started", (r) => r.started_at || ""], ["Stopped", (r) => r.stopped_at || ""],
            ["Status", (r) => r.status], ["Role", (r) => r.role || ""], ["Quality", (r) => r.quality || ""],
            ["Duration", secs], ["Captured", (r) => (r.enforced ? "yes" : "no")],
            ["File", (r) => r.file_url || ""], ["Error", (r) => r.error || ""],
          ])}
        >
          Export log
        </Button>
      </div>
      {recordings.map((r) => (
        <div key={r.id} className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={RECORDING_TONE[r.status]} dot>{r.status}</Badge>
            {/* Under dual recording (services/broadcast.py._recording_start) two rows share
                one start-to-stop cycle — this is what tells them apart in the log. */}
            {r.role && <Badge tone={r.role === "primary" ? "brand" : "neutral"} size="sm">{r.role === "primary" ? "Primary" : "Backup"}</Badge>}
            {r.quality && <Badge tone="neutral" size="sm">{r.quality}</Badge>}
            {r.enforced
              ? <Badge tone="success" size="sm"><FiCheck aria-hidden="true" /> captured</Badge>
              : <Badge tone="warning" size="sm"><FiAlertTriangle aria-hidden="true" /> not captured</Badge>}
            <span className="ml-auto flex items-center gap-1 text-xs text-slate-400">
              <FiClock aria-hidden="true" /> {secs(r)}
            </span>
          </div>
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {hhmm(r.started_at)}{r.stopped_at ? ` → ${hhmm(r.stopped_at)}` : " → running"}
            {r.auto_upload ? " · auto-upload on" : ""}
          </p>
          {r.file_url && <p className="mt-1 break-all font-mono text-[11px] text-slate-400">{r.file_url}</p>}
          {r.error && <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{r.error}</p>}
        </div>
      ))}
    </div>
  );
}

const CONFIG = {
  invite: { title: "Invite viewers", size: "md" },
  settings: { title: "Broadcast settings", size: "lg" },
  recordings: { title: "Recording log", size: "lg" },
};

export default function FeatureModal({ modal, onClose, state, media, send }) {
  const cfg = modal && CONFIG[modal];

  // Host "Invite" should use the same viewer invitation system
  // already used by Organization Event Details.
  if (modal === "invite") {
    return (
      <InviteViewersModal
        open={true}
        onClose={onClose}
        eventId={state.event?.id}
        eventVisibility={state.event?.visibility || "public"}
      />
    );
  }

  return (
    <Modal
      open={!!cfg}
      onClose={onClose}
      title={cfg?.title}
      size={cfg?.size || "md"}
    >
      {modal === "settings" && (
        <SettingsBody
          settings={state.broadcast?.settings}
          media={media}
          canHost={state.canHost}
          send={send}
        />
      )}

      {modal === "recordings" && (
        <RecordingLog recordings={state.recordings || []} />
      )}
    </Modal>
  );
}