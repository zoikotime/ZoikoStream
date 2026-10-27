import { useEffect, useRef, useState } from "react";
import { FiSave } from "react-icons/fi";
import { Panel, Button } from "../../components/admin";
import { Input, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import Skeleton from "../../ui/Skeleton";
import ElevationNote from "../../components/admin/ElevationNote";
import useElevation from "../../components/admin/useElevation";
import RetentionPolicyPanel from "./RetentionPolicyPanel";

const DEFAULTS = {
  brand: { name: "ZoikoStream", primary_color: "#8b5cf6", support_email: "" },
  // Null means there is no platform ceiling; non-entitled organizations still have zero
  // storage allowance, and only an entitled plan grants storage.
  storage_limits: { max_gb: null },
  streaming_limits: { default_hours: null, default_concurrent_streams: null, max_bitrate_kbps: null },
  global: { signups_enabled: true, maintenance_mode: false },
};

// audience_envelope is deliberately absent from streaming_limits' defaults above (see
// services/platform_settings.py::audience_capacity_envelope): unset is a real, intentional
// state — "no approved band, every stated audience needs an explicit capacity approval" —
// not a value to seed. The form below has to represent "unset" as an empty field, not as
// some numeric default, or saving the page with this field untouched would silently
// configure a band that Operations never approved.
const parseEnvelope = (value) => (value === "" || value == null ? null : Number(value));

function hexToHsv(value) {
  const match = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(value || "");
  const fallback = { hue: 258.3, saturation: 0.626, brightness: 0.965 };
  if (!match) return fallback;

  const hex = match[1].length === 3
    ? [...match[1]].map((digit) => digit + digit).join("")
    : match[1];
  const [red, green, blue] = [0, 2, 4].map((index) => parseInt(hex.slice(index, index + 2), 16) / 255);
  const max = Math.max(red, green, blue);
  const min = Math.min(red, green, blue);
  const delta = max - min;
  let hue = 0;
  if (delta) {
    if (max === red) hue = 60 * (((green - blue) / delta) % 6);
    else if (max === green) hue = 60 * ((blue - red) / delta + 2);
    else hue = 60 * ((red - green) / delta + 4);
  }
  return {
    hue: (hue + 360) % 360,
    saturation: max ? delta / max : 0,
    brightness: max,
  };
}

function hsvToHex(hue, saturation, brightness) {
  const chroma = brightness * saturation;
  const segment = hue / 60;
  const secondary = chroma * (1 - Math.abs((segment % 2) - 1));
  const rgb = segment < 1 ? [chroma, secondary, 0]
    : segment < 2 ? [secondary, chroma, 0]
      : segment < 3 ? [0, chroma, secondary]
        : segment < 4 ? [0, secondary, chroma]
          : segment < 5 ? [secondary, 0, chroma]
            : [chroma, 0, secondary];
  const offset = brightness - chroma;
  return `#${rgb.map((channel) => Math.round((channel + offset) * 255)
    .toString(16).padStart(2, "0")).join("")}`;
}

function PrimaryColorField({ value, onChange }) {
  const containerRef = useRef(null);
  const triggerRef = useRef(null);
  const hueRef = useRef(null);
  const [open, setOpen] = useState(false);
  const { hue, saturation, brightness } = hexToHsv(value);
  const color = /^#?(?:[0-9a-f]{3}|[0-9a-f]{6})$/i.test(value || "")
    ? (value.startsWith("#") ? value : `#${value}`)
    : "#8b5cf6";

  useEffect(() => {
    if (!open) return undefined;
    const closeOutside = (event) => {
      if (!containerRef.current?.contains(event.target)) setOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    hueRef.current?.focus();
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [open]);

  const updateSaturationBrightness = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const nextSaturation = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const nextBrightness = 1 - Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    onChange(hsvToHex(hue, nextSaturation, nextBrightness));
  };

  const changeSaturationBrightnessByKey = (event) => {
    const step = event.shiftKey ? 0.1 : 0.02;
    let nextSaturation = saturation;
    let nextBrightness = brightness;
    if (event.key === "ArrowLeft") nextSaturation -= step;
    else if (event.key === "ArrowRight") nextSaturation += step;
    else if (event.key === "ArrowDown") nextBrightness -= step;
    else if (event.key === "ArrowUp") nextBrightness += step;
    else return;
    event.preventDefault();
    onChange(hsvToHex(hue, Math.max(0, Math.min(1, nextSaturation)),
      Math.max(0, Math.min(1, nextBrightness))));
  };

  return (
    <div ref={containerRef} className="min-w-0">
      <Label>Primary color</Label>
      <div className="flex items-center gap-2">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setOpen((current) => !current)}
          aria-label="Choose primary color"
          aria-expanded={open}
          aria-controls={open ? "primary-color-picker" : undefined}
          className="h-9 w-11 shrink-0 rounded-lg border border-slate-200 shadow-inner outline-none transition focus-visible:ring-2 focus-visible:ring-violet-500 focus-visible:ring-offset-2 dark:border-slate-700 dark:focus-visible:ring-offset-slate-950"
          style={{ backgroundColor: color }}
        />
        <Input
          variant="console"
          value={value}
          aria-label="Primary color hex value"
          onChange={(event) => onChange(event.target.value)}
        />
      </div>
      {open && (
        <div
          id="primary-color-picker"
          role="group"
          aria-label="Primary color picker"
          className="mt-3 w-full max-w-[280px] rounded-xl border border-slate-200 bg-white p-3 shadow-lg dark:border-slate-700 dark:bg-slate-900"
        >
          <div
            role="slider"
            tabIndex={0}
            aria-label="Color saturation and brightness"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(saturation * 100)}
            aria-valuetext={`${Math.round(saturation * 100)}% saturation, ${Math.round(brightness * 100)}% brightness`}
            onPointerDown={(event) => {
              event.currentTarget.setPointerCapture(event.pointerId);
              updateSaturationBrightness(event);
            }}
            onPointerMove={(event) => {
              if (event.buttons === 1) updateSaturationBrightness(event);
            }}
            onKeyDown={changeSaturationBrightnessByKey}
            className="relative h-32 w-full cursor-crosshair touch-none rounded-md outline-none focus-visible:ring-2 focus-visible:ring-violet-500"
            style={{
              backgroundColor: `hsl(${hue} 100% 50%)`,
              backgroundImage: "linear-gradient(to top, #000, transparent), linear-gradient(to right, #fff, transparent)",
            }}
          >
            <span
              aria-hidden="true"
              className="pointer-events-none absolute h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white shadow-[0_0_0_1px_#111]"
              style={{ left: `${saturation * 100}%`, top: `${(1 - brightness) * 100}%` }}
            />
          </div>
          <label className="mt-3 block text-xs font-medium text-slate-600 dark:text-slate-300" htmlFor="primary-color-hue">
            Hue
          </label>
          <input
            ref={hueRef}
            id="primary-color-hue"
            type="range"
            min="0"
            max="359"
            value={Math.round(hue)}
            onChange={(event) => onChange(hsvToHex(Number(event.target.value), saturation, brightness))}
            aria-label="Primary color hue"
            className="mt-1 h-4 w-full cursor-pointer accent-violet-600"
          />
          <div className="mt-3 flex items-center justify-between gap-3">
            <span className="font-mono text-xs uppercase text-slate-600 dark:text-slate-300">{color}</span>
            <Button variant="secondary" size="sm" onClick={() => {
              setOpen(false);
              triggerRef.current?.focus();
            }}>
              Done
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function useSettingsData() {
  return useApi(() => api.get("/admin/settings").then((r) => r.data));
}

// Platform-wide config, keyed exactly like the backend's PlatformSetting rows
// (brand / storage_limits / streaming_limits / global). GET/PATCH /admin/settings.
export default function Settings() {
  const { data, loading, error, reload } = useSettingsData();

  if (error) {
    return (
      <div className="mx-auto max-w-[1000px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load platform settings. Try refreshing the page.
      </div>
    );
  }

  // `!data` is part of the guard, not just `loading`: SettingsForm below reads
  // `settings.brand` at mount, so "loaded but empty" has to stay on this side of the fence.
  // The effect this replaced was guarded by `if (!data) return;` for the same reason.
  if (loading || !data) {
    return (
      <div className="mx-auto max-w-[1000px] space-y-6">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="block" className="h-40" />
        <Skeleton variant="block" className="h-40" />
        <Skeleton variant="block" className="h-24" />
      </div>
    );
  }

  // The form lives in its own component so it can be MOUNTED only once `data` exists, which
  // is what lets its initial state be seeded straight from the server response instead of by
  // an effect. The behaviour is identical to the effect it replaces because useApi's reload()
  // sets loading=true: the guards above unmount this subtree during every refetch and remount
  // it against the fresh response, so a save still redisplays the canonical stored values.
  return <SettingsForm settings={data} onSaved={reload} />;
}

function SettingsForm({ settings, onSaved }) {
  const [form, setForm] = useState(() => ({
    brand: { ...DEFAULTS.brand, ...(settings.brand?.value || {}) },
    storage_limits: {
      max_gb: settings.storage_limits?.value?.max_gb ?? DEFAULTS.storage_limits.max_gb,
    },
    streaming_limits: { ...DEFAULTS.streaming_limits, ...(settings.streaming_limits?.value || {}) },
    global: { ...DEFAULTS.global, ...(settings.global?.value || {}) },
  }));
  const [saving, setSaving] = useState(false);
  // PATCH /admin/settings needs a platform elevation (security.require_elevation). The form
  // used to offer Save regardless, so an operator edited, saved, and only then learned they
  // had to elevate - from a red error, with no way to do it here. It now says so up front and
  // holds Save while the console KNOWS the server would refuse; when the elevation state is
  // unknown the server decides. Elevating does not remount this form, so edits survive it.
  const elevation = useElevation("platform");
  const refused = ["none", "expired", "wrong_scope"].includes(elevation.status);

  const setField = (group, key, value) =>
    setForm((f) => ({ ...f, [group]: { ...f[group], [key]: value } }));

  const save = async () => {
    setSaving(true);
    try {
      await api.patch("/admin/settings", {
        values: {
          brand: form.brand,
          // parseEnvelope, not Number(): Number("") is 0, so clearing the ceiling to mean
          // "no ceiling" would have saved a limit of ZERO.
          storage_limits: {
            max_gb: parseEnvelope(form.storage_limits.max_gb),
          },
          streaming_limits: {
            // Everything already stored first: the blob is REPLACED on save, so a key this
            // form does not edit (require_media_plane) must ride along or it is silently lost.
            ...(settings.streaming_limits?.value || {}),
            default_hours: parseEnvelope(form.streaming_limits.default_hours),
            default_concurrent_streams: parseEnvelope(form.streaming_limits.default_concurrent_streams),
            max_bitrate_kbps: parseEnvelope(form.streaming_limits.max_bitrate_kbps),
            // Capacity protection (services/admission.py): blank = no ceiling, the default.
            viewer_admission_ceiling: parseEnvelope(form.streaming_limits.viewer_admission_ceiling),
            // Explicitly included (even as null) on every save — streaming_limits is stored
            // and replaced as one JSON blob (routers/admin.py update_settings), so omitting
            // this key here would silently drop it the next time anything else in the panel
            // is saved.
            audience_envelope: parseEnvelope(form.streaming_limits.audience_envelope),
          },
          global: form.global,
        },
      });
      notify.success("Platform settings saved");
      onSaved();
    } catch (e) {
      notify.error(errMsg(e));
      // Refused anyway - the grant ended in another tab since the console last looked. Re-read
      // the console state so the note and the held Save reflect that, not the stale grant.
      if (e?.response?.status === 403) elevation.reload?.();
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto max-w-[1000px] space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Platform Configuration</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Platform-wide brand, limits, access and retention governance</p>
        </div>
        <Button leftIcon={FiSave} onClick={save} loading={saving} disabled={refused}>Save changes</Button>
      </div>

      <ElevationNote
        elevation={elevation}
        need="Saving these settings needs platform elevation. Your edits are kept while you elevate."
        reason="Change platform configuration"
      />

      <Panel eyebrow="Brand" title="Platform Identity" description="Shown across emails and the public marketing site.">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label>Platform name</Label>
            <Input variant="console" value={form.brand.name} onChange={(e) => setField("brand", "name", e.target.value)} />
          </div>
          <div>
            <Label>Support email</Label>
            <Input variant="console" type="email" value={form.brand.support_email} onChange={(e) => setField("brand", "support_email", e.target.value)} />
          </div>
          <PrimaryColorField
            value={form.brand.primary_color}
            onChange={(color) => setField("brand", "primary_color", color)}
          />
        </div>
      </Panel>

      <Panel eyebrow="Limits" title="Storage & Streaming Limits" description="Storage requires an entitled plan; the platform ceiling only caps an applicable plan allowance. Streaming defaults apply independently.">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label>Max storage (GB)</Label>
            <Input variant="console" type="number" min={0} value={form.storage_limits.max_gb ?? ""} placeholder="No ceiling" onChange={(e) => setField("storage_limits", "max_gb", e.target.value)} />
          </div>
          <div>
            <Label>Default streaming hours</Label>
            <Input variant="console" type="number" min={0} value={form.streaming_limits.default_hours ?? ""} placeholder="Not set" onChange={(e) => setField("streaming_limits", "default_hours", e.target.value)} />
          </div>
          <div>
            <Label>Default concurrent streams</Label>
            <Input variant="console" type="number" min={0} step={1} value={form.streaming_limits.default_concurrent_streams ?? ""} placeholder="Not set" onChange={(e) => setField("streaming_limits", "default_concurrent_streams", e.target.value)} />
          </div>
          <div>
            <Label>Max bitrate (kbps)</Label>
            <Input variant="console" type="number" min={0} value={form.streaming_limits.max_bitrate_kbps ?? ""} placeholder="No ceiling" onChange={(e) => setField("streaming_limits", "max_bitrate_kbps", e.target.value)} />
          </div>
          <div className="sm:col-span-2">
            <Label>Approved audience capacity envelope (peak concurrent viewers)</Label>
            <Input
              variant="console"
              type="number"
              min={0}
              placeholder="Not configured — every stated audience requires manual approval"
              value={form.streaming_limits.audience_envelope ?? ""}
              onChange={(e) => setField("streaming_limits", "audience_envelope", e.target.value)}
            />
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              An event whose organizer states an expected audience at or under this number goes
              live without further approval. Left blank (the default), EVERY event that states an
              expected audience — any number — is blocked from going live until Operations
              approves its capacity directly on the event. This is a fail-closed rule and cannot
              be bypassed here; it only decides whether that manual step is required.
            </p>
          </div>
          <div className="sm:col-span-2">
            <Label>Viewer admission ceiling (concurrent viewers per live event)</Label>
            <Input
              variant="console"
              type="number"
              min={1}
              placeholder="No ceiling"
              value={form.streaming_limits.viewer_admission_ceiling ?? ""}
              onChange={(e) => setField("streaming_limits", "viewer_admission_ceiling", e.target.value)}
            />
            <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
              Infrastructure protection only. When a live event reaches this many viewers, NEW
              viewers wait on a "very busy" screen and are let in automatically as room frees
              up. Nobody already watching is ever disconnected, and a viewer who drops and
              comes back keeps their place. Event staff are never held. Leave blank for no
              ceiling.
            </p>
          </div>
        </div>
        <p className="mt-4 text-xs leading-relaxed text-slate-500 dark:text-slate-400">
          Defaults apply only to organizations that have never had a subscription. Finite
          subscription limits remain in force after a lapse; a lapsed subscription with no
          finite limit does not grant access. Blank defaults do not grant access.
          Storage blocks new recording starts at the effective quota, but a LiveKit recording
          already in progress may finish above it. Only one recording per organization can
          consume finite storage headroom at a time. Streaming hours are cumulative across the
          organization&apos;s broadcasts, counting concurrent broadcasts independently; an
          active broadcast may finish, while new starts are blocked at the hours or concurrent
          stream limit. Organizations without a plan need a default concurrent-stream limit
          configured before they can start a broadcast.
        </p>
      </Panel>

      <Panel eyebrow="Access" title="Global Controls">
        <div className="space-y-3">
          <Switch
            checked={form.global.signups_enabled}
            onChange={(v) => setField("global", "signups_enabled", v)}
            accent="violet"
            label={
              <span>
                <span className="block font-medium text-slate-800 dark:text-slate-100">Signups enabled</span>
                <span className="block text-xs text-slate-400">Allow new organizations to sign up</span>
              </span>
            }
          />
          <Switch
            checked={form.global.maintenance_mode}
            onChange={(v) => setField("global", "maintenance_mode", v)}
            accent="violet"
            label={
              <span>
                <span className="block font-medium text-slate-800 dark:text-slate-100">Maintenance mode</span>
                <span className="block text-xs text-slate-400">
                  Blocks the organization/host console for non-admins. Live broadcasts, viewer
                  pages, and this console stay reachable.
                </span>
              </span>
            }
          />
        </div>
      </Panel>

      {/* Not part of "Save changes": the retention policy is changed only through its own
          propose/approve workflow, which the generic settings PATCH refuses to touch. */}
      <RetentionPolicyPanel />
    </div>
  );
}
