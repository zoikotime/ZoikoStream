import { useMemo, useState } from "react";
import toast from "react-hot-toast";
import { FiCheckCircle, FiEdit2, FiSlash, FiTrash2, FiUsers, FiSearch } from "react-icons/fi";
import { Badge, Button, DataTable, Panel, StatCard, initials, timeAgo } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import UserModal from "./UserModal";
import { ROLES, roleLabel } from "./roleInfo";

const ROLE_TONE = { super_admin: "brand", org_admin: "info", host: "info", moderator: "warning", speaker: "neutral", viewer: "neutral" };

const inputCls =
  "h-9 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm text-slate-800 placeholder:text-slate-400 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";
const selectCls =
  "h-9 rounded-lg border border-slate-200 bg-white px-3 text-sm text-slate-700 focus-visible:border-violet-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-violet-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

function useUsersData() {
  return useApi(() => api.get("/admin/users", { params: { page_size: 100 } }).then((r) => r.data.items));
}

// Every platform user, real GET/PATCH/DELETE against /admin/users. Creation isn't offered
// here — accounts come from org signup/invitations, not a super-admin form.
export default function Users() {
  const { data: users, loading, error, reload } = useUsersData();
  const [q, setQ] = useState("");
  const [role, setRole] = useState("all");
  const [org, setOrg] = useState("all");
  const [active, setActive] = useState("all");
  const [modalOpen, setModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState(null);

  const rows = users || [];

  const orgs = useMemo(
    () => [...new Set(rows.map((u) => u.organization_name).filter(Boolean))].sort(),
    [rows]
  );

  const kpis = useMemo(
    () => ({
      total: rows.length,
      active: rows.filter((u) => u.is_active).length,
      inactive: rows.filter((u) => !u.is_active).length,
      superAdmins: rows.filter((u) => u.role === "super_admin").length,
    }),
    [rows]
  );

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return rows.filter(
      (u) =>
        (!query ||
          u.full_name.toLowerCase().includes(query) ||
          u.email.toLowerCase().includes(query) ||
          u.username.toLowerCase().includes(query)) &&
        (role === "all" || u.role === role) &&
        (org === "all" || u.organization_name === org) &&
        (active === "all" || (active === "active" ? u.is_active : !u.is_active))
    );
  }, [rows, q, role, org, active]);

  const openEdit = (u) => { setEditingUser(u); setModalOpen(true); };

  const toggleActive = async (u) => {
    try {
      await api.patch(`/admin/users/${u.id}`, { is_active: !u.is_active });
      toast.success(`${u.full_name} ${u.is_active ? "deactivated" : "activated"}`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (u) => {
    if (!window.confirm(`Delete ${u.full_name}? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/users/${u.id}`);
      toast.success(`${u.full_name} deleted`);
      reload();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const columns = [
    {
      key: "full_name",
      header: "User",
      sortable: true,
      render: (u) => (
        <div className="flex items-center gap-3">
          <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            {initials(u.full_name)}
          </span>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-slate-800 dark:text-slate-100">{u.full_name}</span>
              {!u.is_active && <Badge tone="danger" dot>Inactive</Badge>}
            </div>
            <span className="text-xs text-slate-400">{u.email}</span>
          </div>
        </div>
      ),
    },
    { key: "organization_name", header: "Organization", sortable: true, render: (u) => u.organization_name || "—" },
    { key: "role", header: "Role", sortable: true, render: (u) => <Badge tone={ROLE_TONE[u.role] || "neutral"}>{roleLabel(u.role)}</Badge> },
    { key: "created_at", header: "Joined", align: "right", sortable: true, render: (u) => (u.created_at ? timeAgo(u.created_at) : "—") },
  ];

  const rowActions = (u) => (
    <>
      <Button variant="ghost" size="sm" iconOnly title={`Edit ${u.full_name}`} leftIcon={FiEdit2} onClick={() => openEdit(u)} />
      <Button
        variant="ghost"
        size="sm"
        iconOnly
        title={u.is_active ? "Deactivate" : "Activate"}
        leftIcon={u.is_active ? FiSlash : FiCheckCircle}
        onClick={() => toggleActive(u)}
      />
      <Button variant="ghost" size="sm" iconOnly title={`Delete ${u.full_name}`} leftIcon={FiTrash2} className="hover:text-rose-600 dark:hover:text-rose-400" onClick={() => remove(u)} />
    </>
  );

  if (error) {
    return (
      <div className="mx-auto max-w-[1440px] rounded-xl border border-rose-200 bg-rose-50 px-5 py-4 text-sm text-rose-700 dark:border-rose-500/20 dark:bg-rose-500/10 dark:text-rose-300">
        Couldn't load users. Try refreshing the page.
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px] space-y-6">
      <div>
        <h1 className="text-[24px] font-semibold tracking-tight text-slate-900 dark:text-white">Users</h1>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Every account across every organization</p>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <StatCard label="Total" value={kpis.total} loading={loading} />
        <StatCard label="Active" value={kpis.active} loading={loading} />
        <StatCard label="Inactive" value={kpis.inactive} loading={loading} />
        <StatCard label="Super Admins" value={kpis.superAdmins} loading={loading} />
      </div>

      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search by name, email, or username…" className={inputCls} />
          </div>
          <select value={role} onChange={(e) => setRole(e.target.value)} className={selectCls} aria-label="Filter by role">
            <option value="all">All roles</option>
            {ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </select>
          <select value={org} onChange={(e) => setOrg(e.target.value)} className={selectCls} aria-label="Filter by organization">
            <option value="all">All organizations</option>
            {orgs.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <select value={active} onChange={(e) => setActive(e.target.value)} className={selectCls} aria-label="Filter by status">
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={filtered}
          rowKey={(u) => u.id}
          loading={loading}
          rowActions={rowActions}
          initialSort={{ key: "created_at", dir: "desc" }}
          pageSize={10}
          minWidth={820}
          empty={{
            icon: FiUsers,
            title: "No users match your filters",
            description: "Try clearing the search or switching the role, organization, and status filters.",
            action: (
              <Button variant="secondary" size="sm" onClick={() => { setQ(""); setRole("all"); setOrg("all"); setActive("all"); }}>
                Clear filters
              </Button>
            ),
          }}
        />
      </Panel>

      <UserModal open={modalOpen} onClose={() => setModalOpen(false)} user={editingUser} onSaved={reload} />
    </div>
  );
}
