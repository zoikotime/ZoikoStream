import { useEffect, useMemo, useState } from "react";
import {
  FiUploadCloud, FiSearch, FiPlus, FiPlay, FiSquare, FiTrash2, FiEye, FiEyeOff,
  FiCopy,
} from "react-icons/fi";
import { CONSOLE, cx, type } from "../../ui/tokens";
import { ConsoleButton } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import CodeBlock from "../../ui/CodeBlock";
import { Input, Label, Select, Textarea } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import DataTable from "../../components/admin/DataTable";
import Panel from "../../components/admin/Panel";
import StatRow from "../../components/admin/StatRow";
import { timeAgo } from "../../components/admin/format";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";

// Live Inputs — the ingest endpoints an encoder publishes into.
//
// STATIC PAGE. This was built against /streams and /channels CRUD, both of which commit
// ef66f1c ("Remove dead channel/stream code") deleted from the backend — there is no streams
// router any more, so every call here could only 404. Rather than ship a page that is nothing
// but an error banner, the list, the create form and the start/stop/delete actions all operate
// on the in-memory sample below: they behave correctly and survive only until reload.
//
// Everything that follows is UI. Point the four handlers at a real API when an ingest
// service exists again; the table, modals and copy need no changes when that happens.
const PAGE_SIZE = 10;

const INGEST_HOST = "rtmps://ingest.zoikostream.com/live";

// A publish key is a credential, so the real API only ever returned it from the per-input read,
// never the list (schemas.StreamListItem omitted it) — precisely so a table could not fan
// twenty credentials across the screen at once. The sample keeps that shape: `stream_key` is
// held here but only ever rendered in the single-input detail sheet.
const INPUTS_SAMPLE = [
  {
    id: "str_9fK2xQ7mAa41", title: "Main stage — camera A", category: "Conference",
    description: "Primary hard-wired encoder in the main auditorium. Owned by the AV team.",
    is_live: true, started_at: new Date(Date.now() - 42 * 60_000).toISOString(),
    stream_key: "live_a41f8c93b7e24d6fa0c5",
  },
  {
    id: "str_3bT8vR2nCc90", title: "Main stage — camera B", category: "Conference",
    description: "Wide-angle backup feed, cut to only if camera A drops.",
    is_live: true, started_at: new Date(Date.now() - 39 * 60_000).toISOString(),
    stream_key: "live_7d2e5b81f4a93c07be16",
  },
  {
    id: "str_5cW1yU4pDd23", title: "Breakout room 2", category: "Training",
    description: "Laptop encoder, presenter-operated. Idle between sessions.",
    is_live: false, started_at: null,
    stream_key: "live_c93a06f5e8d17b42a95f",
  },
  {
    id: "str_8dX6zI9qEe57", title: "Sunday service — sanctuary", category: "Worship",
    description: "Fixed rig behind the balcony; runs unattended on a schedule.",
    is_live: false, started_at: null,
    stream_key: "live_1f84b7d0c62e59a3fd8b",
  },
  {
    id: "str_2eY4aO7rFf88", title: "Field unit — bonded cellular", category: "Broadcast",
    description: "Mobile SRT bonding kit. Key rotated after every deployment.",
    is_live: false, started_at: null,
    stream_key: "live_b50d9e2a71c4f836ad07",
  },
];

// The real create form required a channel the caller owns (POST /streams 403'd otherwise).
// Kept as a static list so the field still demonstrates that ownership rule.
const CHANNELS_SAMPLE = [
  { id: "chn_prod_main", name: "Production — main" },
  { id: "chn_prod_overflow", name: "Production — overflow" },
  { id: "chn_staging", name: "Staging" },
];

const newKey = () =>
  `live_${Array.from({ length: 20 }, () => "0123456789abcdef"[Math.floor(Math.random() * 16)]).join("")}`;

export default function LiveInputs() {
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [creating, setCreating] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [detail, setDetail] = useState(null); // { stream, key } — key fetched on demand
  const [showKey, setShowKey] = useState(false);

  const [inputs, setInputs] = useState(INPUTS_SAMPLE);

  // Debounce the search box, and reset to page 1 when the term changes — page 3 of the old
  // result set is meaningless against a new one.
  useEffect(() => {
    const id = setTimeout(() => setSearch(query.trim()), 300);
    return () => clearTimeout(id);
  }, [query]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    if (!q) return inputs;
    return inputs.filter((s) =>
      `${s.title} ${s.category || ""}`.toLowerCase().includes(q)
    );
  }, [inputs, search]);

  const total = filtered.length;
  // Clamp rather than reset in an effect: deleting the last row of page 3 should land on the
  // new last page, not bounce the operator back to the top.
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const items = useMemo(
    () => filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE),
    [filtered, safePage]
  );
  const liveCount = items.filter((s) => s.is_live).length;

  const toggleLive = (stream) => {
    setInputs((list) =>
      list.map((s) =>
        s.id === stream.id
          ? { ...s, is_live: !s.is_live, started_at: s.is_live ? null : new Date().toISOString() }
          : s
      )
    );
    notify.success(stream.is_live ? "Input stopped" : "Input started");
  };

  const removeInput = (stream) => {
    setInputs((list) => list.filter((s) => s.id !== stream.id));
    notify.success("Live input deleted");
  };

  // No per-input fetch to make: the key is already on the row, and this sheet is still the
  // only surface that renders it.
  const openDetail = (stream) => {
    setShowKey(false);
    setDetail({ stream, key: stream.stream_key || null });
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
            {s.category || "Uncategorised"}
            {s.started_at ? ` · started ${timeAgo(s.started_at)}` : ""}
          </p>
        </div>
      ),
    },
    {
      key: "is_live",
      header: "State",
      render: (s) =>
        s.is_live ? (
          <Badge tone="success" dot>
            Live
          </Badge>
        ) : (
          <Badge tone="neutral" dot>
            Idle
          </Badge>
        ),
    },
    {
      key: "id",
      header: "Input id",
      mono: true,
      render: (s) => (
        <span className={cx(type.mono, "text-[12px]", CONSOLE.faint)}>{String(s.id).slice(0, 8)}…</span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Live Inputs"
        subtitle="Ingest endpoints your encoder publishes into. Publish credentials are shown one input at a time, never in the list."
        actions={
          /* ponytail: no Refresh control — there is nothing to refetch, and an inert button
             labelled "Refresh" is worse than no button. */
          <ConsoleButton leftIcon={FiPlus} onClick={() => setCreating(true)}>
            New live input
          </ConsoleButton>
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
                placeholder="Search by title…"
                className="pl-9"
              />
            </div>
          </div>

          <DataTable
            columns={columns}
            rows={items}
            rowKey={(s) => s.id}
            onRowClick={openDetail}
            /* Server mode: `items` IS one page and `total` is the full filtered count, so the
               table must not re-slice or re-count it. */
            pageSize={PAGE_SIZE}
            total={total}
            page={safePage}
            onPageChange={setPage}
            minWidth={620}
            rowActions={(s) => (
              <>
                <ConsoleButton
                  variant="ghost"
                  size="sm"
                  iconOnly
                  aria-label={s.is_live ? `Stop ${s.title}` : `Start ${s.title}`}
                  leftIcon={s.is_live ? FiSquare : FiPlay}
                  onClick={() => toggleLive(s)}
                />
                <ConsoleButton
                  variant="ghost"
                  size="sm"
                  iconOnly
                  aria-label={`Delete ${s.title}`}
                  leftIcon={FiTrash2}
                  onClick={() => setConfirmDelete(s)}
                />
              </>
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
            <StatRow label="Inputs total" value={total} reason="Loading" />
            <StatRow
              label="Live on this page"
              value={liveCount}
              reason="Loading"
              tone={liveCount ? "text-green-600 dark:text-green-400" : undefined}
            />
            <StatRow
              label="Ingest protocol in use"
              value={null}
              reason="Protocol and region are not recorded on a session (documented gap)"
            />
            <StatRow
              label="Connection health"
              value={null}
              reason="Encoder-side connection telemetry is not ingested"
            />
          </Panel>

          <Panel eyebrow="Encoder" title="Connecting a source">
            <ol className={cx("ml-4 list-decimal space-y-2 text-[13px]", CONSOLE.body)}>
              <li>Create an input, then open it to reveal its publish key.</li>
              <li>
                Point your encoder at the ingest URL with the key as the stream name — never the
                other way round.
              </li>
              <li>Start the input here, then start sending from the encoder.</li>
              <li>Stop the input when the source disconnects so the state stays truthful.</li>
            </ol>
            <p className={cx("mt-4 border-t pt-3 text-[12px]", CONSOLE.divider, CONSOLE.faint)}>
              A publish key is a credential. Rotate it by replacing the input if it leaks.
            </p>
          </Panel>
        </div>
      </div>

      <CreateInputModal
        open={creating}
        onClose={() => setCreating(false)}
        onCreate={(input) => {
          setInputs((list) => [input, ...list]);
          setPage(1);
          notify.success("Live input created");
        }}
      />

      {/* Detail sheet — the one surface allowed to show a publish key, one input at a time. */}
      <Modal
        open={Boolean(detail)}
        onClose={() => {
          setDetail(null);
          setShowKey(false);
        }}
        title={detail?.stream?.title || "Live input"}
        size="xl"
      >
        {detail && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center gap-2">
              {detail.stream.is_live ? (
                <Badge tone="success" dot>
                  Live
                </Badge>
              ) : (
                <Badge tone="neutral" dot>
                  Idle
                </Badge>
              )}
              {detail.stream.category && <Badge tone="brand">{detail.stream.category}</Badge>}
            </div>

            {detail.stream.description && (
              <p className={cx("text-[13px] leading-[20px]", CONSOLE.muted)}>{detail.stream.description}</p>
            )}

            <div>
              <Label variant="console">Ingest URL</Label>
              <div className="mt-1 flex items-center gap-2">
                <code
                  className={cx(
                    "min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-[12px]",
                    type.mono,
                    CONSOLE.inset,
                    CONSOLE.body
                  )}
                >
                  {INGEST_HOST}
                </code>
                <ConsoleButton
                  variant="secondary"
                  size="sm"
                  iconOnly
                  aria-label="Copy ingest URL"
                  leftIcon={FiCopy}
                  onClick={() => copy(INGEST_HOST, "Ingest URL")}
                />
              </div>
            </div>

            <div>
              <Label variant="console">Publish key</Label>
              <div className="mt-1 flex items-center gap-2">
                <code
                  className={cx(
                    "min-w-0 flex-1 truncate rounded-lg px-3 py-2 text-[12px]",
                    type.mono,
                    CONSOLE.inset,
                    detail.key ? CONSOLE.body : CONSOLE.faint
                  )}
                >
                  {!detail.key
                    ? "Unavailable"
                    : showKey
                      ? detail.key
                      : "•".repeat(Math.min(detail.key.length, 40))}
                </code>
                <ConsoleButton
                  variant="secondary"
                  size="sm"
                  iconOnly
                  aria-label={showKey ? "Hide publish key" : "Reveal publish key"}
                  leftIcon={showKey ? FiEyeOff : FiEye}
                  disabled={!detail.key}
                  onClick={() => setShowKey((v) => !v)}
                />
                <ConsoleButton
                  variant="secondary"
                  size="sm"
                  iconOnly
                  aria-label="Copy publish key"
                  leftIcon={FiCopy}
                  disabled={!detail.key}
                  onClick={() => copy(detail.key, "Publish key")}
                />
              </div>
              <p className={cx("mt-1.5 text-[11px]", CONSOLE.faint)}>
                Treat this like a password: it grants publish rights to this input.
              </p>
            </div>

            <div>
              <Label variant="console">Example encoder command</Label>
              <div className="mt-1">
                <CodeBlock
                  filename="ffmpeg"
                  code={`ffmpeg -re -i source.mp4 \\\n  -c:v libx264 -preset veryfast -b:v 4500k -g 60 \\\n  -c:a aac -b:a 128k -ar 48000 \\\n  -f flv ${INGEST_HOST}/${showKey && detail.key ? detail.key : "<publish-key>"}`}
                />
              </div>
            </div>
          </div>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        onClose={() => setConfirmDelete(null)}
        title={`Delete “${confirmDelete?.title || ""}”?`}
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

// An input belongs to a channel the caller owns — the rule the real POST enforced, kept here so
// the form still shows it. The channel list is static along with everything else on this page.
function CreateInputModal({ open, onClose, onCreate }) {
  const [form, setForm] = useState({ channel_id: "", title: "", description: "", category: "" });
  const list = CHANNELS_SAMPLE;
  // Render-phase reset when the dialog opens, the pattern DataTable uses — an effect that
  // calls setState would cascade a second render on every open.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) setForm({ channel_id: list[0]?.id || "", title: "", description: "", category: "" });
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));
  const valid = form.channel_id && form.title.trim();

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="New live input"
      size="md"
      footer={
        <>
          <ConsoleButton variant="secondary" onClick={onClose}>
            Cancel
          </ConsoleButton>
          <ConsoleButton
            disabled={!valid}
            onClick={() => {
              onCreate({
                id: `str_${newKey().slice(5, 17)}`,
                title: form.title.trim(),
                description: form.description.trim() || null,
                category: form.category.trim() || null,
                is_live: false,
                started_at: null,
                stream_key: newKey(),
              });
              onClose();
            }}
          >
            Create input
          </ConsoleButton>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label variant="console" htmlFor="li-channel">
            Channel
          </Label>
          <Select
            variant="console"
            id="li-channel"
            value={form.channel_id}
            onChange={set("channel_id")}
          >
            {list.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
          <p className={cx("mt-1.5 text-[11px]", CONSOLE.faint)}>
            An input belongs to a channel you own.
          </p>
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
          <Label variant="console" htmlFor="li-category">
            Category
          </Label>
          <Input
            variant="console"
            id="li-category"
            value={form.category}
            onChange={set("category")}
            placeholder="Conference, worship, training…"
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
