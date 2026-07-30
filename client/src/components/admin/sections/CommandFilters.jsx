import { FiDownload, FiRefreshCw } from "react-icons/fi";
import { CONSOLE, cx, focusRing, type } from "../../../ui/tokens";
import { Select } from "../../../ui/forms";

// The console's window controls. Every value here is sent to /admin/command-center, so a
// change genuinely re-scopes the data rather than filtering an already-fetched blob.
const RANGES = [
  ["live", "Live"],
  ["1h", "1h"],
  ["24h", "24h"],
  ["7d", "7d"],
  ["custom", "Custom"],
];

// Local datetime-local value (the control has no timezone, so it reads as operator-local
// and is sent as an ISO instant).
const toLocalInput = (d) => {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

const SCOPES = [
  ["core_live", "Core + Live Events"],
  ["core", "Core platform"],
  ["live", "Live events"],
];

export default function CommandFilters({ value, onChange, regions = [], onRefresh, onExport, refreshing }) {
  const set = (patch) => onChange({ ...value, ...patch });

  // Switching to Custom seeds a sensible window (last 24h) so the first click already
  // shows data instead of an empty required-field state.
  const pickCustom = () => {
    if (value.range === "custom") return;
    const now = new Date();
    set({
      range: "custom",
      from: value.from || toLocalInput(new Date(now.getTime() - 24 * 3600 * 1000)),
      to: value.to || toLocalInput(now),
    });
  };

  return (
    <div className="flex flex-wrap items-center gap-2 sm:gap-2.5">
      {/* Range — segmented control */}
      <div
        role="group"
        aria-label="Time range"
        className={cx("flex items-center gap-0.5 rounded-lg p-0.5", CONSOLE.segment)}
      >
        {RANGES.map(([key, label]) => (
          <button
            key={key}
            onClick={() => (key === "custom" ? pickCustom() : set({ range: key }))}
            aria-pressed={value.range === key}
            className={cx(
              "rounded-md px-2.5 py-1.5 text-[12px] font-semibold transition",
              focusRing,
              value.range === key ? CONSOLE.segmentOn : CONSOLE.segmentOff
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Custom window — only present when Custom is the active range. */}
      {value.range === "custom" && (
        <div className="flex flex-wrap items-center gap-1.5">
          <input
            type="datetime-local"
            aria-label="Window start"
            value={value.from || ""}
            max={value.to || undefined}
            onChange={(e) => set({ from: e.target.value })}
            className={cx("rounded-lg border px-2 py-1.5 text-[12px]", CONSOLE.control)}
          />
          <span className={cx("text-[12px]", CONSOLE.faint)}>→</span>
          <input
            type="datetime-local"
            aria-label="Window end"
            value={value.to || ""}
            min={value.from || undefined}
            onChange={(e) => set({ to: e.target.value })}
            className={cx("rounded-lg border px-2 py-1.5 text-[12px]", CONSOLE.control)}
          />
        </div>
      )}

      <Select
        variant="console"
        aria-label="Region"
        value={value.region || ""}
        onChange={(e) => set({ region: e.target.value || null })}
        className="w-[9.5rem]"
      >
        <option value="">All regions</option>
        {regions.map((r) => (
          <option key={r.code} value={r.code}>
            {r.label}
          </option>
        ))}
      </Select>

      <Select
        variant="console"
        aria-label="Scope"
        value={value.scope}
        onChange={(e) => set({ scope: e.target.value })}
        className="w-[11.5rem]"
      >
        {SCOPES.map(([key, label]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </Select>

      <label
        className={cx(
          "flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-[12px] font-medium transition",
          CONSOLE.control
        )}
      >
        <input
          type="checkbox"
          checked={value.include_test}
          onChange={(e) => set({ include_test: e.target.checked })}
          className="h-3.5 w-3.5 rounded accent-violet-600"
        />
        Include test mode
      </label>

      <button
        onClick={onRefresh}
        aria-label="Refresh"
        className={cx("grid h-9 w-9 shrink-0 place-items-center rounded-lg border transition", CONSOLE.control, focusRing)}
      >
        <FiRefreshCw className={cx("text-[15px]", refreshing && "zk-spin")} />
      </button>

      <button
        onClick={onExport}
        className={cx(
          "inline-flex shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-[12px] font-semibold transition",
          CONSOLE.control,
          focusRing
        )}
      >
        <FiDownload className="text-[14px]" />
        <span className={type.label}>Export snapshot</span>
      </button>
    </div>
  );
}
