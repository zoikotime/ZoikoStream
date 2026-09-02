import { useMemo, useState } from "react";
import { FiUserPlus, FiSearch, FiRefreshCw, FiX, FiTrash2, FiUsers, FiMail, FiSend } from "react-icons/fi";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import { useAuth } from "../../auth/AuthContext";
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

  const { data, loading, error, reload } = useApi(() =>
    Promise.all([
      api.get("/organization/users", { params: { page_size: 100 } }).then((r) => r.data.items),
      api.get("/organization/invitations", { params: { page_size: 100 } }).then((r) => r.data.items),
    ]).then(([members, invites]) => ({ members, invites }))
  );
  const members = useMemo(() => data?.members || [], [data]);
  const invites = useMemo(() => data?.invites || [], [data]);
  const pending = invites.filter((i) => i.status === "pending");

  const filteredMembers = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return members;
    return members.filter((m) => (m.full_name || "").toLowerCase().includes(q) || (m.email || "").toLowerCase().includes(q));
  }, [members, query]);

  const kpis = [
    { label: "Members", value: members.length },
    { label: "Active", value: members.filter((m) => m.is_active).length },
    { label: "Admins", value: members.filter((m) => m.role === "org_admin").length },
    { label: "Pending invites", value: pending.length },
  ];

  const changeRole = async (m, role) => {
    try {
      await api.patch(`/organization/users/${m.id}`, { role });
      notify.success(`${m.full_name} is now ${roleLabel(role)}`);
      reload();
    } catch (e) {
      notify.error(errMsg(e));
    }
  };

  const removeMember = async (m) => {
    if (!window.confirm(`Remove ${m.full_name} from the organization?`)) return;
    try {
      await api.delete(`/organization/users/${m.id}`);
      notify.success("Member removed");
      reload();
    } catch (e) {
      notify.error(errMsg(e));
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
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-violet-100 text-xs font-semibold text-violet-700 dark:bg-violet-500/15 dark:text-violet-300">
            {(r.full_name || "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase()}
          </span>
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
      render: (r) => (
        <select
          value={r.role}
          onChange={(e) => changeRole(r, e.target.value)}
          disabled={r.role === "super_admin"}
          aria-label={`Role for ${r.full_name}`}
          className={cx(control, "h-8 w-full max-w-[9rem] disabled:opacity-60")}
        >
          {r.role === "super_admin" && <option value="super_admin">Super Admin</option>}
          {ROLES.map((role) => <option key={role} value={role}>{roleLabel(role)}</option>)}
        </select>
      ),
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
        title="Members"
        subtitle="Manage who has access to your organization and their roles"
        actions={<Button size="sm" leftIcon={FiUserPlus} onClick={() => setInviteOpen(true)}>Invite member</Button>}
      />

      {error ? (
        <OrganizationErrorState error={error} onRetry={reload} title="Couldn't load members" />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 xl:grid-cols-4">
            {kpis.map((k) => <StatCard key={k.label} label={k.label} value={k.value} loading={loading} />)}
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
              loading={loading}
              pageSize={10}
              initialSort={{ key: "full_name", dir: "asc" }}
              minWidth={720}
              empty={{ icon: FiUsers, title: query ? "No members match your search" : "No members yet", description: query ? "Try a different search." : "Invite your first teammate to get started." }}
              rowActions={(r) =>
                user?.email !== r.email && r.role !== "super_admin" ? (
                  <Button variant="ghost" size="sm" iconOnly leftIcon={FiTrash2} aria-label={`Remove ${r.full_name}`} className="text-rose-500 hover:bg-rose-50 hover:text-rose-600 dark:hover:bg-rose-500/10" onClick={() => removeMember(r)} />
                ) : null
              }
            />
          </SectionCard>

          <SectionCard title="Pending invitations" subtitle={`${pending.length} awaiting acceptance`} icon={FiMail} padding="none">
            <DataTable
              columns={inviteColumns}
              rows={invites}
              rowKey={(r) => r.id}
              loading={loading}
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
    </div>
  );
}
