import { useEffect, useState } from "react";
import { FiSave } from "react-icons/fi";
import { Panel, Button } from "../../components/admin";
import { Input, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import Skeleton from "../../ui/Skeleton";

const DEFAULTS = {
  brand: { name: "ZoikoStream", primary_color: "#8b5cf6", support_email: "" },
  storage_limits: { default_gb: 50, max_gb: 5000 },
  streaming_limits: { default_hours: 20, max_bitrate_kbps: 8000 },
  global: { signups_enabled: true, maintenance_mode: false },
};

function useSettingsData() {
  return useApi(() => api.get("/admin/settings").then((r) => r.data));
}

// Platform-wide config, keyed exactly like the backend's PlatformSetting rows
// (brand / storage_limits / streaming_limits / global). GET/PATCH /admin/settings.
export default function Settings() {
  const { data, loading, error, reload } = useSettingsData();
  const [form, setForm] = useState(DEFAULTS);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!data) return;
    setForm({
      brand: { ...DEFAULTS.brand, ...(data.brand?.value || {}) },
      storage_limits: { ...DEFAULTS.storage_limits, ...(data.storage_limits?.value || {}) },
      streaming_limits: { ...DEFAULTS.streaming_limits, ...(data.streaming_limits?.value || {}) },
      global: { ...DEFAULTS.global, ...(data.global?.value || {}) },
    });
  }, [data]);

  const setField = (group, key, value) =>
    setForm((f) => ({ ...f, [group]: { ...f[group], [key]: value } }));

  const save = async () => {
    setSaving(true);
    try {
      await api.patch("/admin/settings", {
        values: {
          brand: form.brand,
          storage_limits: { default_gb: Number(form.storage_limits.default_gb), max_gb: Number(form.storage_limits.max_gb) },
          streaming_limits: { default_hours: Number(form.streaming_limits.default_hours), max_bitrate_kbps: Number(form.streaming_limits.max_bitrate_kbps) },
          global: form.global,
        },
      });
      notify.success("Platform settings saved");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  if (error) {
    return (
      <div className="mx-auto max-w-[1000px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load platform settings. Try refreshing the page.
      </div>
    );
  }

  if (loading) {
    return (
      <div className="mx-auto max-w-[1000px] space-y-6">
        <Skeleton variant="title" className="w-64" />
        <Skeleton variant="block" className="h-40" />
        <Skeleton variant="block" className="h-40" />
        <Skeleton variant="block" className="h-24" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1000px] space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Platform Settings</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Platform-wide brand, limits, and access config</p>
        </div>
        <Button leftIcon={FiSave} onClick={save} loading={saving}>Save changes</Button>
      </div>

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
          <div>
            <Label>Primary color</Label>
            <div className="flex items-center gap-2">
              <input
                type="color"
                value={form.brand.primary_color}
                onChange={(e) => setField("brand", "primary_color", e.target.value)}
                className="h-9 w-11 shrink-0 cursor-pointer rounded-lg border border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"
                aria-label="Primary color"
              />
              <Input variant="console" value={form.brand.primary_color} onChange={(e) => setField("brand", "primary_color", e.target.value)} />
            </div>
          </div>
        </div>
      </Panel>

      <Panel eyebrow="Limits" title="Storage & Streaming Limits" description="Max storage and max bitrate are enforced platform-wide as a hard ceiling on top of every organization's own plan. Default storage and default streaming hours are not yet applied anywhere — they don't do anything today.">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <Label>Default storage (GB)</Label>
            <Input variant="console" type="number" min={0} value={form.storage_limits.default_gb} onChange={(e) => setField("storage_limits", "default_gb", e.target.value)} />
          </div>
          <div>
            <Label>Max storage (GB)</Label>
            <Input variant="console" type="number" min={0} value={form.storage_limits.max_gb} onChange={(e) => setField("storage_limits", "max_gb", e.target.value)} />
          </div>
          <div>
            <Label>Default streaming hours</Label>
            <Input variant="console" type="number" min={0} value={form.streaming_limits.default_hours} onChange={(e) => setField("streaming_limits", "default_hours", e.target.value)} />
          </div>
          <div>
            <Label>Max bitrate (kbps)</Label>
            <Input variant="console" type="number" min={0} value={form.streaming_limits.max_bitrate_kbps} onChange={(e) => setField("streaming_limits", "max_bitrate_kbps", e.target.value)} />
          </div>
        </div>
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
    </div>
  );
}
