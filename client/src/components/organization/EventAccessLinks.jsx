import { useState } from "react";
import {
  FiCopy, FiKey, FiLink, FiPlus, FiRefreshCw, FiSlash, FiTrash2, FiExternalLink,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import SectionCard from "../admin/SectionCard";
import EmptyState from "./OrganizationEmptyState";
import ErrorState from "./OrganizationErrorState";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import Skeleton from "../../ui/Skeleton";
import { Input, Label, Select } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import { cx } from "../../ui/tokens";
import { fmtDateTime, visLabel, VISIBILITY_HELP } from "../../data/events";

// Viewer access links for one event.
//
// The security model is the org invitation's, deliberately: the raw token is shown ONCE on
// create/rotate and only its sha256 hash is stored server-side. That is why there is no
// "copy" button on an existing row — the secret genuinely cannot be recovered, and offering
// a control that would need it is how a UI teaches users to expect the impossible.
// Losing a link is handled by Regenerate, which re-secrets the same row.

const EXPIRY_OPTIONS = [
  { value: "1", label: "1 day" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "", label: "Never expires" },
];

/** Live | Expired | Revoked — computed from the row, so the badge cannot disagree with what
 *  the server will actually accept (crud.event.find_access_link applies the same three rules). */
function linkState(link) {
  if (link.revoked_at) return { label: "Revoked", tone: "danger" };
  if (link.expires_at && new Date(link.expires_at) < new Date()) return { label: "Expired", tone: "warning" };
  return { label: "Active", tone: "success" };
}

/** The one-time reveal. Shown after create and after regenerate — the only two moments the
 *  raw token exists outside the recipient's hands. */
function RevealDialog({ issued, onClose }) {
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(issued.url);
      notify.success("Viewer link copied");
    } catch {
      notify.error("Couldn't copy — select the link and copy it manually");
    }
  };

  return (
    <Modal
      open={!!issued}
      onClose={onClose}
      title="Viewer link created"
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose}>Done</Button>
          <Button size="sm" leftIcon={FiCopy} onClick={copy}>Copy link</Button>
        </>
      }
    >
      <p>
        Share this link with the people who should be able to watch. It is shown{" "}
        <strong className="font-semibold text-slate-800 dark:text-slate-100">only now</strong> —
        the platform stores a hash, not the link, so it cannot be shown again. Regenerate the
        link if it is lost.
      </p>
      <label className="mt-4 block">
        <span className="sr-only">Viewer link</span>
        <input
          readOnly
          value={issued?.url || ""}
          onFocus={(e) => e.target.select()}
          className={cx(
            "w-full rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 font-mono text-xs text-slate-700",
            "dark:border-slate-700 dark:bg-slate-800 dark:text-slate-200"
          )}
        />
      </label>
      <p className="mt-2 text-xs text-slate-500 dark:text-slate-400">
        {issued?.expires_at
          ? `Expires ${fmtDateTime(issued.expires_at)}.`
          : "This link does not expire. Revoke it when it is no longer needed."}
      </p>
    </Modal>
  );
}

function CreateDialog({ open, onClose, onCreate, busy }) {
  const [label, setLabel] = useState("");
  const [days, setDays] = useState("7");

  const submit = async () => {
    const ok = await onCreate({
      label: label.trim() || null,
      expires_in_days: days === "" ? null : Number(days),
    });
    if (ok) {
      setLabel("");
      setDays("7");
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Generate viewer link"
      size="lg"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button size="sm" leftIcon={FiLink} loading={busy} disabled={busy} onClick={submit}>
            Generate link
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label variant="console" htmlFor="zk-link-label">Label</Label>
          <Input
            id="zk-link-label"
            variant="console"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. Press, Partners, Internal"
            maxLength={120}
          />
          <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
            For your own reference — it lets you revoke one audience without affecting the others.
          </p>
        </div>
        <div>
          <Label variant="console" htmlFor="zk-link-expiry">Expires</Label>
          <Select
            id="zk-link-expiry"
            variant="console"
            value={days}
            onChange={(e) => setDays(e.target.value)}
          >
            {EXPIRY_OPTIONS.map((o) => (
              <option key={o.label} value={o.value}>{o.label}</option>
            ))}
          </Select>
        </div>
      </div>
    </Modal>
  );
}

export default function EventAccessLinks({ event, canManage }) {
  const eventId = event.id;
  const { data, loading, error, reload } = useApi(() =>
    api.get(`/events/${eventId}/access-links`).then((r) => r.data)
  );
  const links = data || [];

  const [createOpen, setCreateOpen] = useState(false);
  const [issued, setIssued] = useState(null);      // the one-time reveal payload
  const [confirm, setConfirm] = useState(null);    // { kind: "revoke"|"delete", link }

  const mutate = useMutation({ onDone: reload });

  const create = async (body) => {
    const res = await mutate.run(() => api.post(`/events/${eventId}/access-links`, body), {
      success: "Viewer link generated",
    });
    if (!res) return false;
    setIssued(res.data);
    setCreateOpen(false);
    return true;
  };

  const rotate = async (link) => {
    const res = await mutate.run(
      () => api.post(`/events/${eventId}/access-links/${link.id}/rotate`, { expires_in_days: 7 }),
      { success: "Link regenerated — the previous one no longer works" }
    );
    if (res) setIssued(res.data);
  };

  const runConfirm = async () => {
    const { kind, link } = confirm;
    const ok =
      kind === "revoke"
        ? await mutate.run(() => api.post(`/events/${eventId}/access-links/${link.id}/revoke`), {
            success: "Link revoked",
          })
        : await mutate.run(() => api.delete(`/events/${eventId}/access-links/${link.id}`), {
            success: "Link deleted",
          });
    if (ok) setConfirm(null);
  };

  const watchUrl = `${window.location.origin}/events/${eventId}/watch`;

  return (
    <div className="space-y-4">
      {/* What this event's visibility ALREADY allows, before any link is issued. Stated
          plainly so an admin does not generate links for a public event thinking they gate it. */}
      <SectionCard title="Who can watch" subtitle="Set by this event's visibility" icon={FiKey}>
        <dl className="space-y-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <dt className="text-slate-500 dark:text-slate-400">Visibility</dt>
            <dd className="flex items-center gap-2">
              <Badge tone="brand">{visLabel(event.visibility)}</Badge>
              <span className="text-xs text-slate-500 dark:text-slate-400">
                {VISIBILITY_HELP[event.visibility]}
              </span>
            </dd>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <dt className="text-slate-500 dark:text-slate-400">Passphrase</dt>
            <dd>
              {event.password_protected ? (
                <Badge tone="success">Required to start playback</Badge>
              ) : (
                <span className="text-slate-600 dark:text-slate-300">Not set</span>
              )}
            </dd>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <dt className="text-slate-500 dark:text-slate-400">Registration</dt>
            <dd className="text-right">
              <span className="text-slate-600 dark:text-slate-300">
                {event.registration_required ? "Required" : "Open"}
                {event.registration_limit ? ` · limit ${event.registration_limit}` : ""}
              </span>
              {event.registration_required && (
                // Never imply enforcement that does not exist: services/viewer.py returns
                // registration_enforced: false because there is no registrations table.
                <p className="mt-0.5 text-xs text-amber-600 dark:text-amber-400">
                  Stored as configuration — attendee registration is not yet collected, so this
                  does not block entry.
                </p>
              )}
            </dd>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <dt className="text-slate-500 dark:text-slate-400">Watch page</dt>
            <dd className="flex min-w-0 items-center gap-2">
              <code className="truncate font-mono text-xs text-slate-600 dark:text-slate-300">{watchUrl}</code>
              <Button
                variant="ghost"
                size="sm"
                iconOnly
                leftIcon={FiExternalLink}
                href={`/events/${eventId}/watch`}
                aria-label="Open the attendee watch page"
              />
            </dd>
          </div>
        </dl>
      </SectionCard>

      <SectionCard
        title="Viewer access links"
        subtitle="Expiring, revocable links that admit someone outside your organization"
        icon={FiLink}
        padding="none"
        action={
          canManage && (
            <Button size="sm" leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>
              Generate link
            </Button>
          )
        }
      >
        {error ? (
          <div className="p-4">
            <ErrorState error={error} onRetry={reload} title="Couldn't load access links" />
          </div>
        ) : loading ? (
          <div className="space-y-2 p-4" aria-hidden="true">
            {[0, 1].map((i) => <Skeleton key={i} className="h-14 w-full rounded-lg" />)}
          </div>
        ) : links.length === 0 ? (
          <EmptyState
            icon={FiLink}
            title="No access links"
            description={
              event.visibility === "public" || event.visibility === "unlisted"
                ? "This event is already open to any signed-in viewer, so links are optional — generate one only if you want a revocable link you can track."
                : "Generate a link to let someone outside your organization watch this event."
            }
            action={
              canManage && (
                <Button size="sm" leftIcon={FiPlus} onClick={() => setCreateOpen(true)}>
                  Generate link
                </Button>
              )
            }
          />
        ) : (
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {links.map((link) => {
              const st = linkState(link);
              return (
                <li key={link.id} className="flex flex-wrap items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                        {link.label || "Untitled link"}
                      </p>
                      <Badge tone={st.tone} size="sm">{st.label}</Badge>
                    </div>
                    <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
                      {link.expires_at ? `Expires ${fmtDateTime(link.expires_at)}` : "No expiry"}
                      {" · "}
                      {link.uses > 0
                        ? `used ${link.uses} ${link.uses === 1 ? "time" : "times"}${link.last_used_at ? `, last ${fmtDateTime(link.last_used_at)}` : ""}`
                        : "not used yet"}
                    </p>
                  </div>
                  {canManage && (
                    <div className="flex items-center gap-1">
                      <Button
                        variant="ghost"
                        size="sm"
                        leftIcon={FiRefreshCw}
                        disabled={mutate.busy}
                        aria-label={`Regenerate the ${link.label || "untitled"} link`}
                        onClick={() => rotate(link)}
                      >
                        Regenerate
                      </Button>
                      {!link.revoked_at && (
                        <Button
                          variant="ghost"
                          size="sm"
                          iconOnly
                          leftIcon={FiSlash}
                          disabled={mutate.busy}
                          aria-label={`Revoke the ${link.label || "untitled"} link`}
                          onClick={() => setConfirm({ kind: "revoke", link })}
                        />
                      )}
                      <Button
                        variant="ghost"
                        size="sm"
                        iconOnly
                        leftIcon={FiTrash2}
                        disabled={mutate.busy}
                        aria-label={`Delete the ${link.label || "untitled"} link`}
                        className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
                        onClick={() => setConfirm({ kind: "delete", link })}
                      />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </SectionCard>

      <CreateDialog
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreate={create}
        busy={mutate.busy}
      />
      <RevealDialog issued={issued} onClose={() => setIssued(null)} />
      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={runConfirm}
        busy={mutate.busy}
        title={confirm?.kind === "revoke" ? "Revoke this link?" : "Delete this link?"}
        confirmLabel={confirm?.kind === "revoke" ? "Revoke" : "Delete"}
        body={
          confirm?.kind === "revoke" ? (
            <>
              Anyone using <strong className="font-semibold text-slate-800 dark:text-slate-100">
                {confirm?.link?.label || "this link"}
              </strong>{" "}
              loses access immediately. The row stays so the revocation remains auditable.
            </>
          ) : (
            <>
              <strong className="font-semibold text-slate-800 dark:text-slate-100">
                {confirm?.link?.label || "This link"}
              </strong>{" "}
              and its usage history are removed permanently. Revoke instead if you need the
              audit trail.
            </>
          )
        }
      />
    </div>
  );
}
