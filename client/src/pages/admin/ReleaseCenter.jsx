import { useState } from "react";
import toast from "react-hot-toast";
import { FiPackage, FiPlus, FiTrash2 } from "react-icons/fi";
import { Badge, Panel, Button, timeAgo } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import ReleaseModal from "./ReleaseModal";

const CHANNEL_TONE = { production: "success", staging: "warning", beta: "info" };

function useReleasesData() {
  return useApi(() => api.get("/admin/releases", { params: { page_size: 100 } }).then((r) => r.data.items));
}

// Admin-authored changelog — there's no CI/CD integration to source this from, so every
// entry here was manually published, real history rather than a fabricated deploy feed.
export default function ReleaseCenter() {
  const { data: releases, loading, error, reload } = useReleasesData();
  const [modalOpen, setModalOpen] = useState(false);

  const rows = releases || [];

  const remove = async (r) => {
    if (!window.confirm(`Delete release ${r.version}? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/releases/${r.id}`);
      toast.success(`${r.version} deleted`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  if (error) {
    return (
      <div className="mx-auto max-w-[1000px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load the release log. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1000px] space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Release Center</h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Platform changelog</p>
        </div>
        <Button leftIcon={FiPlus} onClick={() => setModalOpen(true)}>Publish Release</Button>
      </div>

      <Panel flush>
        {loading ? (
          <div className="space-y-4 px-5 py-5">
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="zk-skeleton h-16 rounded-lg bg-slate-200 dark:bg-slate-800" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <div className="px-5 py-16 text-center">
            <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-slate-800">
              <FiPackage className="text-lg" />
            </div>
            <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">No releases logged yet</p>
            <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Publish your first entry to start the changelog.</p>
          </div>
        ) : (
          <ol className="relative space-y-6 px-5 py-5 pl-8">
            <span className="absolute inset-y-6 left-[15px] w-px bg-slate-200 dark:bg-slate-800" aria-hidden />
            {rows.map((r) => (
              <li key={r.id} className="group relative">
                <span className="absolute -left-[26px] top-1.5 h-[9px] w-[9px] rounded-full border-2 border-white bg-violet-500 dark:border-slate-900" aria-hidden />
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-sm font-semibold text-slate-800 dark:text-slate-100">{r.version}</span>
                      <Badge tone={CHANNEL_TONE[r.channel] || "neutral"}>{r.channel}</Badge>
                    </div>
                    <p className="mt-0.5 font-medium text-slate-800 dark:text-slate-100">{r.title}</p>
                    {r.notes && <p className="mt-1 whitespace-pre-line text-sm text-slate-500 dark:text-slate-400">{r.notes}</p>}
                    <p className="mt-1.5 text-xs text-slate-400">{r.released_by || "system"} · {timeAgo(r.released_at)}</p>
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    title={`Delete ${r.version}`}
                    leftIcon={FiTrash2}
                    className="opacity-0 transition-opacity group-hover:opacity-100 hover:text-rose-600 dark:hover:text-rose-400"
                    onClick={() => remove(r)}
                  />
                </div>
              </li>
            ))}
          </ol>
        )}
      </Panel>

      {modalOpen && <ReleaseModal open onClose={() => setModalOpen(false)} onSaved={reload} />}
    </div>
  );
}
