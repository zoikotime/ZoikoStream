import { useEffect, useRef, useState } from "react";
import toast from "react-hot-toast";
import { FiCheckCircle, FiEdit2, FiSlash, FiTrash2, FiUsers, FiSearch } from "react-icons/fi";
import { Badge, Button, CONSOLE, DataTable, Panel, StatCard, initials, timeAgo } from "../../components/admin";
import api, { errMsg } from "../../api";
import useApi from "../../hooks/useApi";
import UserModal from "./UserModal";
import { ASSIGNABLE_PLATFORM_ROLES, roleLabel } from "./roleInfo";

// Stable identity for the "nothing loaded yet" case. The useMemo hooks below take this list
// as a dependency, and a fresh `[]` literal on every render would defeat every one of them
// (permanently, for a response that simply omits the field). It is never mutated.
const NONE = [];

// No "moderator" key: the role is retired. A not-yet-migrated legacy row falls through to
// the default tone rather than showing a role the platform no longer has.
const ROLE_TONE = { super_admin: "brand", org_admin: "info", host: "info", speaker: "neutral", viewer: "neutral" };

// Field skins come from the console tokens so a hover or focus change lands on every filter
// row at once, instead of being re-typed per page.
const inputCls = CONSOLE.search;
const selectCls = CONSOLE.select;

export const PAGE_SIZE = 50;   // well inside the API's le=100 cap

// DataTable identifies a column by the row field it renders; the API names sortable fields in
// its own vocabulary and validates them with a regex. Sending the column key straight through
// meant sort_by=full_name, which the route rejects with 422 — the table would have looked
// sorted only because the request silently failed and the previous page stayed on screen.
// Anything not in this map is sent as no sort at all, so a new column cannot invent a field.
const SORT_FIELD = {
  full_name: "name",
  organization_name: "organization",
  role: "role",
  created_at: "joined",
};

// Everything is decided by the database now: filtering, sorting AND paging.
//
// The page used to ask for page_size:100 with no `page`, take `items` and throw `total`
// away. With 1128 accounts that made 1028 of them unreachable — the operator could not even
// tell, because the count line reported the size of the page it had. Sorting made it worse
// rather than better: DataTable ordered the fetched rows, so "Joined, oldest first" returned
// the oldest of the newest 100, which reads exactly like an answer and is not one.
function useUsersData({ q, role, isActive, orgId, page, sort }) {
  return useApi(() =>
    api
      .get("/admin/users", {
        params: {
          page,
          page_size: PAGE_SIZE,
          q: q || undefined,
          role: role === "all" ? undefined : role,
          is_active: isActive === "all" ? undefined : isActive === "active",
          // The API filters by org_id; the old dropdown matched organization_name over the
          // rows already fetched, so it could only ever offer the orgs on the current page.
          org_id: orgId === "all" ? undefined : orgId,
          sort_by: (sort && SORT_FIELD[sort.key]) || undefined,
          order: sort && SORT_FIELD[sort.key] ? sort.dir : undefined,
        },
      })
      // `total` is the whole point — it is what makes the last page reachable.
      .then((r) => ({ items: r.data.items, total: r.data.total }))
  );
}

// Every organization, not just those on the current page. Paged at the API's maximum and
// walked to the end, because the filter is a promise that it lists them all.
function useAllOrganizations() {
  return useApi(async () => {
    const out = [];
    for (let page = 1; page <= 50; page += 1) {
      const { data } = await api.get("/admin/organizations", { params: { page, page_size: 100 } });
      out.push(...(data.items || []));
      if (out.length >= (data.total ?? 0) || !(data.items || []).length) break;
    }
    return out.map((o) => ({ id: o.id, name: o.name })).sort((a, b) => a.name.localeCompare(b.name));
  });
}

// Dataset-wide counts for the KPI cards — independent of whatever filters are active, so
// they don't collapse to the size of the current search result. One round trip against
// GET /admin/users/summary rather than four page_size=1 list calls.
function useUserStats() {
  const [stats, setStats] = useState(null);
  // There was no catch here at all. A failed summary left `stats` null and the cards fell
  // back to { total: rows.length, active: 0, inactive: 0 } — three zeros and a "total" that
  // was really the page size, all rendered as if measured. Unknown must stay unknown.
  const [failed, setFailed] = useState(false);
  // No synchronous setState here: `useEffect(load, [])` runs this on mount, and clearing the
  // flag before the request would be a set-state-in-effect (an error under this repo's
  // eslint-plugin-react-hooks). Both outcomes are settled in the async handlers instead, so
  // each resolution states the result rather than pre-announcing it.
  const load = () => {
    api
      .get("/admin/users/summary")
      .then((r) => {
        setFailed(false);
        setStats({
          total: r.data.total,
          active: r.data.active,
          inactive: r.data.inactive,
          superAdmins: r.data.super_admins,
        });
      })
      .catch(() => {
        setStats(null);
        setFailed(true);
      });
  };
  useEffect(load, []);
  return { stats, statsFailed: failed, reload: load };
}

// Every platform user, real GET/PATCH/DELETE against /admin/users. Creation isn't offered
// here — accounts come from org signup/invitations, not a super-admin form.
export default function Users() {
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [role, setRole] = useState("all");
  const [org, setOrg] = useState("all");
  const [active, setActive] = useState("all");
  const [page, setPage] = useState(1);
  const [sort, setSort] = useState(null);      // { key, dir } | null -> server sort_by/order
  const [modalOpen, setModalOpen] = useState(false);
  const [editingUser, setEditingUser] = useState(null);

  // Debounce the search box so we're not firing a request per keystroke.
  useEffect(() => {
    const t = setTimeout(() => setQ(qInput.trim()), 300);
    return () => clearTimeout(t);
  }, [qInput]);

  // Narrowing the set must send the operator back to its first page: staying on page 9 of a
  // result that now has 2 is an empty table that looks like "no users".
  const resetPage = (apply) => { setPage(1); apply(); };

  const { data, loading, error, reload } = useUsersData({
    q, role, isActive: active, orgId: org, page, sort,
  });
  const { stats, statsFailed, reload: reloadStats } = useUserStats();
  const { data: allOrgs } = useAllOrganizations();
  const reloadAll = () => { reload(); reloadStats(); };

  // Re-fetch whenever anything the SERVER decides changes — filters, sort, or page.
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    reload();
  }, [q, role, active, org, page, sort, reload]);

  // A search typed while on a later page must land on page 1 of its own results.
  const [lastQ, setLastQ] = useState(q);
  if (q !== lastQ) { setLastQ(q); if (page !== 1) setPage(1); }

  const rows = data?.items || NONE;
  const total = data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const orgs = allOrgs || NONE;

  // An em dash, never 0 and never null. StatCard renders a non-number verbatim, so `null`
  // would paint an empty card that just reads as broken; "—" is the same "not available"
  // convention the analytics cards use. Falling back to rows.length here is what made a
  // 50-row page report a 50-account platform.
  const UNKNOWN = "—";
  const kpis = stats || {
    total: UNKNOWN, active: UNKNOWN, inactive: UNKNOWN, superAdmins: UNKNOWN,
  };

  const openEdit = (u) => { setEditingUser(u); setModalOpen(true); };

  const toggleActive = async (u) => {
    try {
      await api.patch(`/admin/users/${u.id}`, { is_active: !u.is_active });
      toast.success(`${u.full_name} ${u.is_active ? "deactivated" : "activated"}`);
      reloadAll();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (u) => {
    if (!window.confirm(`Delete ${u.full_name}? This cannot be undone.`)) return;
    try {
      await api.delete(`/admin/users/${u.id}`);
      toast.success(`${u.full_name} deleted`);
      reloadAll();
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

      {statsFailed && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-sm text-amber-800 dark:border-amber-500/25 dark:bg-amber-500/10 dark:text-amber-200">
          <span>Couldn&apos;t load the account totals. The table below is unaffected.</span>
          <Button variant="secondary" size="sm" onClick={reloadStats}>Retry</Button>
        </div>
      )}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        {/* "—" when the summary failed: unknown, which is not the same fact as zero. */}
        <StatCard label="Total" value={kpis.total} loading={loading && !statsFailed} />
        <StatCard label="Active" value={kpis.active} loading={loading && !statsFailed} />
        <StatCard label="Inactive" value={kpis.inactive} loading={loading && !statsFailed} />
        <StatCard label="Super Admins" value={kpis.superAdmins} loading={loading && !statsFailed} />
      </div>

      <Panel flush>
        <div className="flex flex-wrap items-center gap-3 px-4 py-3">
          <div className="relative min-w-0 flex-1">
            <FiSearch className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
            <input value={qInput} onChange={(e) => setQInput(e.target.value)} placeholder="Search by name, email, or username…" className={inputCls} />
          </div>
          {/* The two platform roles, matching what the Edit modal can assign — filtering by a
              role this console cannot grant was a dead end nobody reached for.

              "All roles" is untouched and still means ALL: useUsersData sends no `role` param
              for "all", so the backend applies no role predicate and every Billing Admin,
              Host, Speaker and Viewer comes back. Narrowing the options here removes three
              ways to SLICE the list, never a way to see someone — the Role column still shows
              each account's real role, and search still finds them by name/email/username. */}
          <select value={role} onChange={(e) => resetPage(() => setRole(e.target.value))} className={selectCls} aria-label="Filter by role">
            <option value="all">All roles</option>
            {ASSIGNABLE_PLATFORM_ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
          </select>
          {/* Every organization, and the value is the org_id the API filters by. This used to
              be built from `new Set(rows.map(u => u.organization_name))` — the orgs present
              on the current page — so most tenants were simply not offered, and picking one
              filtered the page rather than the platform. */}
          <select value={org} onChange={(e) => resetPage(() => setOrg(e.target.value))} className={selectCls} aria-label="Filter by organization">
            <option value="all">All organizations</option>
            {orgs.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          <select value={active} onChange={(e) => resetPage(() => setActive(e.target.value))} className={selectCls} aria-label="Filter by status">
            <option value="all">All statuses</option>
            <option value="active">Active</option>
            <option value="inactive">Inactive</option>
          </select>
        </div>
        <div className="border-t border-slate-100 dark:border-slate-800/70" />
        <DataTable
          columns={columns}
          rows={rows}
          rowKey={(u) => u.id}
          loading={loading}
          rowActions={rowActions}
          minWidth={820}
          // Server-driven: DataTable renders the page the API returned and reports clicks
          // back, so sorting and paging span all {total} accounts rather than these rows.
          pageSize={PAGE_SIZE}
          serverSort={sort}
          onSortChange={(next) => { setPage(1); setSort(next); }}
          serverPage={page}
          serverPageCount={pageCount}
          serverTotal={total}
          onPageChange={setPage}
          empty={{
            icon: FiUsers,
            title: "No users match your filters",
            description: "Try clearing the search or switching the role, organization, and status filters.",
            action: (
              <Button variant="secondary" size="sm" onClick={() => { setPage(1); setQInput(""); setRole("all"); setOrg("all"); setActive("all"); }}>
                Clear filters
              </Button>
            ),
          }}
        />
      </Panel>

      {modalOpen && (
        <UserModal key={editingUser?.id ?? "none"} open onClose={() => setModalOpen(false)} user={editingUser} onSaved={reloadAll} />
      )}
    </div>
  );
}
