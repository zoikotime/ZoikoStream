import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FiMail, FiSearch, FiUserCheck, FiUserPlus, FiUserX, FiUsers } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import useMutation from "../../hooks/useMutation";
import InviteModal from "../../pages/organization/InviteModal";
import { expiryHint } from "../../data/invitations";
import SectionCard from "../admin/SectionCard";
import EmptyState from "./OrganizationEmptyState";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import ConfirmDialog from "../../ui/ConfirmDialog";
import { Input } from "../../ui/forms";
import { TEAM_ROLES, roleMeta } from "../../data/events";

// Event-team assignment for all six roles.
//
// Assignment is per-EVENT and it is what actually grants control: an org's host role does
// not make someone host of every event in it (server/app/services/moderation.py resolve_ctx),
// so without this panel an invited host has no authority over the broadcast.
//
// Three operations, three endpoints, all org-admin gated server-side:
//   add      POST   /events/{id}/team/{role}          { user_id }   idempotent
//   remove   DELETE /events/{id}/team/{role}/{userId}
//   replace  PATCH  /events/{id}/team/{role}          { user_ids }  whole set
// Each returns the FULL team, so one round trip repaints every role — no refetch, and the
// six roles can never disagree about who is assigned.

const initials = (name) =>
  (name || "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();

function MemberChip({ member, roleLabel, onRemove, busy }) {
  return (
    <li className="flex items-center gap-3 rounded-xl border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900/50">
      <span
        className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-violet-100 text-xs font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300"
        aria-hidden="true"
      >
        {initials(member.full_name || member.email)}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-slate-800 dark:text-slate-100">
          {member.full_name || member.email}
        </p>
        <p className="truncate text-xs text-slate-500 dark:text-slate-400">{member.email}</p>
      </div>
      {onRemove && (
        <Button
          variant="ghost"
          size="sm"
          iconOnly
          leftIcon={FiUserX}
          disabled={busy}
          // Named, not "Remove" — a screen reader user tabbing a grid of these needs to
          // know which person and which role each button acts on.
          aria-label={`Remove ${member.full_name || member.email} as ${roleLabel}`}
          className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10"
          onClick={() => onRemove(member)}
        />
      )}
    </li>
  );
}

/** The member picker for one role. Filters out people already assigned to that role, so
 *  "duplicate prevention" is visible in the UI and not only a 409 the server would swallow. */
function AddMember({ role, members, assigned, onAdd, busy }) {
  const [query, setQuery] = useState("");
  const assignedIds = useMemo(() => new Set(assigned.map((m) => m.id)), [assigned]);

  const candidates = useMemo(() => {
    const q = query.trim().toLowerCase();
    return members
      .filter((m) => !assignedIds.has(m.id))
      .filter((m) => !q || `${m.full_name || ""} ${m.email}`.toLowerCase().includes(q))
      .slice(0, 50); // the org list is already page_size-capped; this bounds the DOM
  }, [members, assignedIds, query]);

  return (
    <div className="rounded-xl border border-slate-200 p-3 dark:border-slate-800">
      <div className="relative">
        <FiSearch
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
          aria-hidden="true"
        />
        <Input
          variant="console"
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={`Search members to add as ${roleMeta(role).label.toLowerCase()}…`}
          aria-label={`Search organization members to assign as ${roleMeta(role).label}`}
          className="pl-9"
        />
      </div>

      {candidates.length === 0 ? (
        <p className="mt-3 text-sm text-slate-500 dark:text-slate-400">
          {members.length === 0
            ? "No organization members yet. Invite people under Members & Access first — only members of this organization can be assigned."
            : query
              ? "No members match that search."
              : "Every member is already assigned to this role."}
        </p>
      ) : (
        <ul className="mt-3 max-h-64 divide-y divide-slate-100 overflow-y-auto dark:divide-slate-800">
          {candidates.map((m) => (
            <li key={m.id} className="flex items-center gap-3 py-2">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                  {m.full_name || m.email}
                </span>
                <span className="block truncate text-xs text-slate-500 dark:text-slate-400">{m.email}</span>
              </span>
              <Badge tone="neutral" size="sm" className="shrink-0">{m.role}</Badge>
              <Button
                size="sm"
                variant="secondary"
                leftIcon={FiUserPlus}
                disabled={busy}
                aria-label={`Assign ${m.full_name || m.email} as ${roleMeta(role).label}`}
                onClick={() => onAdd(m)}
              >
                Assign
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function EventTeamPanel({ eventId, event, team, members, canManage, onTeamChange }) {
  const [openRole, setOpenRole] = useState(null);
  const [pendingRemove, setPendingRemove] = useState(null); // { role, member }
  const [inviteOpen, setInviteOpen] = useState(false);

  // Open invitations for THIS event — the other half of building a team. Assigning covers
  // people who are already members; inviting covers everyone else, which is the flow the
  // brief describes as "Open Event -> Invite Members -> Assign Role".
  const { data: invites, reload: reloadInvites } = useApi(() =>
    api
      .get("/organization/invitations", { params: { event_id: eventId, page_size: 100 } })
      .then((r) => r.data.items)
  );
  const openInvites = useMemo(
    () => (invites || []).filter((i) => i.status === "pending"),
    [invites]
  );

  // Every mutation returns the whole team, so the parent replaces its state from the
  // response rather than refetching (the optimistic-update requirement, done honestly:
  // the UI shows what the server confirmed, one round trip, no divergence risk).
  const mutate = useMutation({ onDone: (res) => onTeamChange?.(res?.data) });

  const add = (role, member) =>
    mutate.run(() => api.post(`/events/${eventId}/team/${role}`, { user_id: member.id }), {
      success: `${member.full_name || member.email} assigned as ${roleMeta(role).label.toLowerCase()}`,
    });

  const remove = async () => {
    const { role, member } = pendingRemove;
    const ok = await mutate.run(
      () => api.delete(`/events/${eventId}/team/${role}/${member.id}`),
      { success: `${member.full_name || member.email} removed as ${roleMeta(role).label.toLowerCase()}` }
    );
    if (ok) setPendingRemove(null);
  };

  const total = TEAM_ROLES.reduce((n, r) => n + (team?.[r.key]?.length || 0), 0);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          {total} {total === 1 ? "person" : "people"} assigned across {TEAM_ROLES.length} roles.
          {" "}Assignments are specific to this event.
        </p>
        {canManage ? (
          <Button size="sm" leftIcon={FiMail} onClick={() => setInviteOpen(true)}>
            Invite by email
          </Button>
        ) : (
          <Badge tone="neutral">Read-only — only organization admins can change the team</Badge>
        )}
      </div>

      {/* Invitations that haven't been answered yet. Shown ABOVE the roles because an admin
          building a team needs to know who has been asked but hasn't joined — otherwise they
          look like an empty role and get invited twice. */}
      {openInvites.length > 0 && (
        <SectionCard
          title="Awaiting acceptance"
          subtitle={`${openInvites.length} invitation${openInvites.length === 1 ? "" : "s"} sent for this event`}
          icon={FiMail}
        >
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {openInvites.map((inv) => (
              <li key={inv.id} className="flex flex-wrap items-center gap-3 py-2.5">
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium text-slate-800 dark:text-slate-100">
                    {inv.email}
                  </span>
                  <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                    {roleMeta(inv.event_role).label} · expires {expiryHint(inv.expires_at)}
                    {inv.send_error ? " · email failed" : inv.sent_at ? "" : " · not emailed"}
                  </span>
                </span>
                <Badge tone={inv.send_error ? "danger" : "warning"} size="sm" dot>
                  {inv.send_error ? "Delivery failed" : "Pending"}
                </Badge>
              </li>
            ))}
          </ul>
          <p className="mt-3 border-t border-slate-100 pt-3 text-xs text-slate-500 dark:border-slate-800 dark:text-slate-400">
            Manage these on the{" "}
            <Link
              to={`/organization/invitations?event=${eventId}`}
              className="font-semibold text-violet-600 hover:underline dark:text-violet-400"
            >
              invitations page
            </Link>
            .
          </p>
        </SectionCard>
      )}

      {TEAM_ROLES.map(({ key, label, plural, grants }) => {
        const assigned = team?.[key] || [];
        const isOpen = openRole === key;
        return (
          <SectionCard
            key={key}
            title={plural}
            subtitle={grants}
            icon={FiUsers}
            action={
              canManage ? (
                <Button
                  variant={isOpen ? "secondary" : "primary"}
                  size="sm"
                  leftIcon={FiUserCheck}
                  aria-expanded={isOpen}
                  onClick={() => setOpenRole(isOpen ? null : key)}
                >
                  {isOpen ? "Done" : `Manage ${plural.toLowerCase()}`}
                </Button>
              ) : (
                <Badge tone="neutral" size="sm">{assigned.length}</Badge>
              )
            }
          >
            <div className="space-y-3">
              {isOpen && canManage && (
                <AddMember
                  role={key}
                  members={members}
                  assigned={assigned}
                  onAdd={(m) => add(key, m)}
                  busy={mutate.busy}
                />
              )}

              {assigned.length === 0 ? (
                <EmptyState
                  icon={FiUsers}
                  title={`No ${plural.toLowerCase()} assigned`}
                  description={
                    key === "host"
                      ? "A host is required before this event can be broadcast."
                      : `Assign ${plural.toLowerCase()} from your organization's members.`
                  }
                />
              ) : (
                <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {assigned.map((m) => (
                    <MemberChip
                      key={m.id}
                      member={m}
                      roleLabel={label}
                      busy={mutate.busy}
                      onRemove={canManage ? (member) => setPendingRemove({ role: key, member }) : null}
                    />
                  ))}
                </ul>
              )}
            </div>
          </SectionCard>
        );
      })}

      <InviteModal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        onInvited={reloadInvites}
        event={event || { id: eventId }}
      />

      <ConfirmDialog
        open={!!pendingRemove}
        onClose={() => setPendingRemove(null)}
        onConfirm={remove}
        busy={mutate.busy}
        title="Remove from this event?"
        confirmLabel="Remove"
        body={
          pendingRemove && (
            <>
              <strong className="font-semibold text-slate-800 dark:text-slate-100">
                {pendingRemove.member.full_name || pendingRemove.member.email}
              </strong>{" "}
              will lose the {roleMeta(pendingRemove.role).label.toLowerCase()} role on this event.
              {["host", "moderator"].includes(pendingRemove.role) && (
                <p className="mt-2">
                  They will no longer be able to open the{" "}
                  {pendingRemove.role === "host" ? "broadcast" : "moderation"} console for it.
                </p>
              )}
              <p className="mt-2 text-slate-500 dark:text-slate-400">
                Their organization membership is unaffected.
              </p>
            </>
          )
        }
      />
    </div>
  );
}
