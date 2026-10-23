import { useEffect, useMemo, useRef, useState } from "react";
import { FiUserPlus, FiSearch, FiRefreshCw, FiX, FiTrash2, FiUsers, FiMail, FiSend, FiLock } from "react-icons/fi";
import api, { errMsg } from "../../api";
import useStepUp, { STEP_UP_HEADER, isStepUpRequired } from "../../components/auth/useStepUp";
import useApi from "../../hooks/useApi";
import { useAuth } from "../../auth/AuthContext";
import { isOrgAdmin } from "../../auth/orgRole";
import { notify } from "../../ui/Toast";
import Modal from "../../ui/Modal";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import StatCard from "../../components/admin/StatCard";
import { ConsoleButton as Button } from "../../ui/Button";
import Badge from "../../ui/Badge";
import DataTable from "../../components/admin/DataTable";
import SectionCard from "../../components/admin/SectionCard";
import { cx, focusRing } from "../../ui/tokens";
import { Label } from "../../ui/forms";
import { fmtDate } from "../../data/events";
import UserAvatar from "../../ui/UserAvatar";

// Mirrors the backend's ORG_ASSIGNABLE_ROLES (routers/organization.py) — "moderator" was
// removed from both together, so the form cannot offer a role the API would reject.
const ROLES = ["org_admin", "host", "speaker", "viewer"];
const ROLE_LABEL = { org_admin: "Admin", host: "Host", speaker: "Speaker", viewer: "Viewer" };
const roleLabel = (r) => ROLE_LABEL[r] || r;

const INVITE_TONE = { pending: "warning", accepted: "success", cancelled: "neutral", expired: "danger", rejected: "danger" };

const control = cx(
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 outline-none",
  "focus:border-violet-400 focus:ring-2 focus:ring-violet-500/20",
  "dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200",
  focusRing
);
const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);

// Stable toast ids, so a refusal raised again (a second attempt, the same failure) replaces the
// toast already on screen instead of stacking copies. One per member, so different members'
// outcomes stay distinguishable; self-demotion gets its own, since it is always the same refusal.
const roleToastId = (memberId) => `member-role:${memberId}`;
const SELF_CHANGE_TOAST_ID = "member-role:self-change-forbidden";

// The structured refusal code ({detail: {code}} from /organization/users, or a top-level code).
// Read here rather than via api.js's errCode: suites that render this page mock ../../api with
// a strict factory, and a new named import from it would throw there.
const refusalCode = (e) => {
  const data = e?.response?.data;
  const detail = data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.code) return detail.code;
  return (data && typeof data === "object" && data.code) || null;
};

function InviteModal({ open, onClose, onInvited }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("viewer");
  const [sending, setSending] = useState(false);

  const close = () => { setEmail(""); setRole("viewer"); onClose(); };

  const send = async () => {
    setSending(true);
    try {
      await api.post("/organization/invitations", { email: email.trim().toLowerCase(), role });
      notify.success(`Invitation sent to ${email.trim()}`);
      onInvited?.();
      close();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setSending(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={close}
      title="Invite a member"
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={sending}>Cancel</Button>
          <Button size="sm" leftIcon={FiSend} onClick={send} loading={sending} disabled={!isEmail(email) || sending}>
            Send Invitation
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <div>
          <Label>Email address</Label>
          <div className="relative">
            <FiMail className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="teammate@company.com"
              className={cx(control, "h-10 w-full pl-9")}
            />
          </div>
        </div>
        <div>
          <Label>Role</Label>
          <select value={role} onChange={(e) => setRole(e.target.value)} className={cx(control, "h-10 w-full")}>
            {ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </select>
        </div>
        <p className="text-xs text-slate-500 dark:text-slate-400">
          They'll receive an email with a secure link to join your organization.
        </p>
      </div>
    </Modal>
  );
}

export default function OrganizationMembers() {
  const { user } = useAuth();
  const [inviteOpen, setInviteOpen] = useState(false);
  const [query, setQuery] = useState("");

  // Both endpoints below are org_admin-only, and the sidebar deliberately shows Members to
  // every role — so a host arriving here used to fire two requests, collect two 403s and get
  // a page-wide red "org_admin access required". Deciding up front means no request is sent
  // that is already known to be refused, and the page can say something useful instead.
  //
  // This is presentation only. The gate that matters is require_org_admin on the server,
  // which is unchanged and still answers 403 to a direct call from a host or viewer.
  const canManageMembers = isOrgAdmin(user);

  const { data, loading, error, reload } = useApi(() =>
    !canManageMembers
      ? Promise.resolve(null)
      : Promise.all([
          api.get("/organization/users", { params: { page_size: 100 } }).then((r) => r.data.items),
          api.get("/organization/invitations", { params: { page_size: 100 } }).then((r) => r.data.items),
        ]).then(([members, invites]) => ({ members, invites }))
  );
  // Skeletons only for the first load. A refresh after a change keeps the rows on screen, so a
  // member's newly confirmed role does not blink out behind a skeleton and back.
  const firstLoad = loading && !data;
  const members = useMemo(() => data?.members || [], [data]);
  const invites = useMemo(() => data?.invites || [], [data]);
  const pending = invites.filter((i) => i.status === "pending");

  const filteredMembers = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return members;
    return members.filter((m) => (m.full_name || "").toLowerCase().includes(q) || (m.email || "").toLowerCase().includes(q));
  }, [members, query]);

  // ── Member mutations ──────────────────────────────────────────────────────────────────
  // One outstanding mutation PER MEMBER. The ref is the guard: it is read and written
  // synchronously, so a burst of change events inside one render cannot slip a second request
  // through. The state mirrors it only so the row can render disabled. A Set, so member B's
  // request finishing never unlocks member A's.
  const busyRef = useRef(new Set());
  const [busyIds, setBusyIds] = useState(() => new Set());
  const lockMember = (id) => {
    if (busyRef.current.has(id)) return false;
    busyRef.current.add(id);
    setBusyIds(new Set(busyRef.current));
    return true;
  };
  const unlockMember = (id) => {
    busyRef.current.delete(id);
    setBusyIds(new Set(busyRef.current));
  };

  // The role being requested while its PATCH is outstanding (shown, disabled, in the select).
  const [requestedRoles, setRequestedRoles] = useState({});
  // The role the server CONFIRMED, tied to the member list it was confirmed against. It bridges
  // the gap between a successful PATCH and the reload that follows (so the select does not
  // flicker back to the old role), and it stops applying as soon as fresh server data lands —
  // so it can never hide a later change made elsewhere.
  const [confirmedRoles, setConfirmedRoles] = useState({});
  const membersRef = useRef(members);
  useEffect(() => {
    membersRef.current = members;
  }, [members]);
  const confirmedRole = (m) => {
    const c = confirmedRoles[m.id];
    return c && c.list === members ? c.role : m.role;
  };
  const shownRole = (m) => requestedRoles[m.id] ?? confirmedRole(m);

  // Password re-confirmation for the server's high-risk grants (promotion to admin).
  const stepUp = useStepUp();

  const kpis = [
    { label: "Members", value: members.length },
    { label: "Active", value: members.filter((m) => m.is_active).length },
    { label: "Admins", value: members.filter((m) => confirmedRole(m) === "org_admin").length },
    { label: "Pending invites", value: pending.length },
  ];

  const changeRole = async (m, newRole) => {
    if (!m?.id || m.role === "super_admin") return;
    if (busyRef.current.has(m.id)) return;          // this member already has a change in flight
    if (newRole === confirmedRole(m)) return;       // already that role: nothing to send
    lockMember(m.id);
    setRequestedRoles((prev) => ({ ...prev, [m.id]: newRole }));

    const url = `/organization/users/${m.id}`;
    const body = { role: newRole };
    try {
      let res;
      try {
        res = await api.patch(url, body);
      } catch (e) {
        if (!isStepUpRequired(e)) throw e;
        // The server wants proof the acting admin is present before handing over admin
        // control. Ask once; cancelling sends nothing further and rolls the select back.
        const reference = await stepUp.request({
          purpose: e.response.data.detail.purpose || "high_risk_role_grant",
          title: "Confirm admin access",
          description:
            `Granting ${m.full_name || "this member"} ${roleLabel(newRole)} access gives them full control of ` +
            "this organization. Enter your password to confirm it's you.",
          confirmLabel: `Grant ${roleLabel(newRole)} access`,
        });
        if (!reference) return;
        try {
          res = await api.patch(url, body, { headers: { [STEP_UP_HEADER]: reference } });
        } catch (e2) {
          if (!isStepUpRequired(e2)) throw e2;
          // The confirmation was spent or expired between the dialog and the retry.
          notify.error(
            `Your password confirmation expired, so ${m.full_name || "the member"}'s role was not changed. Try again.`,
            { id: roleToastId(m.id) }
          );
          return;
        }
      }
      const role = res?.data?.role || newRole;
      setConfirmedRoles((prev) => ({ ...prev, [m.id]: { role, list: membersRef.current } }));
      notify.success(`${m.full_name} is now ${roleLabel(role)}`, { id: roleToastId(m.id) });
      reload();
    } catch (e) {
      const id = refusalCode(e) === "SELF_ROLE_CHANGE_FORBIDDEN" ? SELF_CHANGE_TOAST_ID : roleToastId(m.id);
      notify.error(errMsg(e), { id });
    } finally {
      // Success: the confirmed role (above) takes over. Failure or cancel: this falls back to
      // the last confirmed role — the rollback.
      setRequestedRoles((prev) => {
        const next = { ...prev };
        delete next[m.id];
        return next;
      });
      unlockMember(m.id);
    }
  };

  const removeMember = async (m) => {
    if (busyRef.current.has(m.id)) return;
    if (!window.confirm(`Remove ${m.full_name} from the organization?`)) return;
    if (!lockMember(m.id)) return;
    try {
      await api.delete(`/organization/users/${m.id}`);
      notify.success("Member removed");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      unlockMember(m.id);
    }
  };

  const resendInvite = async (inv) => {
    try {
      await api.patch(`/organization/invitations/${inv.id}`, { action: "resend" });
      notify.success(`Invitation resent to ${inv.email}`);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const cancelInvite = async (inv) => {
    try {
      await api.delete(`/organization/invitations/${inv.id}`);
      notify.success("Invitation cancelled");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const memberColumns = [
    {
      key: "full_name",
      header: "Member",
      sortable: true,
      sortValue: (r) => (r.full_name || "").toLowerCase(),
      render: (r) => (
        <div className="flex items-center gap-3">
          <UserAvatar
            name={r.full_name}
            src={r.avatar_url}
            alt=""
            className="h-9 w-9 bg-violet-100 text-xs font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300"
          />
          <div className="min-w-0">
            <p className="truncate font-medium text-slate-800 dark:text-slate-100">
              {r.full_name}
              {user?.email === r.email && <span className="ml-1.5 text-xs font-normal text-slate-400">(you)</span>}
            </p>
            <p className="truncate text-xs text-slate-500 dark:text-slate-400">{r.email}</p>
          </div>
        </div>
      ),
    },
    {
      key: "role",
      header: "Role",
      sortable: true,
      sortValue: (r) => roleLabel(shownRole(r)),
      render: (r) => {
        const isUpdating = busyIds.has(r.id);
        return (
          <select
            value={shownRole(r)}
            onChange={(e) => changeRole(r, e.target.value)}
            disabled={r.role === "super_admin" || isUpdating}
            aria-busy={isUpdating || undefined}
            aria-label={`Role for ${r.full_name}`}
            className={cx(control, "h-8 w-full max-w-[9rem] disabled:opacity-60")}
          >
            {r.role === "super_admin" && <option value="super_admin">Super Admin</option>}
            {ROLES.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
          </select>
        );
      },
    },
    {
      key: "is_active",
      header: "Status",
      sortable: true,
      sortValue: (r) => (r.is_active ? 1 : 0),
      render: (r) => <Badge tone={r.is_active ? "success" : "neutral"} dot>{r.is_active ? "Active" : "Inactive"}</Badge>,
    },
    { key: "created_at", header: "Joined", align: "right", sortable: true, sortValue: (r) => (r.created_at ? new Date(r.created_at).getTime() : 0), render: (r) => fmtDate(r.created_at) },
  ];

  const inviteColumns = [
    { key: "email", header: "Email", sortable: true, render: (r) => <span className="font-medium text-slate-800 dark:text-slate-100">{r.email}</span> },
    { key: "role", header: "Role", render: (r) => <Badge tone="brand">{roleLabel(r.role)}</Badge> },
    { key: "status", header: "Status", sortable: true, render: (r) => <Badge tone={INVITE_TONE[r.status] || "neutral"} dot={r.status === "pending"}>{r.status[0].toUpperCase() + r.status.slice(1)}</Badge> },
    { key: "invited_by", header: "Invited by", render: (r) => r.invited_by || "—" },
    { key: "expires_at", header: "Expires", align: "right", sortable: true, sortValue: (r) => (r.expires_at ? new Date(r.expires_at).getTime() : 0), render: (r) => fmtDate(r.expires_at) },
  ];

  return (
    <div className="space-y-6">
      <OrganizationPageHeader
        breadcrumbs={[["Organization", "/organization/manage"], ["Team & roles"]]}
        eyebrow="Organization console / Team & roles"
        title="Members"
        subtitle={
          canManageMembers
            ? "Manage who has access to your organization and their roles"
            : "Who has access to your organization and their roles"
        }
        actions={
          canManageMembers ? (
            <Button size="sm" leftIcon={FiUserPlus} onClick={() => setInviteOpen(true)}>Invite member</Button>
          ) : null
        }
      />

      {!canManageMembers ? (
        /* Deliberately NOT an empty state. "No members found" would be a claim about the
           organization — and a false one, since the organization plainly has members; this
           reader simply may not see them. Nothing is counted, listed or zeroed here: the KPI
           tiles, the member table and the invitation table are all absent rather than shown
           holding zeros, because a fabricated zero is worse than an honest refusal. */
        <SectionCard title="Members &amp; access" icon={FiLock}>
          <div className="max-w-xl space-y-2 py-2">
            <p className="text-sm font-medium text-slate-800 dark:text-slate-100">
              Organization admin access is required to manage members.
            </p>
            <p className="text-[13px] leading-relaxed text-slate-500 dark:text-slate-400">
              Viewing the member list, sending invitations and changing roles are all
              restricted to organization admins. Contact an organization admin if you need
              access.
            </p>
          </div>
        </SectionCard>
      ) : error ? (
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load members" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            {kpis.map((k) => <StatCard key={k.label} label={k.label} value={k.value} loading={firstLoad} />)}
          </div>

          <SectionCard
            title="Team members"
            subtitle={`${members.length} in your organization`}
            icon={FiUsers}
            padding="none"
            action={
              <div className="relative">
                <FiSearch className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Search members…"
                  className={cx(control, "w-full pl-8 sm:w-48")}
                />
              </div>
            }
          >
            <DataTable
              columns={memberColumns}
              rows={filteredMembers}
              rowKey={(r) => r.id}
              loading={firstLoad}
              pageSize={10}
              initialSort={{ key: "full_name", dir: "asc" }}
              minWidth={720}
              empty={{ icon: FiUsers, title: query ? "No members match your search" : "No members yet", description: query ? "Try a different search." : "Invite your first teammate to get started." }}
              rowActions={(r) =>
                user?.email !== r.email && r.role !== "super_admin" ? (
                  <Button variant="ghost" size="sm" iconOnly leftIcon={FiTrash2} aria-label={`Remove ${r.full_name}`} disabled={busyIds.has(r.id)} className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10" onClick={() => removeMember(r)} />
                ) : null
              }
            />
          </SectionCard>

          <SectionCard title="Pending invitations" subtitle={`${pending.length} awaiting acceptance`} icon={FiMail} padding="none">
            <DataTable
              columns={inviteColumns}
              rows={invites}
              rowKey={(r) => r.id}
              loading={firstLoad}
              pageSize={10}
              initialSort={{ key: "expires_at", dir: "desc" }}
              minWidth={720}
              empty={{ icon: FiMail, title: "No invitations", description: "Invite members to see their invitations here.", action: <Button size="sm" leftIcon={FiUserPlus} onClick={() => setInviteOpen(true)}>Invite member</Button> }}
              rowActions={(r) => (
                <>
                  {r.status === "pending" && (
                    <Button variant="ghost" size="sm" iconOnly leftIcon={FiRefreshCw} aria-label={`Resend to ${r.email}`} onClick={() => resendInvite(r)} />
                  )}
                  {r.status !== "accepted" && (
                    <Button variant="ghost" size="sm" iconOnly leftIcon={FiX} aria-label={`Cancel invitation to ${r.email}`} className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10" onClick={() => cancelInvite(r)} />
                  )}
                </>
              )}
            />
          </SectionCard>
        </>
      )}

      <InviteModal open={inviteOpen} onClose={() => setInviteOpen(false)} onInvited={reload} />
      {stepUp.dialog}
    </div>
  );
}
