import { useMemo, useState } from "react";
import toast from "react-hot-toast";
import { FiFlag, FiPlus, FiTrash2 } from "react-icons/fi";
import { Panel, Button, StatCard } from "../../components/admin";
import { Switch } from "../../ui/forms";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import FeatureFlagModal from "./FeatureFlagModal";

function useFlagsData() {
  return useApi(() => api.get("/admin/feature-flags").then((r) => r.data));
}

// Platform feature toggles — real GET/POST/PATCH/DELETE against /admin/feature-flags.
// Reading a flag elsewhere in the app isn't wired up yet; this is the admin-managed
// source of truth other modules can check against later.
export default function FeatureFlags() {
  const { data: flags, loading, error, reload } = useFlagsData();
  const [modalOpen, setModalOpen] = useState(false);

  const rows = flags || [];
  const kpis = useMemo(
    () => ({ total: rows.length, enabled: rows.filter((f) => f.enabled).length }),
    [rows]
  );

  const toggle = async (flag) => {
    try {
      await api.patch(`/admin/feature-flags/${flag.id}`, { enabled: !flag.enabled });
      toast.success(`${flag.name} ${flag.enabled ? "disabled" : "enabled"}`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (flag) => {
    if (!window.confirm(`Delete flag "${flag.key}"? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/feature-flags/${flag.id}`);
      toast.success(`${flag.name} deleted`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  if (error) {
    return (
      <div className="mx-auto max-w-[1000px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load feature flags. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1000px] space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Feature Flags</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Platform-wide toggles, managed here</p>
        </div>
        <Button leftIcon={FiPlus} onClick={() => setModalOpen(true)}>New Flag</Button>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <StatCard label="Total Flags" value={kpis.total} loading={loading} />
        <StatCard label="Enabled" value={kpis.enabled} loading={loading} />
      </div>

      <Panel flush>
        {!loading && rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiFlag className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No feature flags yet</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Create one to start controlling a rollout.</p>
          </div>
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {loading &&
              Array.from({ length: 4 }).map((_, i) => (
                <li key={i} className="flex items-center justify-between gap-3 px-5 py-4">
                  <div className="zk-skeleton h-4 w-40 rounded bg-slate-200 dark:bg-slate-800" />
                  <div className="zk-skeleton h-5 w-9 rounded-full bg-slate-200 dark:bg-slate-800" />
                </li>
              ))}
            {!loading &&
              rows.map((f) => (
                <li key={f.id} className="group flex items-center justify-between gap-3 px-5 py-4">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-slate-800 dark:text-slate-100">{f.name}</span>
                      <code className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px] text-slate-500 dark:bg-slate-800 dark:text-slate-400">{f.key}</code>
                    </div>
                    {f.description && <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">{f.description}</p>}
                  </div>
                  <div className="flex shrink-0 items-center gap-3">
                    <Switch checked={f.enabled} onChange={() => toggle(f)} accent="violet" />
                    <Button
                      variant="ghost"
                      size="sm"
                      iconOnly
                      title={`Delete ${f.name}`}
                      leftIcon={FiTrash2}
                      className="opacity-0 transition-opacity group-hover:opacity-100 hover:text-rose-600 dark:hover:text-rose-400"
                      onClick={() => remove(f)}
                    />
                  </div>
                </li>
              ))}
          </ul>
        )}
      </Panel>

      <FeatureFlagModal open={modalOpen} onClose={() => setModalOpen(false)} onSaved={reload} />
    </div>
  );
}
