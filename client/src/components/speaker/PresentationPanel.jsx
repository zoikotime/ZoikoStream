// client/src/components/speaker/PresentationPanel.jsx
// Upload, review and start a presentation.
//
// Files travel over REST (routers/speaker.py) because bytes cannot go through a socket frame;
// everything else — which deck is live, which slide, approval decisions — is a socket action, so
// every console and the recording composite converge on the same state.
//
// Honest about one limit: PowerPoint is ACCEPTED (speakers have .pptx, not PDFs) but cannot be
// rendered as slides, because that needs a converter this stack does not run. The row says so and
// offers the file for download instead of failing at the worst possible moment.
import { useCallback, useRef, useState } from "react";
import {
  FiUpload, FiPlay, FiTrash2, FiCheck, FiX, FiFileText, FiImage, FiFile,
  FiAlertTriangle, FiEye, FiSquare,
} from "react-icons/fi";
import api from "../../api";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import EmptyState from "../organization/OrganizationEmptyState";
import Panel, { ActionButton } from "../moderation/Panel";
import { notify } from "../../ui/Toast";
import {
  ACCEPTED_UPLOAD, MAX_UPLOAD_MB, ASSET_STATUS_TONE, ASSET_STATUS_LABEL, fmtBytes,
} from "../../data/speaker";

const KIND_ICON = { slides: FiFileText, image: FiImage, file: FiFile };

export default function PresentationPanel({
  eventId, assets = [], presentation, myIdentity, canModerate, canPresent,
  onUploaded, onPreview, send, className,
}) {
  const [busy, setBusy] = useState(false);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef(null);

  const liveId = presentation?.asset_id || null;
  const iAmPresenting = liveId && presentation?.presenter_identity === myIdentity;

  const upload = useCallback(async (file) => {
    if (!file) return;
    // Checked here as well as server-side so a 25 MB mistake fails instantly instead of after
    // the whole body has been sent up a hotel uplink.
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      notify.error(`That file is ${fmtBytes(file.size)} — the limit is ${MAX_UPLOAD_MB} MB.`);
      return;
    }
    const body = new FormData();
    body.append("file", file);
    setBusy(true);
    try {
      const { data } = await api.post(`/speaker/events/${eventId}/assets`, body);
      notify.success(
        data.kind === "file"
          ? `${data.filename} uploaded — export it as a PDF to present it as slides.`
          : `${data.filename} uploaded, waiting for approval.`
      );
      onUploaded?.(data);
    } catch (e) {
      notify.error(e?.response?.data?.detail || "Couldn't upload that file.");
    } finally {
      setBusy(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }, [eventId, onUploaded]);

  const remove = async (asset) => {
    try {
      await api.delete(`/speaker/events/${eventId}/assets/${asset.id}`);
      notify.info(`${asset.filename} deleted.`);
      onUploaded?.(null);
    } catch (e) {
      notify.error(e?.response?.data?.detail || "Couldn't delete that file.");
    }
  };

  return (
    <Panel
      title="Presentation"
      count={assets.length}
      badge={liveId && <Badge tone="success" size="sm" dot>On screen</Badge>}
      className={className}
      action={canPresent && (
        <>
          <input
            ref={inputRef}
            type="file"
            accept={ACCEPTED_UPLOAD}
            onChange={(e) => upload(e.target.files?.[0])}
            className="hidden"
            id="speaker-asset-input"
          />
          <label
            htmlFor="speaker-asset-input"
            className={cx(
              "inline-flex cursor-pointer items-center gap-1 rounded-lg border border-slate-200 px-2 py-1 text-xs font-medium transition dark:border-slate-700",
              busy ? "opacity-50" : "text-slate-600 hover:bg-slate-50 dark:text-slate-300 dark:hover:bg-slate-800"
            )}
          >
            <FiUpload aria-hidden="true" /> {busy ? "Uploading…" : "Upload"}
          </label>
        </>
      )}
    >
      {canPresent && (
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={(e) => { e.preventDefault(); setDragging(false); upload(e.dataTransfer.files?.[0]); }}
          className={cx(
            "mb-3 rounded-xl border border-dashed px-3 py-4 text-center text-xs transition",
            dragging
              ? "border-emerald-400 bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300"
              : "border-slate-200 text-slate-400 dark:border-slate-700"
          )}
        >
          Drop a PDF or image here · up to {MAX_UPLOAD_MB} MB
          <br />
          <span className="text-[11px]">PowerPoint is stored but can't be shown as slides — export a PDF.</span>
        </div>
      )}

      <div className="space-y-2">
        {assets.map((a) => {
          const Icon = KIND_ICON[a.kind] || FiFile;
          const isLive = a.id === liveId;
          const mine = a.uploaded_by === myIdentity;
          const presentable = a.kind !== "file" && (a.status === "approved" || canModerate);
          return (
            <div
              key={a.id}
              className={cx(
                "rounded-xl border p-2.5 transition",
                isLive
                  ? "border-emerald-300 bg-emerald-50/60 dark:border-emerald-500/40 dark:bg-emerald-500/10"
                  : "border-slate-100 dark:border-slate-800"
              )}
            >
              <div className="flex items-start gap-2">
                <Icon className="mt-0.5 shrink-0 text-slate-400" aria-hidden="true" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                    {a.filename}
                  </p>
                  <p className="truncate text-xs text-slate-400">
                    {fmtBytes(a.size_bytes)}
                    {a.pages ? ` · ${a.pages} page${a.pages === 1 ? "" : "s"}` : ""}
                    {!mine && a.uploader_name ? ` · ${a.uploader_name}` : ""}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1">
                    <Badge tone={ASSET_STATUS_TONE[a.status]} size="sm">
                      {ASSET_STATUS_LABEL[a.status] || a.status}
                    </Badge>
                    {a.kind === "file" && (
                      <Badge tone="neutral" size="sm">
                        <FiAlertTriangle aria-hidden="true" /> Not presentable
                      </Badge>
                    )}
                    {isLive && <Badge tone="success" size="sm" dot>Live</Badge>}
                  </div>
                  {a.review_note && (
                    <p className="mt-1 rounded-lg bg-amber-50 px-2 py-1 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
                      {a.review_note}
                    </p>
                  )}
                </div>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-1">
                <ActionButton icon={FiEye} label="Preview" onClick={() => onPreview?.(a)} />

                {canPresent && presentable && !isLive && (
                  <ActionButton
                    icon={FiPlay}
                    label="Present"
                    tone="emerald"
                    onClick={() => send("presentation.start", { asset_id: a.id })}
                  />
                )}
                {isLive && (iAmPresenting || canModerate) && (
                  <ActionButton
                    icon={FiSquare}
                    label="Stop"
                    tone="rose"
                    onClick={() => send("presentation.stop", {})}
                  />
                )}

                {/* Approval is a MODERATOR decision — the whole point of the step is that
                    somebody other than the uploader vets what goes on the main screen. */}
                {canModerate && a.status !== "approved" && (
                  <ActionButton
                    icon={FiCheck}
                    label="Approve"
                    tone="emerald"
                    onClick={() => send("presentation.review", { asset_id: a.id, approved: true })}
                  />
                )}
                {canModerate && a.status !== "rejected" && (
                  <ActionButton
                    icon={FiX}
                    title="Reject this presentation"
                    tone="rose"
                    onClick={() => send("presentation.review", { asset_id: a.id, approved: false })}
                  />
                )}

                {(mine || canModerate) && (
                  <ActionButton icon={FiTrash2} title={`Delete ${a.filename}`} tone="rose"
                                onClick={() => remove(a)} />
                )}
              </div>
            </div>
          );
        })}

        {assets.length === 0 && (
          <EmptyState
            icon={FiFileText}
            title="No files yet"
            description={
              canPresent
                ? "Upload a PDF or images. A host approves it, then you can put it on screen."
                : "The speakers on this event haven't uploaded anything yet."
            }
            className="py-8"
          />
        )}
      </div>
    </Panel>
  );
}
