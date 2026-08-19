import { useEffect, useMemo, useState } from "react";
import {
  FiUploadCloud, FiSearch, FiPlus, FiTrash2, FiEye, FiEyeOff, FiCopy, FiRefreshCw,
} from "react-icons/fi";
import api, { errMsg } from "../../api";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import CodeBlock from "../../ui/CodeBlock";
import { Input, Label, Select, Textarea } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import useApi from "../../hooks/useApi";
import DataTable from "../../components/admin/DataTable";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";

// Live Inputs — RTMP/WHIP ingest endpoints an on-site encoder publishes into, backed by
// LiveKit's own Ingress API (server/app/services/livekit.py::create_ingress et al.). This
// used to run against a standalone /streams + /channels API that was deleted from the
// backend (see git history for ef66f1c) — rebuilt here as EVENT-scoped rather than tied to
// a persistent "channel" concept, matching how everything else in this app works (one
// LiveKit room per event; an input publishes into a specific event's room as a named
// participant, exactly the on-site primary/backup encoder path the product spec calls for).
//
// State (inactive/buffering/publishing/error/complete) is driven by the ENCODER, not a
// button here — there is no "start/stop" action: you start an input by pointing your
// encoder at its ingest URL, and stop it by disconnecting the encoder. LiveKit's
// ingress_started/ingress_ended webhook (routers/live.py) keeps the state honest.
//
// The publish key is never included in the list — GET /organization/live-inputs/{id}/key
// fetches it live from LiveKit only when the detail sheet is open, and it is never stored
// in this app's own database at all (see models/live.py's LiveIngressEndpoint docstring).
const PAGE_SIZE = 10;

const INPUT_TYPE_LABEL = { rtmp: "RTMP", whip: "WHIP" };
const STATE_BADGE = {
  inactive: { tone: "neutral", label: "Idle" },
  buffering: { tone: "warning", label: "Connecting" },
  publishing: { tone: "success", label: "Live" },
  error: { tone: "danger", label: "Error" },
  complete: { tone: "neutral", label: "Complete" },
};

export default function LiveInputs() {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [detail, setDetail] = useState(null); // the input row, or null

  const { data, loading, reload } = useApi(() =>
    api.get("/organization/live-inputs").then((r) => r.data)
  );
  const inputs = useMemo(() => data || [], [data]);

  useEffect(() => {
    const id = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(id);
  }, [query]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return inputs;
    return inputs.filter((s) =>
      `${s.title} ${s.event_title || ""}`.toLowerCase().includes(q)
    );
  }, [inputs, search]);

  const total = filtered.length;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const items = useMemo(
    () => filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [filtered, safePage]
  );
  const liveCount = items.filter((s) => s.state === "publishing").length;
  const errorCount = items.filter((s) => s.state === "error").length;
  const protocolCounts = useMemo(() => {
    const counts = {};
    for (const s of inputs) counts[s.input_type] = (counts[s.input_type] || 0) + 1;
    return counts;
  }, [inputs]);

  const removeInput = async (stream) => {
    try {
      await api.delete(`/organization/live-inputs/${stream.id}`);
      notify.success("Live input deleted");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const copy = async (value, what) => {
    try {
      await navigator.clipboard.writeText(value);
      notify.success(`${what} copied`);
    } catch {
      notify.error("Clipboard is unavailable in this browser");
    }
  };

  const columns = [
    {
      key: "title",
      header: "Live input",
      render: (s) => (
        <div className="min-w-0">
          <p className={cx("truncate text-[13px] font-semibold", CONSOLE.heading)}>{s.title}</p>
          <p className={cx("truncate text-[11px]", CONSOLE.faint)}>
            {s.event_title || "Untitled event"} · {INPUT_TYPE_LABEL[s.input_type] || s.input_type}
          </p>
        </div>
      ),
    },
    {
      key: "state",
      header: "State",
      render: (s) => {
        const b = STATE_BADGE[s.state] || STATE_BADGE.inactive;
        return (
          <Badge tone={b.tone} dot title={s.error || undefined}>
            {b.label}
          </Badge>
        );
      },
    },
    {
      key: "enforced",
      header: "Provisioned",
      render: (s) =>
        s.enforced ? (
          <span className={cx("text-[12px]", CONSOLE.faint)}>Yes</span>
        ) : (
          <span className="text-[12px] text-amber-600 dark:text-amber-400" title={s.error || "LiveKit was unavailable when this was created"}>
            Not enforced
          </span>
        ),
    },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Live Inputs"
        subtitle="RTMP/WHIP ingest endpoints your encoder publishes into. Publish credentials are shown one input at a time, never in the list."
        actions={
          <>
            <ConsoleButton variant="secondary" leftIcon={FiRefreshCw} onClick={reload} loading={loading}>
              Refresh
            </ConsoleButton>
            <ConsoleButton leftIcon={FiPlus} onClick={() => setCreating(true)}>
              New live input
            </ConsoleButton>
          </>
        }
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Panel title="Inputs" flush>
          <div className="px-4 pb-1 pt-4">
            <div className="relative max-w-xs">
              <FiSearch
                className={cx("pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[15px]", CONSOLE.faint)}
                aria-hidden="true"
              />
              <Input
                variant="console"
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                aria-label="Search live inputs"
                placeholder="Search by title or event…"
                className="pl-9"
              />
            </div>
          </div>

          <DataTable
            columns={columns}
            rows={items}
            rowKey={(s) => s.id}
            onRowClick={setDetail}
            loading={loading}
            pageSize={PAGE_SIZE}
            total={total}
            page={safePage}
            onPageChange={setPage}
            minWidth={620}
            rowActions={(s) => (
              <ConsoleButton
                variant="ghost"
                size="sm"
                iconOnly
                aria-label={`Delete ${s.title}`}
                leftIcon={FiTrash2}
                onClick={() => setConfirmDelete(s)}
              />
            )}
            empty={{
              icon: FiUploadCloud,
              title: search ? "No input matches that search" : "No live inputs yet",
              description: search
                ? "Try a shorter term, or clear the search to see every input."
                : "Create an input to get an ingest URL and a publish key for your encoder.",
              action: search ? null : (
                <ConsoleButton size="sm" leftIcon={FiPlus} onClick={() => setCreating(true)}>
                  New live input
                </ConsoleButton>
              ),
            }}
          />
        </Panel>

        <div className="space-y-4">
          <Panel title="Ingest posture">
            <StatRow label="Inputs total" value={loading ? null : total} reason="Loading" />
            <StatRow
              label="Live on this page"
              value={loading ? null : liveCount}
              reason="Loading"
              tone={liveCount ? "text-green-600 dark:text-green-400" : undefined}
            />
            <StatRow
              label="In an error state"
              value={loading ? null : errorCount}
              reason="Loading"
              tone={errorCount ? "text-rose-600 dark:text-rose-400" : undefined}
            />
            <StatRow
              label="RTMP inputs"
              value={loading ? null : protocolCounts.rtmp || 0}
              reason="Loading"
            />
            <StatRow
              label="WHIP inputs"
              value={loading ? null : protocolCounts.whip || 0}
              reason="Loading"
            />
          </Panel>

          <Panel eyebrow="Encoder" title="Connecting a source">
            <ol className={cx("ml-4 list-decimal space-y-2 text-[13px]", CONSOLE.body)}>
              <li>Create an input against the event it should publish into, then open it to reveal its ingest URL and publish key.</li>
              <li>Point your encoder at the ingest URL with the key as the stream name — never the other way round.</li>
              <li>State updates on its own once the encoder connects — there's no separate "start" button here.</li>
              <li>Disconnecting the encoder is what stops it; delete the input only to permanently retire it.</li>
            </ol>
            <p className={cx("mt-4 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              A publish key is a credential. Delete and recreate the input to rotate it.
            </p>
          </Panel>
        </div>
      </div>

      <CreateInputModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={() => {
          setPage(1);
          reload();
        }}
      />

      <InputDetailSheet input={detail} onClose={() => setDetail(null)} onCopy={copy} />

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        onClose={() => setConfirmDelete(null)}
        title={`Delete "${confirmDelete?.title || ""}"?`}
        body="The input and its publish key stop working immediately. Any encoder still pointed at it will fail to connect. Recordings already produced are not affected."
        confirmLabel="Delete input"
        onConfirm={() => {
          removeInput(confirmDelete);
          setConfirmDelete(null);
        }}
      />
    </div>
  );
}

// Detail sheet — the one surface allowed to show a publish key, fetched live from LiveKit
// only while this is open (GET .../live-inputs/{id}/key) and never cached beyond that.
function InputDetailSheet({ input, onClose, onCopy }) {
  const [showKey, setShowKey] = useState(false);
  const [creds, setCreds] = useState(null); // { ingest_url, stream_key } | null
  const [loadingKey, setLoadingKey] = useState(false);

  useEffect(() => {
    // Resets whenever a different input is opened (or the sheet closes) — an async
    // continuation's own .then/.catch/.finally does the rest, same pattern as
    // EventWatch.jsx's fetchWatch.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setShowKey(false);
    setCreds(null);
    if (!input) return;
    setLoadingKey(true);
    api
      .get(`/organization/live-inputs/${input.id}/key`)
      .then((r) => setCreds(r.data))
      .catch(() => setCreds({ ingest_url: null, stream_key: null }))
      .finally(() => setLoadingKey(false));
  }, [input]);

  return (
    <Modal open={Boolean(input)} onClose={onClose} title={input?.title || "Live input"} size="xl">
      {input && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            {(() => {
              const b = STATE_BADGE[input.state] || STATE_BADGE.inactive;
              return <Badge tone={b.tone} dot>{b.label}</Badge>;
            })()}
            <Badge tone="brand">{INPUT_TYPE_LABEL[input.input_type] || input.input_type}</Badge>
            {input.event_title && <Badge tone="neutral">{input.event_title}</Badge>}
          </div>

          {input.description && (
            <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>{input.description}</p>
          )}
          {input.error && (
            <p className="text-[12px] text-rose-600 dark:text-rose-400">{input.error}</p>
          )}

          <div>
            <Label variant="console">Ingest URL</Label>
            <div className="mt-1 flex items-center gap-2">
              <code
                className={cx(
                  "min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-[12px]",
                  type.mono, CONSOLE.inset,
                  creds?.ingest_url ? CONSOLE.body : CONSOLE.faint
                )}
              >
                {loadingKey ? "Loading…" : creds?.ingest_url || "Unavailable"}
              </code>
              <ConsoleButton
                variant="secondary" size="sm" iconOnly
                aria-label="Copy ingest URL"
                leftIcon={FiCopy}
                disabled={!creds?.ingest_url}
                onClick={() => onCopy(creds.ingest_url, "Ingest URL")}
              />
            </div>
          </div>

          <div>
            <Label variant="console">Publish key</Label>
            <div className="mt-1 flex items-center gap-2">
              <code
                className={cx(
                  "min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-[12px]",
                  type.mono, CONSOLE.inset,
                  creds?.stream_key ? CONSOLE.body : CONSOLE.faint
                )}
              >
                {loadingKey
                  ? "Loading…"
                  : !creds?.stream_key
                    ? "Unavailable"
                    : showKey
                      ? creds.stream_key
                      : "•".repeat(Math.min(creds.stream_key.length, 40))}
              </code>
              <ConsoleButton
                variant="secondary" size="sm" iconOnly
                aria-label={showKey ? "Hide publish key" : "Reveal publish key"}
                leftIcon={showKey ? FiEyeOff : FiEye}
                disabled={!creds?.stream_key}
                onClick={() => setShowKey((v) => !v)}
              />
              <ConsoleButton
                variant="secondary" size="sm" iconOnly
                aria-label="Copy publish key"
                leftIcon={FiCopy}
                disabled={!creds?.stream_key}
                onClick={() => onCopy(creds.stream_key, "Publish key")}
              />
            </div>
            <p className={cx("mt-1.5 text-[11px]", CONSOLE.faint)}>
              Treat this like a password: it grants publish rights to this input.
            </p>
          </div>

          {input.input_type === "rtmp" && (
            <div>
              <Label variant="console">Example encoder command</Label>
              <div className="mt-1">
                <CodeBlock
                  filename="ffmpeg"
                  code={`ffmpeg -re -i source.mp4 \\\n  -c:v libx264 -preset veryfast -b:v 4500k -g 60 \\\n  -c:a aac -b:a 128k -ar 48000 \\\n  -f flv ${showKey && creds?.stream_key ? `${creds.ingest_url}/${creds.stream_key}` : "<ingest-url>/<publish-key>"}`}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </Modal>
  );
}

function CreateInputModal({ open, onClose, onCreated }) {
  const [form, setForm] = useState({ event_id: "", title: "", description: "", input_type: "rtmp" });
  const [saving, setSaving] = useState(false);
  const { data: events } = useApi(() =>
    api.get("/events", { params: { page: 1, page_size: 100, sort_by: "start_time", order: "desc" } })
      .then((r) => (Array.isArray(r.data) ? r.data : r.data?.items || []))
  );
  const list = events || [];

  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setForm({ event_id: list[0]?.id || "", title: "", description: "", input_type: "rtmp" });
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const valid = form.event_id && form.title.trim();

  const create = async () => {
    setSaving(true);
    try {
      const { data } = await api.post("/organization/live-inputs", {
        event_id: form.event_id,
        title: form.title.trim(),
        description: form.description.trim() || null,
        input_type: form.input_type,
      });
      onCreated(data);
      onClose();
      if (!data.enforced) {
        notify.error(data.error || "LiveKit couldn't provision this input — it's saved, but not live yet.");
      } else {
        notify.success("Live input created");
      }
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New live input"
      size="md"
      footer={
        <>
          <ConsoleButton variant="secondary" onClick={onClose} disabled={saving}>
            Cancel
          </ConsoleButton>
          <ConsoleButton disabled={!valid || saving} loading={saving} onClick={create}>
            Create input
          </ConsoleButton>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label variant="console" htmlFor="li-event">
            Event
          </Label>
          <Select variant="console" id="li-event" value={form.event_id} onChange={set("event_id")}>
            {list.length === 0 && <option value="">No events available</option>}
            {list.map((ev) => (
              <option key={ev.id} value={ev.id}>
                {ev.title || "Untitled event"}
              </option>
            ))}
          </Select>
          <p className={cx("mt-1.5 text-[11px]", CONSOLE.faint)}>
            The input publishes into this event's room as a named contributor.
          </p>
        </div>

        <div>
          <Label variant="console" htmlFor="li-type">
            Protocol
          </Label>
          <Select variant="console" id="li-type" value={form.input_type} onChange={set("input_type")}>
            <option value="rtmp">RTMP</option>
            <option value="whip">WHIP</option>
          </Select>
        </div>

        <div>
          <Label variant="console" htmlFor="li-title">
            Title
          </Label>
          <Input
            variant="console"
            id="li-title"
            value={form.title}
            onChange={set("title")}
            placeholder="Main stage — camera A"
            maxLength={120}
          />
        </div>

        <div>
          <Label variant="console" htmlFor="li-desc">
            Description
          </Label>
          <Textarea
            variant="console"
            id="li-desc"
            rows={3}
            value={form.description}
            onChange={set("description")}
            placeholder="What this input carries, and who owns the encoder."
          />
        </div>
      </div>
    </Modal>
  );
}
