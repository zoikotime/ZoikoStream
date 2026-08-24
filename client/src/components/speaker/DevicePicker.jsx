// client/src/components/speaker/DevicePicker.jsx
// Camera/mic device selection for the contributor preflight step. `selectCamera`/
// `selectMic` already exist on hooks/useMediaPreview.js — they've had no UI consumer
// anywhere in the app until now. Device labels are only populated by the browser AFTER
// permission is granted (see useMediaPreview's own comment on this), so this renders once
// `devices.cameras`/`devices.mics` are non-empty, not before.
import { FiVideo, FiMic } from "react-icons/fi";
import { Select, Label } from "../../ui/forms";
import { cx } from "../../ui/tokens";

function DeviceSelect({ icon: Icon, label, options, value, onChange }) {
  return (
    <div>
      <Label>
        <span className="inline-flex items-center gap-1.5">
          <Icon aria-hidden="true" /> {label}
        </span>
      </Label>
      <Select variant="console" value={value || ""} onChange={(e) => onChange(e.target.value)}>
        {options.map((d, i) => (
          <option key={d.deviceId || i} value={d.deviceId}>
            {d.label || `${label} ${i + 1}`}
          </option>
        ))}
      </Select>
    </div>
  );
}

export default function DevicePicker({ devices, picked, selectCamera, selectMic, className = "" }) {
  if (!devices.cameras.length && !devices.mics.length) return null;
  return (
    <div className={cx("grid grid-cols-1 gap-3 sm:grid-cols-2", className)}>
      {devices.cameras.length > 1 && (
        <DeviceSelect icon={FiVideo} label="Camera" options={devices.cameras} value={picked.camera} onChange={selectCamera} />
      )}
      {devices.mics.length > 1 && (
        <DeviceSelect icon={FiMic} label="Microphone" options={devices.mics} value={picked.mic} onChange={selectMic} />
      )}
    </div>
  );
}
