import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  FiAlertTriangle, FiChevronDown, FiClock, FiMail, FiRefreshCw, FiSearch, FiSend,
  FiSlash, FiTrash2, FiUserPlus, FiX, FiEye,
} from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import { notify } from "../../ui/Toast";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import { InvitationStatusBadge, DeliveryBadge } from "../../components/organization/InvitationStatusBadge";
import StatCard from "../../components/admin/StatCard";
import SectionCard from "../../components/admin/SectionCard";
import DataTable from "../../components/admin/DataTable";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import Modal from "../../ui/Modal";
import ConfirmDialog from "../../ui/ConfirmDialog";
import { cx, focusRing } from "../../ui/tokens";
import { fmtDateTime } from "../../data/events";
import { PLATFORM_ROLES, EVENT_ROLES, STATUS_ORDER, expiryHint, roleLabel } from "../../data/invitations";
import InviteModal from "./InviteModal";

// Invitation & Access Management. Route: /organization/invitations
//
// Server-side search / filter / sort / paginate (GET /organization/invitations), matching the
// events console. Every action button is driven by the SERVER's can_resend / can_cancel /
// can_revoke booleans rather than the client re-implementing the state machine — so a button
// only appears when the API would actually accept it.

const PAGE_SIZE = 25;
const DEBOUNCE_MS = 300;

const control = cx(
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none",
  "focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20",
  "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  focusRing
);

const ALL_ROLES = [...PLATFORM_ROLES, ...EVENT_ROLES];

/** Read-only detail sheet. "View details" in the actions column — everything the row holds,
 *  including the delivery history an admin needs when someone says "I never got it". */
function DetailModal({ invitation, onClose }) {
  if (!invitation) return null;
  const rows = [
    ["Recipient", invitation.email],
    ["Status", invitation.status_label],
    ["Organization role", roleLabel(invitation.role)],
    ["Event", invitation.event_title || "—"],
    ["Event role", invitation.event_role ? roleLabel(invitation.event_role) : "—"],
    ["Invited by", invitation.invited_by || "—"],
    ["Invited by (email)", invitation.invited_by_email || "—"],
    ["Created", fmtDateTime(invitation.created_at)],
    ["Expires", `${fmtDateTime(invitation.expires_at)} · ${expiryHint(invitation.expires_at)}`],
    ["Email sent", invitation.sent_at ? fmtDateTime(invitation.sent_at) : "Not emailed"],
    ["Delivery confirmed", invitation.delivered_at ? fmtDateTime(invitation.delivered_at) : "—"],
    ["Send attempts", String(invitation.send_attempts ?? 0)],
    ["Resent", `${invitation.resend_count ?? 0} time${invitation.resend_count === 1 ? "" : "s"}`],
    ["Accepted", invitation.accepted_at ? fmtDateTime(invitation.accepted_at) : "—"],
    ["Declined", invitation.declined_at ? fmtDateTime(invitation.declined_at) : "—"],
    ["Revoked", invitation.revoked_at ? fmtDateTime(invitation.revoked_at) : "—"],
  ];
  return (
    <Modal open onClose={onClose} title="Invitation details" size="lg">
      {invitation.send_error && (
        <p className="mb-4 flex items-start gap-2 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-400">
          <FiAlertTriangle className="mt-0.5 shrink-0" aria-hidden="true" />
          <span>
            <strong className="font-semibold">Delivery failed:</strong> {invitation.send_error}
          </span>
        </p>
      )}
      {invitation.message && (
        <blockquote className="mb-4 border-l-2 border-violet-500 bg-slate-50 px-3 py-2 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
          {invitation.message}
        </blockquote>
      )}
      <dl className="divide-y divide-slate-100 text-sm dark:divide-slate-800">
        {rows.map(([k, v]) => (
          <div key={k} className="flex items-start justify-between gap-4 py-2.5">
            <dt className="shrink-0 text-slate-500 dark:text-slate-400">{k}</dt>
            <dd className="min-w-0 break-words text-right font-medium text-slate-800 dark:text-slate-100">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-4 text-xs text-slate-500 dark:text-slate-400">
        The invitation link itself cannot be shown again — only a hash of it is stored. Resend
        the invitation to issue a fresh link.
      </p>
    </Modal>
  );
}

export default function OrganizationInvitations() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [detail, setDetail] = useState(null);
  const [confirm, setConfirm] = useState(null);   // { kind, invitation?, ids?, clear? }

  const [search, setSearch] = useState("");
  const [debounced, setDebounced] = useState("");
  const [status, setStatus] = useState("all");
  const [role, setRole] = useState("all");
  const [sort, setSort] = useState({ key: "created_at", dir: "desc" });
  const [page, setPage] = useState(1);

  // Deep link from the event page: ?event=<id> scopes the list to one event.
  const eventFilter = searchParams.get("event") || "";

  useEffect(() => {
    const id = setTimeout(() => setDebounced(search.trim()), DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [search]);

  // Render-phase page reset (the pattern DataTable itself uses) so a stale page is never
  // requested — an effect would fire a request for page 7 and then another for page 1.
  const filterKey = [debounced, status, role, eventFilter, sort?.key, sort?.dir].join("|");
  const [prevKey, setPrevKey] = useState(filterKey);
  if (filterKey !== prevKey) {
    setPrevKey(filterKey);
    setPage(1);
  }

  const params = useMemo(
    () => ({
      page,
      page_size: PAGE_SIZE,
      sort_by: sort?.key || "created_at",
      order: sort?.dir || "desc",
      ...(debounced ? { q: debounced } : {}),
      ...(status !== "all" ? { status } : {}),
      ...(role !== "all" ? { role } : {}),
      ...(eventFilter ? { event_id: eventFilter } : {}),
    }),
    [page, sort, debounced, status, role, eventFilter]
  );

  const { data, loading, error, reload } = useApi(() =>
    api.get("/organization/invitations", { params }).then((r) => r.data)
  );
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) {
      mounted.current = true;
      return;
    }
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params]);

  const { data: stats, reload: reloadStats } = useApi(() =>
    api.get("/organization/invitations/stats").then((r) => r.data)
  );

  const rows = useMemo(() => data?.items || [], [data]);
  const total = data?.total ?? 0;

  const refresh = useCallback(() => {
    reload();
    reloadStats();
  }, [reload, reloadStats]);

  const mutate = useMutation({ onDone: refresh });

  // A resend returns a fresh single-use link. Surfacing it matters for the manual case (an
  // admin who needs to hand it over rather than rely on email).
  const resend = (inv) =>
    mutate.run(() => api.patch(`/organization/invitations/${inv.id}`, { action: "resend" }), {
      success: `Invitation resent to ${inv.email}`,
    });

  const runConfirm = async () => {
    const c = confirm;
    if (c.kind === "cancel" || c.kind === "revoke") {
      const ok = await mutate.run(
        () => api.patch(`/organization/invitations/${c.invitation.id}`, { action: c.kind }),
        { success: c.kind === "cancel" ? "Invitation cancelled" : "Access revoked" }
      );
      if (ok) setConfirm(null);
      return;
    }
    if (c.kind === "delete") {
      const ok = await mutate.run(() => api.delete(`/organization/invitations/${c.invitation.id}`), {
        success: "Invitation removed from the list",
      });
      if (ok) setConfirm(null);
      return;
    }
    if (c.kind === "delete-expired") {
      const res = await mutate.run(() => api.post("/organization/invitations/delete-expired"), {
        success: null,
      });
      if (!res) return;
      const n = res.data?.succeeded?.length || 0;
      notify.success(n ? `${n} expired invitation${n === 1 ? "" : "s"} hidden` : "Nothing to clean up");
      setConfirm(null);
      return;
    }
    // bulk resend / cancel / delete
    const res = await mutate.run(
      () => api.post("/organization/invitations/bulk-action", { action: c.kind.replace("bulk-", ""), ids: c.ids }),
      { success: null }
    );
    if (!res) return;
    const { succeeded = [], failed = [] } = res.data || {};
    if (succeeded.length) notify.success(`${succeeded.length} invitation${succeeded.length === 1 ? "" : "s"} updated`);
    if (failed.length) notify.error(`${failed.length} skipped — ${failed[0].reason}`);
    c.clear?.();
    setConfirm(null);
  };

  const kpis = [
    { label: "Pending", value: stats?.pending ?? 0 },
    { label: "Accepted", value: stats?.accepted ?? 0 },
    { label: "Declined", value: stats?.rejected ?? 0 },
    { label: "Needs attention", value: (stats?.expired ?? 0) + (stats?.failed_delivery ?? 0) },
  ];

  const filtersActive = !!debounced || status !== "all" || role !== "all" || !!eventFilter;
  const clearFilters = () => {
    setSearch("");
    setStatus("all");
    setRole("all");
    if (eventFilter) {
      searchParams.delete("event");
      setSearchParams(searchParams, { replace: true });
    }
  };

  const columns = useMemo(
    () => [
      {
        key: "email",
        header: "Recipient",
        sortable: true,
        width: 240,
        render: (r) => (
          <div className="min-w-0">
            <p className="truncate font-medium text-slate-800 dark:text-slate-100">{r.email}</p>
            <p className="truncate text-xs text-slate-400">
              {r.invited_by ? `invited by ${r.invited_by}` : "—"}
            </p>
          </div>
        ),
      },
      {
        key: "event_title",
        header: "Event",
        render: (r) =>
          r.event_id ? (
            <Link
              to={`/organization/events/${r.event_id}`}
              className={cx("truncate text-violet-600 hover:underline dark:text-violet-400", focusRing)}
            >
              {r.event_title || "Untitled event"}
            </Link>
          ) : (
            <span className="text-slate-400">Organization</span>
          ),
      },
      {
        key: "role",
        header: "Role",
        render: (r) => (
          <div className="flex flex-wrap items-center gap-1">
            {r.event_role && <Badge tone="brand" size="sm">{roleLabel(r.event_role)}</Badge>}
            <Badge tone="neutral" size="sm">{roleLabel(r.role)}</Badge>
          </div>
        ),
      },
      { key: "status", header: "Status", sortable: true, render: (r) => <InvitationStatusBadge invitation={r} /> },
      {
        key: "delivery",
        header: "Delivery",
        render: (r) => (
          <span className="inline-flex items-center gap-1.5">
            <DeliveryBadge invitation={r} />
            {r.send_error && (
              <FiAlertTriangle
                className="text-rose-500"
                aria-label={`Delivery failed: ${r.send_error}`}
                title={r.send_error}
              />
            )}
          </span>
        ),
      },
      { key: "sent_at", header: "Sent", sortable: true, render: (r) => (r.sent_at ? fmtDateTime(r.sent_at) : "—") },
      {
        key: "expires_at",
        header: "Expires",
        sortable: true,
        render: (r) => (
          <span className={r.status === "pending" ? "" : "text-slate-400"}>
            {expiryHint(r.expires_at)}
          </span>
        ),
      },
      {
        key: "accepted_at",
        header: "Accepted",
        sortable: true,
        render: (r) => (r.accepted_at ? fmtDateTime(r.accepted_at) : "—"),
      },
    ],
    []
  );

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        title="Invitations"
        subtitle="Invite hosts, moderators, speakers and staff — and track every invitation"
        actions={
          <>
            <Button variant="secondary" size="sm" leftIcon={FiClock} onClick={() => setConfirm({ kind: "delete-expired" })}>
              Clean up expired
            </Button>
            <Button size="sm" leftIcon={FiUserPlus} onClick={() => setInviteOpen(true)}>
              Invite people
            </Button>
          </>
        }
      />

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-0 flex-1 sm:max-w-xs">
          <FiSearch className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by email…"
            aria-label="Search invitations by email address"
            className={cx(control, "w-full pl-8")}
          />
        </div>
        <div className="relative">
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            aria-label="Filter by status"
            className={cx(control, "appearance-none pr-8")}
          >
            <option value="all">All statuses</option>
            {STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {s === "rejected" ? "Declined" : s[0].toUpperCase() + s.slice(1)}
              </option>
            ))}
          </select>
          <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
        </div>
        <div className="relative">
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            aria-label="Filter by role"
            className={cx(control, "appearance-none pr-8")}
          >
            <option value="all">All roles</option>
            {ALL_ROLES.map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
          </select>
          <FiChevronDown className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
        </div>
        {filtersActive && (
          <Button variant="ghost" size="sm" leftIcon={FiX} onClick={clearFilters}>Clear filters</Button>
        )}
      </div>

      {error ? (
        <OrganizationErrorState error={error} onRetry={refresh} title="Couldn't load invitations" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            {kpis.map((k) => <StatCard key={k.label} label={k.label} value={k.value} loading={loading} />)}
          </div>

          <SectionCard
            title="All invitations"
            subtitle={eventFilter ? "Filtered to one event" : `${total} in your organization`}
            icon={FiMail}
            padding="none"
          >
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(r) => r.id}
              loading={loading}
              minWidth={1180}
              pageSize={PAGE_SIZE}
              total={total}
              page={page}
              onPageChange={setPage}
              sort={sort}
              onSortChange={setSort}
              selectable
              bulkActions={({ selected, clear }) => (
                <div className="flex flex-wrap items-center gap-1.5">
                  <Button
                    variant="secondary"
                    size="sm"
                    leftIcon={FiRefreshCw}
                    disabled={mutate.busy}
                    onClick={() => setConfirm({ kind: "bulk-resend", ids: selected, clear })}
                  >
                    Resend
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    leftIcon={FiSlash}
                    disabled={mutate.busy}
                    onClick={() => setConfirm({ kind: "bulk-cancel", ids: selected, clear })}
                  >
                    Cancel
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    leftIcon={FiTrash2}
                    disabled={mutate.busy}
                    className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
                    onClick={() => setConfirm({ kind: "bulk-delete", ids: selected, clear })}
                  >
                    Remove
                  </Button>
                </div>
              )}
              empty={{
                icon: FiMail,
                title: filtersActive ? "No invitations match your filters" : "No invitations yet",
                description: filtersActive
                  ? "Try clearing the search or filters."
                  : "Invite a host, moderator or speaker to get started.",
                action: filtersActive ? (
                  <Button size="sm" variant="secondary" leftIcon={FiX} onClick={clearFilters}>Clear filters</Button>
                ) : (
                  <Button size="sm" leftIcon={FiUserPlus} onClick={() => setInviteOpen(true)}>Invite people</Button>
                ),
              }}
              rowActions={(r) => (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    leftIcon={FiEye}
                    aria-label={`View details for ${r.email}`}
                    onClick={() => setDetail(r)}
                  />
                  {/* Every button below is gated on a SERVER boolean, so the console never
                      offers an action the API would refuse. */}
                  {r.can_resend && (
                    <Button
                      variant="ghost"
                      size="sm"
                      iconOnly
                      leftIcon={FiSend}
                      disabled={mutate.busy}
                      aria-label={`Resend invitation to ${r.email}`}
                      onClick={() => resend(r)}
                    />
                  )}
                  {r.can_cancel && (
                    <Button
                      variant="ghost"
                      size="sm"
                      iconOnly
                      leftIcon={FiSlash}
                      disabled={mutate.busy}
                      aria-label={`Cancel invitation to ${r.email}`}
                      onClick={() => setConfirm({ kind: "cancel", invitation: r })}
                    />
                  )}
                  {r.can_revoke && (
                    <Button
                      variant="ghost"
                      size="sm"
                      iconOnly
                      leftIcon={FiSlash}
                      disabled={mutate.busy}
                      className="text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-500/10"
                      aria-label={`Revoke ${r.email}'s access`}
                      onClick={() => setConfirm({ kind: "revoke", invitation: r })}
                    />
                  )}
                  <Button
                    variant="ghost"
                    size="sm"
                    iconOnly
                    leftIcon={FiTrash2}
                    disabled={mutate.busy}
                    className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
                    aria-label={`Remove the invitation for ${r.email} from this list`}
                    onClick={() => setConfirm({ kind: "delete", invitation: r })}
                  />
                </>
              )}
            />
          </SectionCard>
        </>
      )}

      <InviteModal open={inviteOpen} onClose={() => setInviteOpen(false)} onInvited={refresh} />
      <DetailModal invitation={detail} onClose={() => setDetail(null)} />

      <ConfirmDialog
        open={!!confirm}
        onClose={() => setConfirm(null)}
        onConfirm={runConfirm}
        busy={mutate.busy}
        tone={confirm?.kind === "revoke" || confirm?.kind?.includes("delete") ? "danger" : "primary"}
        title={
          {
            cancel: "Cancel this invitation?",
            revoke: "Revoke this person's access?",
            delete: "Remove this invitation?",
            "delete-expired": "Clean up expired invitations?",
            "bulk-resend": `Resend ${confirm?.ids?.length || 0} invitation${confirm?.ids?.length === 1 ? "" : "s"}?`,
            "bulk-cancel": `Cancel ${confirm?.ids?.length || 0} invitation${confirm?.ids?.length === 1 ? "" : "s"}?`,
            "bulk-delete": `Remove ${confirm?.ids?.length || 0} invitation${confirm?.ids?.length === 1 ? "" : "s"}?`,
          }[confirm?.kind] || "Are you sure?"
        }
        confirmLabel={
          {
            cancel: "Cancel invitation",
            revoke: "Revoke access",
            delete: "Remove",
            "delete-expired": "Clean up",
            "bulk-resend": "Resend",
            "bulk-cancel": "Cancel them",
            "bulk-delete": "Remove",
          }[confirm?.kind] || "Confirm"
        }
        body={
          {
            cancel: (
              <>
                The link sent to{" "}
                <strong className="font-semibold text-slate-800 dark:text-slate-100">
                  {confirm?.invitation?.email}
                </strong>{" "}
                stops working immediately. You can resend it later to reopen the invitation.
              </>
            ),
            revoke: (
              <>
                <strong className="font-semibold text-slate-800 dark:text-slate-100">
                  {confirm?.invitation?.email}
                </strong>{" "}
                loses the {roleLabel(confirm?.invitation?.event_role)} role on{" "}
                {confirm?.invitation?.event_title || "this event"}, and we'll email them to say so.
                <p className="mt-2 text-slate-500 dark:text-slate-400">
                  Their account and organization membership are unaffected — remove those from
                  Members &amp; Access.
                </p>
              </>
            ),
            delete: (
              <>
                This hides the invitation from the list. The record is retained for your audit
                trail, and the same person can be invited again.
              </>
            ),
            "delete-expired": (
              <>
                Every expired invitation is hidden from this list. Nothing is destroyed — the
                records are kept for your audit trail and anyone can be invited again.
              </>
            ),
            "bulk-resend": (
              <>
                Each one gets a NEW single-use link, and the previous link stops working.
                Invitations that can't be resent — already accepted, declined or revoked — are
                skipped and reported.
              </>
            ),
            "bulk-cancel": <>Their links stop working immediately. Anything already accepted is skipped.</>,
            "bulk-delete": <>These are hidden from the list. Nothing is destroyed.</>,
          }[confirm?.kind] || null
        }
      />
    </div>
  );
}
