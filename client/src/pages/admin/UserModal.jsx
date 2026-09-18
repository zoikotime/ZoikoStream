import { useEffect, useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Select, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { ASSIGNABLE_PLATFORM_ROLES, roleLabel, STAFF_COMMERCIAL_ROLES } from "./roleInfo";
import { CAP_MEMBERS_WRITE, readSupportState } from "./supportAccess";

// Edit an existing platform user's role/name/active state. The admin API has no user
// creation route (accounts are created through org signup/invitations), so this is
// edit-only — no create mode.
export default function UserModal({ open, onClose, user, onSaved }) {
  // Mounted only while open and keyed by the user being edited (Users.jsx), so this initial
  // state IS the per-open reset.
  const [form, setForm] = useState(() =>
    user
      ? {
          full_name: user.full_name || "",
          role: user.role || "viewer",
          is_active: !!user.is_active,
          staff_commercial_role: user.staff_commercial_role || "",
        }
      : { full_name: "", role: "viewer", is_active: true, staff_commercial_role: "" }
  );
  const [saving, setSaving] = useState(false);
  const set = (key, value) => setForm((f) => ({ ...f, [key]: value }));

  // ── ORG-009: may this admin write to this tenant at all? ─────────────────────────────
  // Read, never assumed. The backend answers from the same predicates its gate uses, so this
  // panel cannot promise an ability the PATCH would refuse — and the PATCH is still the thing
  // that decides. Disabling Save is a courtesy, not the control.
  const [support, setSupport] = useState(null);
  // Seeded from whether there is anything to fetch, so the effect below sets state only in
  // its async handlers — a synchronous setState inside an effect is an error under this
  // repo's eslint-plugin-react-hooks. The modal is keyed by user, so this IS the per-open reset.
  const [supportLoading, setSupportLoading] = useState(() => Boolean(user?.org_id));
  const [requesting, setRequesting] = useState(false);
  const orgId = user?.org_id;

  useEffect(() => {
    if (!orgId) return undefined;
    let cancelled = false;
    api
      .get("/admin/support-access/state", { params: { org_id: orgId } })
      .then((r) => { if (!cancelled) setSupport(r.data); })
      // A failed read must not be mistaken for "access granted"; readSupportState treats
      // null as no access, which is the safe direction.
      .catch(() => { if (!cancelled) setSupport(null); })
      .finally(() => { if (!cancelled) setSupportLoading(false); });
    return () => { cancelled = true; };
  }, [orgId]);

  const access = readSupportState(support);

  const requestAccess = async () => {
    setRequesting(true);
    try {
      // The real endpoint, with the real required fields. It creates a REQUEST — the
      // organization still has to approve it, and a session still has to be started.
      await api.post("/admin/support-access", {
        org_id: orgId,
        case_reference: `IDN-${String(user.id).slice(0, 8)}`,
        reason_category: "customer_reported_issue",
        engineer_display: "Platform Support",
        requested_scope: `Account administration for ${user.email}`,
        allowed_actions: [CAP_MEMBERS_WRITE],
        minutes: 60,
      });
      notify.success("Access requested. The organization has been asked to approve it.");
      const r = await api.get("/admin/support-access/state", { params: { org_id: orgId } });
      setSupport(r.data);
    } catch (e) {
      notify.error(errMsg(e));
    } finally {
      setRequesting(false);
    }
  };

  // Super Admin and Org Admin are the only roles this console GRANTS. The account's existing
  // role is appended when it is not one of those, which is the whole reason this is a list
  // rather than a constant:
  //
  // A controlled <select> whose value matches no <option> renders BLANK, and a Billing Admin,
  // Host, Speaker or Viewer would then show an empty Role on an edit form — inviting an
  // operator to pick something just to fill it in, and converting an account that was only
  // opened to fix a typo in the name. Keeping the current value visible and selected means
  // saving without touching Role re-sends exactly the role the row already had.
  //
  // Derived from `user.role`, NOT `form.role`: keying it to the draft would make the original
  // option vanish the moment the operator clicked Org Admin, so a mis-click could not be
  // undone before saving. The modal is keyed by user in Users.jsx, so this is stable per open.
  const roleOptions = ASSIGNABLE_PLATFORM_ROLES.includes(user?.role)
    ? ASSIGNABLE_PLATFORM_ROLES
    : [...ASSIGNABLE_PLATFORM_ROLES, user?.role].filter(Boolean);

  const close = () => !saving && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!user) return;
    if (!form.full_name.trim()) return notify.error("Name is required");
    setSaving(true);
    try {
      await api.patch(`/admin/users/${user.id}`, {
        full_name: form.full_name.trim(),
        role: form.role,
        is_active: form.is_active,
        // "" clears it; only sent meaningfully when role stays/becomes super_admin — the
        // backend rejects a non-empty value otherwise (schemas.admin.UserUpdate).
        staff_commercial_role: form.role === "super_admin" ? form.staff_commercial_role : "",
      });
      notify.success(`${form.full_name} updated`);
      onSaved?.();
      onClose();
    } catch (e2) {
      notify.error(errMsg(e2));
    } finally {
      setSaving(false);
    }
  };

  if (!user) return null;

  return (
    <Modal
      open={open}
      onClose={close}
      title="Edit User"
      className="max-w-md"
      footer={
        <>
          <Button variant="secondary" size="sm" onClick={close} disabled={saving}>Cancel</Button>
          <Button
            size="sm"
            onClick={submit}
            loading={saving}
            // Disabled while the gate is closed so the operator is not invited to fill in a
            // form that cannot be submitted. The server still refuses regardless — this
            // reflects the rule, it does not implement it.
            disabled={supportLoading || !access.canWrite}
            title={access.canWrite ? undefined : "Organization approval is required"}
          >
            Save changes
          </Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        {!supportLoading && !access.canWrite && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-3.5 dark:border-amber-500/25 dark:bg-amber-500/10">
            <p className="text-sm font-medium text-amber-900 dark:text-amber-100">
              Organization approval is required before account changes can be made.
            </p>
            <p className="mt-1 text-xs leading-relaxed text-amber-800/90 dark:text-amber-200/80">
              {access.label}. {access.detail}
            </p>
            {access.canRequest && (
              <Button
                variant="secondary"
                size="sm"
                className="mt-2.5"
                onClick={requestAccess}
                loading={requesting}
              >
                Request support access
              </Button>
            )}
          </div>
        )}
        {!supportLoading && access.canWrite && (
          <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-xs text-emerald-800 dark:border-emerald-500/25 dark:bg-emerald-500/10 dark:text-emerald-200">
            Support session active — account changes are enabled.
            {access.expiresAt ? ` Expires ${new Date(access.expiresAt).toLocaleString()}.` : ""}
          </p>
        )}
        <div>
          <Label>Full name</Label>
          <Input variant="console" value={form.full_name} onChange={(e) => set("full_name", e.target.value)} />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <Label>Email</Label>
            <Input variant="console" value={user.email} disabled className="opacity-60" />
          </div>
          <div>
            <Label>Organization</Label>
            <Input variant="console" value={user.organization_name || "—"} disabled className="opacity-60" />
          </div>
        </div>
        <div>
          <Label>Role</Label>
          <Select variant="console" value={form.role} onChange={(e) => set("role", e.target.value)}>
            {roleOptions.map((r) => (
              <option key={r} value={r}>
                {roleLabel(r)}{ASSIGNABLE_PLATFORM_ROLES.includes(r) ? "" : " (current)"}
              </option>
            ))}
          </Select>
          {!ASSIGNABLE_PLATFORM_ROLES.includes(user.role) && (
            <p className="mt-1 text-xs text-slate-400">
              {roleLabel(user.role)} is granted inside the organization, not here. It stays as
              it is unless you change it; moving this account to Super Admin or Org Admin
              cannot be undone from this console.
            </p>
          )}
        </div>
        {form.role === "super_admin" && (
          <div>
            <Label>Commercial staff scope</Label>
            <Select variant="console" value={form.staff_commercial_role} onChange={(e) => set("staff_commercial_role", e.target.value)}>
              <option value="">Unscoped — full commercial access</option>
              {STAFF_COMMERCIAL_ROLES.map((r) => <option key={r} value={r}>{roleLabel(r)}</option>)}
            </Select>
            <p className="mt-1 text-xs text-slate-400">
              Narrows this account to one commercial role (ZST-LE-COM-001 §25) instead of full access — e.g. Finance/Billing Ops can approve refunds but not accept orders.
            </p>
          </div>
        )}
        <Switch
          checked={form.is_active}
          onChange={(v) => set("is_active", v)}
          accent="violet"
          label={<span className="text-sm font-medium text-slate-700 dark:text-slate-200">Account active</span>}
        />
      </form>
    </Modal>
  );
}
