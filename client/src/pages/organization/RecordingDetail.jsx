// client/src/pages/organization/RecordingDetail.jsx
// One recording: player, transcript, derived insights, bookmarks/notes, metadata and download
// policy. Route: /organization/recordings/:id  (?t=754 deep-links to a position).
import { useCallback, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams, useNavigate } from "react-router-dom";
import {
  FiArrowLeft, FiDownload, FiShare2, FiEdit2, FiSave, FiX, FiLock, FiUnlock, FiSearch,
  FiCopy, FiFileText, FiUploadCloud, FiZap, FiEye, FiHardDrive, FiClock, FiTag,
  FiRefreshCw, FiChevronRight, FiAlertTriangle,
} from "react-icons/fi";
import { cx } from "../../ui/tokens";
import Card from "../../ui/Card";
import Button from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import { notify } from "../../ui/Toast";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { fmtDate } from "../../data/events";
import MediaPlayer, { ChapterList } from "../../components/media/MediaPlayer";
import {
  CATEGORIES, VISIBILITY, DOWNLOAD_MODES, fmtBytes, fmtCount, fmtClock, fmtDuration,
  parseTimestamp, statusOf,
} from "../../data/media";

const control =
  "rounded-xl border border-slate-200 bg-white px-3.5 py-2 text-sm text-slate-700 shadow-sm outline-none focus:border-emerald-400 focus:ring-2 focus:ring-emerald-500/20 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

const TABS = [
  { key: "chapters", label: "Chapters & marks" },
  { key: "transcript", label: "Transcript" },
  { key: "insights", label: "Insights" },
  { key: "details", label: "Details" },
];

export default function RecordingDetail() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const startAtMs = useMemo(() => parseTimestamp(params.get("t")), [params]);

  const { user } = useAuth();
  const detail = useApi(() => api.get(`/media/recordings/${id}`).then((r) => r.data));
  // Fetched here, not only inside the Insights tab, because the CHAPTERS drive the player's
  // scrubber markers and the chapter list — they have to exist before that tab is ever opened.
  const insights = useApi(() => api.get(`/media/recordings/${id}/insights`).then((r) => r.data));
  const [tab, setTab] = useState("chapters");
  const seekRef = useRef(null);
  const captureSeek = useCallback((fn) => { seekRef.current = fn; }, []);
  const seek = (ms) => seekRef.current?.(ms);

  const save = useMutation({ success: "Saved", onDone: detail.reload });

  const rec = detail.data?.recording;
  const canManage = detail.data?.can_manage;

  const countView = useCallback(() => {
    // Fire and forget: a failed view counter must not interrupt playback.
    api.post(`/media/recordings/${id}/view`).catch(() => {});
  }, [id]);

  const addMark = async (atMs, note = null) => {
    try {
      await api.post(`/media/recordings/${id}/marks`, { at_ms: Math.floor(atMs), note, shared: false });
      notify.success(note ? "Note saved" : `Bookmarked at ${fmtClock(atMs)}`);
      detail.reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const deleteMark = async (markId) => {
    try {
      await api.delete(`/media/recordings/${id}/marks/${markId}`);
      detail.reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  if (detail.loading) {
    return (
      <div className="space-y-4">
        <div className="zk-skeleton h-6 w-52 rounded bg-slate-100 dark:bg-slate-800" />
        <div className="zk-skeleton aspect-video w-full rounded-2xl bg-slate-100 dark:bg-slate-800" />
      </div>
    );
  }

  if (detail.error || !rec) {
    return (
      <Card className="py-16 text-center">
        <p className="font-medium text-slate-700 dark:text-slate-200">This recording is not available</p>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          It may have been deleted, or it belongs to a different organization.
        </p>
        <Button className="mt-4" variant="secondary" onClick={() => navigate("/organization/recordings")}>
          <FiArrowLeft /> Back to the library
        </Button>
      </Card>
    );
  }

  const status = statusOf(rec);
  const insightsChapters = insights.data?.chapters || [];
  // The watermark is the VIEWER's identity — that is the only thing it can usefully deter. Email
  // over name: two colleagues can share a display name, and a leaked frame has to identify one
  // person.
  const watermarkLabel = detail.data.download?.watermark
    ? (user?.email || user?.full_name || null)
    : null;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <Link
            to="/organization/recordings"
            className="mb-1 inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
          >
            <FiArrowLeft /> Media Library
          </Link>
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="truncate text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
              {rec.title}
            </h1>
            <Badge tone={status.tone} size="sm">{status.label}</Badge>
          </div>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-slate-500 dark:text-slate-400">
            {rec.event_title && (
              <Link to={`/organization/events/${rec.event_id}`} className="inline-flex items-center gap-1 hover:text-emerald-600">
                {rec.event_title} <FiChevronRight className="text-xs" />
              </Link>
            )}
            {rec.stopped_at && <span>{fmtDate(rec.stopped_at)}</span>}
            <span className="inline-flex items-center gap-1"><FiClock /> {fmtDuration(rec.duration_ms)}</span>
            <span className="inline-flex items-center gap-1"><FiHardDrive /> {fmtBytes(rec.size_bytes)}</span>
            <span className="inline-flex items-center gap-1"><FiEye /> {fmtCount(rec.view_count)} views</span>
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <DownloadButton recordingId={id} download={detail.data.download} hasFile={rec.has_file} />
          <Button
            size="sm" variant="secondary"
            onClick={() => {
              const url = window.location.href.split("?")[0];
              navigator.clipboard?.writeText(url).then(
                () => notify.success("Link copied"), () => notify.info(url));
            }}
          >
            <FiShare2 className="text-base" /> Share
          </Button>
        </div>
      </div>

      {!rec.has_file && (
        <Card className="border-amber-200 bg-amber-50 dark:border-amber-500/30 dark:bg-amber-500/10">
          <div className="flex items-start gap-3 text-sm">
            <FiAlertTriangle className="mt-0.5 shrink-0 text-lg text-amber-600 dark:text-amber-400" />
            <div>
              <p className="font-semibold text-amber-900 dark:text-amber-200">No video file for this recording</p>
              <p className="text-amber-800 dark:text-amber-300/90">
                {rec.error || "The capture did not produce a file."}
                {rec.retryable && " While the broadcast is still live, a host can retry the capture from the control room."}
              </p>
            </div>
          </div>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-5 xl:grid-cols-[minmax(0,1fr)_22rem]">
        <div className="space-y-4">
          <MediaPlayer
            src={detail.data.playback_url}
            captionsUrl={detail.data.transcript_available
              ? `${api.defaults.baseURL}/media/recordings/${id}/transcript.vtt` : null}
            durationMs={rec.duration_ms}
            chapters={insightsChapters}
            marks={detail.data.marks || []}
            quality={rec.quality}
            watermark={watermarkLabel}
            startAtMs={startAtMs}
            recordingId={id}
            onAddMark={(ms) => addMark(ms)}
            onFirstPlay={countView}
            onSeekRequest={captureSeek}
          />

          {rec.description && (
            <Card padding="md">
              <p className="whitespace-pre-wrap text-sm text-slate-600 dark:text-slate-300">{rec.description}</p>
            </Card>
          )}
        </div>

        {/* Side panel */}
        <Card padding="none" className="flex h-fit flex-col overflow-hidden">
          <div className="flex overflow-x-auto border-b border-slate-100 dark:border-slate-800">
            {TABS.map((t) => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                className={cx(
                  "shrink-0 border-b-2 px-3 py-2.5 text-sm font-medium transition",
                  tab === t.key
                    ? "border-emerald-500 text-emerald-600 dark:text-emerald-400"
                    : "border-transparent text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
                )}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="p-3">
            {tab === "chapters" && (
              <MarksTab
                marks={detail.data.marks || []}
                chapters={insightsChapters}
                onSeek={seek}
                onDeleteMark={deleteMark}
              />
            )}
            {tab === "transcript" && (
              <TranscriptTab recordingId={id} canManage={canManage} onSeek={seek} onChanged={detail.reload} />
            )}
            {tab === "insights" && <InsightsTab recordingId={id} onSeek={seek} />}
            {tab === "details" && (
              <DetailsTab rec={rec} canManage={canManage} download={detail.data.download}
                recordingId={id} save={save} />
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ── download ─────────────────────────────────────────────────────────────────

function DownloadButton({ recordingId, download, hasFile }) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);

  const request = async (password) => {
    setBusy(true);
    try {
      const { data } = await api.post(`/media/recordings/${recordingId}/download`,
        password ? { password } : {});
      // The server returns a short-lived signed URL rather than streaming the file, so the browser
      // fetches it straight from storage and the API never proxies gigabytes.
      window.location.href = data.url;
      setAsking(false);
      if (data.watermarked) {
        notify.info("This recording is watermarked in the player. The downloaded file is not — that needs a re-encode.");
      }
    } catch (e) {
      const detail = e?.response?.data?.detail;
      if (detail?.needs_password) {
        setAsking(true);
        if (password) notify.error(detail.error || "Incorrect password");
      } else {
        notify.error(typeof detail === "object" ? detail.error : errMsg(e));
      }
    } finally {
      setBusy(false);
    }
  };

  if (!hasFile) return null;
  if (download?.mode === "disabled") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-xl border border-slate-200 px-3 py-2 text-sm text-slate-400 dark:border-slate-700">
        <FiLock /> Downloads off
      </span>
    );
  }

  return (
    <>
      <Button size="sm" onClick={() => (download?.mode === "password" ? setAsking(true) : request())} disabled={busy}>
        <FiDownload className="text-base" />
        {download?.mode === "password" ? "Download (protected)" : "Download"}
      </Button>
      <Modal open={asking} onClose={() => setAsking(false)} title="This download is password protected">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            request(new FormData(e.currentTarget).get("password"));
          }}
          className="space-y-4"
        >
          <label className="block">
            <span className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-200">Passphrase</span>
            <input name="password" type="password" required autoFocus className={cx(control, "w-full")} />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" onClick={() => setAsking(false)}>Cancel</Button>
            <Button type="submit" disabled={busy}><FiDownload /> Download</Button>
          </div>
        </form>
      </Modal>
    </>
  );
}

// ── tabs ─────────────────────────────────────────────────────────────────────

function MarksTab({ marks, chapters, onSeek, onDeleteMark }) {
  return (
    <div className="space-y-3">
      <ChapterList chapters={chapters} marks={marks} onSeek={onSeek} onDeleteMark={onDeleteMark} />
      <p className="border-t border-slate-100 pt-2.5 text-[11px] text-slate-400 dark:border-slate-800">
        Chapters come from real moments in the event — polls launched, announcements sent, speakers
        taking the stage. Bookmarks and notes are yours unless you share them.
      </p>
    </div>
  );
}

function TranscriptTab({ recordingId, canManage, onSeek, onChanged }) {
  const transcript = useApi(() => api.get(`/media/recordings/${recordingId}/transcript`).then((r) => r.data));
  const [query, setQuery] = useState("");
  const upload = useMutation({
    success: "Transcript attached",
    onDone: () => { transcript.reload(); onChanged?.(); },
  });

  // `|| []` inline would be a new array identity every render, so the filter below would re-run on
  // every keystroke anywhere on the page. Anchored to the fetched value instead.
  const segments = useMemo(() => transcript.data?.segments || [], [transcript.data]);
  const shown = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle.length < 2) return segments;
    return segments.filter((s) => s.text.toLowerCase().includes(needle));
  }, [segments, query]);

  const copyAll = () => {
    const text = segments
      .map((s) => `[${fmtClock(s.start_ms)}] ${s.speaker ? `${s.speaker}: ` : ""}${s.text}`)
      .join("\n");
    navigator.clipboard?.writeText(text).then(
      () => notify.success("Transcript copied"),
      () => notify.error("Clipboard access was blocked")
    );
  };

  if (transcript.loading) {
    return <div className="zk-skeleton h-40 rounded bg-slate-100 dark:bg-slate-800" />;
  }

  if (!transcript.data?.available) {
    return (
      <div className="space-y-3 px-1 py-4 text-center">
        <FiFileText className="mx-auto text-2xl text-slate-300 dark:text-slate-600" />
        <p className="text-sm text-slate-600 dark:text-slate-300">{transcript.data?.reason}</p>
        {canManage && (
          <label className="inline-flex cursor-pointer items-center gap-1.5 rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 transition hover:bg-slate-50 dark:border-slate-700 dark:text-slate-300 dark:hover:bg-slate-800">
            <FiUploadCloud /> Upload .vtt or .srt
            <input
              type="file" accept=".vtt,.srt,text/vtt,text/plain" className="hidden"
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (!file) return;
                const body = new FormData();
                body.append("file", file);
                upload.run(() => api.post(`/media/recordings/${recordingId}/transcript`, body));
                e.target.value = "";
              }}
            />
          </label>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <div className="relative flex-1">
          <FiSearch className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-slate-400" />
          <input
            type="search" value={query} onChange={(e) => setQuery(e.target.value)}
            placeholder="Search the transcript…" aria-label="Search the transcript"
            className={cx(control, "w-full py-1.5 pl-8 text-sm")}
          />
        </div>
        <button onClick={copyAll} title="Copy the whole transcript" aria-label="Copy transcript"
          className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800">
          <FiCopy className="text-sm" />
        </button>
        <a
          href={`${api.defaults.baseURL}/media/recordings/${recordingId}/transcript.txt`}
          title="Download the transcript" aria-label="Download transcript"
          className="grid h-8 w-8 place-items-center rounded-lg border border-slate-200 text-slate-500 hover:bg-slate-50 dark:border-slate-700 dark:hover:bg-slate-800"
        >
          <FiDownload className="text-sm" />
        </a>
      </div>

      {query.trim().length >= 2 && (
        <p className="text-xs text-slate-500 dark:text-slate-400">
          {shown.length} of {segments.length} lines match
        </p>
      )}

      {transcript.data.speaker_separation_note && (
        <p className="rounded-lg bg-slate-50 px-2.5 py-1.5 text-[11px] text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          {transcript.data.speaker_separation_note}
        </p>
      )}

      <ul className="max-h-96 space-y-1 overflow-y-auto pr-1">
        {shown.map((s, i) => (
          <li key={`${s.start_ms}-${i}`}>
            <button
              onClick={() => onSeek?.(s.start_ms)}
              className="flex w-full gap-2.5 rounded-lg px-2 py-1.5 text-left transition hover:bg-slate-50 dark:hover:bg-slate-800"
            >
              <span className="mt-0.5 shrink-0 text-[11px] tabular-nums text-emerald-600 dark:text-emerald-400">
                {fmtClock(s.start_ms)}
              </span>
              <span className="min-w-0 text-sm text-slate-600 dark:text-slate-300">
                {s.speaker && <strong className="font-semibold text-slate-800 dark:text-slate-100">{s.speaker}: </strong>}
                {s.text}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function InsightsTab({ recordingId, onSeek }) {
  const insights = useApi(() => api.get(`/media/recordings/${recordingId}/insights`).then((r) => r.data));
  const refresh = useMutation({ success: "Insights recalculated", onDone: insights.reload });

  if (insights.loading) {
    return <div className="zk-skeleton h-48 rounded bg-slate-100 dark:bg-slate-800" />;
  }
  const d = insights.data;
  if (!d) return <p className="py-6 text-center text-sm text-slate-400">Insights are unavailable.</p>;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] text-slate-400">
          {d.cached ? "Cached" : "Just computed"} · source: {d.provider}
        </p>
        <button
          onClick={() => refresh.run(() => api.get(`/media/recordings/${recordingId}/insights?refresh=true`))}
          disabled={refresh.busy}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"
        >
          <FiRefreshCw className={cx("text-xs", refresh.busy && "animate-spin")} /> Recalculate
        </button>
      </div>

      {/* Model-dependent fields, stated rather than faked. */}
      {["summary", "action_items"].map((key) => (
        <section key={key}>
          <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-400">
            {key === "summary" ? "Summary" : "Action items"}
          </h3>
          {d[key]?.available ? (
            <p className="whitespace-pre-wrap text-sm text-slate-600 dark:text-slate-300">{d[key].value}</p>
          ) : (
            <p className="rounded-lg bg-slate-50 px-2.5 py-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
              {d[key]?.reason}
            </p>
          )}
        </section>
      ))}

      {d.decisions?.items?.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Decisions</h3>
          <ul className="space-y-2">
            {d.decisions.items.map((item, i) => (
              <li key={i} className="rounded-lg border border-slate-100 p-2.5 dark:border-slate-800">
                <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{item.question}</p>
                <p className="mt-0.5 text-xs text-slate-600 dark:text-slate-300">
                  <strong>{item.outcome}</strong> · {item.votes} of {item.total_votes} votes ({item.share}%)
                </p>
                {item.at_ms != null && (
                  <button onClick={() => onSeek?.(item.at_ms)} className="mt-1 text-[11px] font-medium text-emerald-600 hover:underline dark:text-emerald-400">
                    Jump to {fmtClock(item.at_ms)}
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {d.moments?.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
            Busiest moments
          </h3>
          <ul className="space-y-0.5">
            {d.moments.map((m, i) => (
              <li key={i}>
                <button
                  onClick={() => onSeek?.(m.at_ms)}
                  className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm transition hover:bg-slate-50 dark:hover:bg-slate-800"
                >
                  <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] tabular-nums text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                    {fmtClock(m.at_ms)}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-slate-600 dark:text-slate-300">{m.label}</span>
                  <FiZap className="shrink-0 text-xs text-amber-500" />
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {d.keywords?.length > 0 && (
        <section>
          <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Keywords</h3>
          <div className="flex flex-wrap gap-1.5">
            {d.keywords.slice(0, 16).map((k) => (
              <span key={k.term} className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                {k.term} <span className="text-slate-400">{k.count}</span>
              </span>
            ))}
          </div>
        </section>
      )}

      <section>
        <h3 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">Engagement</h3>
        <dl className="grid grid-cols-2 gap-2 text-sm">
          {[
            ["Messages", d.engagement?.messages], ["Questions", d.engagement?.questions],
            ["Answered", d.engagement?.questions_answered], ["Poll votes", d.engagement?.poll_votes],
            ["Reactions", d.engagement?.reactions], ["Peak viewers", d.engagement?.peak_viewers],
          ].map(([label, value]) => (
            <div key={label} className="rounded-lg bg-slate-50 px-2.5 py-1.5 dark:bg-slate-800/60">
              <dt className="text-[11px] text-slate-500 dark:text-slate-400">{label}</dt>
              <dd className="font-semibold tabular-nums text-slate-800 dark:text-slate-100">{fmtCount(value)}</dd>
            </div>
          ))}
        </dl>
      </section>

      {d.sentiment && !d.sentiment.available && (
        <p className="rounded-lg bg-slate-50 px-2.5 py-2 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          <strong className="font-semibold">Sentiment:</strong> {d.sentiment.reason}
        </p>
      )}
    </div>
  );
}

function DetailsTab({ rec, canManage, download, recordingId, save }) {
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({
    title: rec.custom_title || "",
    description: rec.description || "",
    category: rec.category || "",
    visibility: rec.visibility,
    tags: (rec.tags || []).join(", "),
  });

  const submit = (e) => {
    e.preventDefault();
    save.run(
      () => api.patch(`/media/recordings/${recordingId}`, {
        title: form.title,
        description: form.description,
        category: form.category || null,
        visibility: form.visibility,
        tags: form.tags.split(",").map((t) => t.trim()).filter(Boolean),
      }),
      { onDone: () => setEditing(false) }
    );
  };

  if (editing) {
    return (
      <form onSubmit={submit} className="space-y-3">
        <Field label="Title">
          <input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })}
            maxLength={200} placeholder={rec.event_title || "Recording"} className={cx(control, "w-full")} />
          <p className="mt-1 text-[11px] text-slate-400">Leave empty to use the event&rsquo;s title.</p>
        </Field>
        <Field label="Description">
          <textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })}
            rows={3} maxLength={5000} className={cx(control, "w-full resize-y")} />
        </Field>
        <Field label="Category">
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}
            className={cx(control, "w-full")}>
            <option value="">Uncategorised</option>
            {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </Field>
        <Field label="Who can watch">
          <select value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value })}
            className={cx(control, "w-full")}>
            {VISIBILITY.map((v) => <option key={v.key} value={v.key}>{v.label}</option>)}
          </select>
          <p className="mt-1 text-[11px] text-slate-400">
            {VISIBILITY.find((v) => v.key === form.visibility)?.hint}
          </p>
        </Field>
        <Field label="Tags">
          <input value={form.tags} onChange={(e) => setForm({ ...form, tags: e.target.value })}
            placeholder="sales, emea, q3" className={cx(control, "w-full")} />
          <p className="mt-1 text-[11px] text-slate-400">Comma separated, up to 12.</p>
        </Field>
        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" size="sm" variant="secondary" onClick={() => setEditing(false)}>
            <FiX /> Cancel
          </Button>
          <Button type="submit" size="sm" disabled={save.busy}><FiSave /> Save</Button>
        </div>
      </form>
    );
  }

  return (
    <div className="space-y-3 text-sm">
      <Row label="Event" value={rec.event_title} />
      <Row label="Recorded" value={rec.stopped_at ? fmtDate(rec.stopped_at) : "—"} />
      <Row label="Length" value={fmtDuration(rec.duration_ms)} />
      <Row label="Size" value={fmtBytes(rec.size_bytes)} />
      <Row label="Captured at" value={rec.quality ? rec.quality.toUpperCase() : "—"} />
      <Row label="Category" value={rec.category || "Uncategorised"} />
      <Row label="Who can watch" value={VISIBILITY.find((v) => v.key === rec.visibility)?.label || rec.visibility} />
      <Row label="Views" value={fmtCount(rec.view_count)} />
      <Row label="Downloads" value={fmtCount(rec.download_count)} />
      <Row
        label="Download policy"
        value={
          <span className="inline-flex items-center gap-1.5">
            {download?.mode === "disabled" ? <FiLock className="text-xs" /> : <FiUnlock className="text-xs" />}
            {DOWNLOAD_MODES.find((m) => m.key === download?.mode)?.label || download?.mode}
            {rec.download_policy && <span className="text-[10px] text-slate-400">(override)</span>}
          </span>
        }
      />
      {download?.expires_at && <Row label="Download window ends" value={fmtDate(download.expires_at)} />}
      {rec.tags?.length > 0 && (
        <div>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Tags</p>
          <div className="flex flex-wrap gap-1.5">
            {rec.tags.map((t) => (
              <span key={t} className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
                <FiTag className="text-[10px]" /> {t}
              </span>
            ))}
          </div>
        </div>
      )}
      {download?.watermark && (
        <p className="rounded-lg bg-slate-50 px-2.5 py-2 text-[11px] text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          Watermarking is on: the player overlays the viewer&rsquo;s identity. A downloaded file
          cannot carry a watermark without re-encoding it, which this deployment does not do.
        </p>
      )}
      {canManage && (
        <Button size="sm" variant="secondary" className="w-full" onClick={() => setEditing(true)}>
          <FiEdit2 /> Edit details
        </Button>
      )}
    </div>
  );
}

function Row({ label, value }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-slate-50 pb-1.5 last:border-0 dark:border-slate-800/60">
      <dt className="shrink-0 text-slate-500 dark:text-slate-400">{label}</dt>
      <dd className="min-w-0 truncate text-right font-medium text-slate-800 dark:text-slate-100">{value || "—"}</dd>
    </div>
  );
}

function Field({ label, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-slate-700 dark:text-slate-200">{label}</span>
      {children}
    </label>
  );
}
