import { useEffect, useState } from "react";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Select, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { ASSIGNABLE_PLATFORM_ROLES, roleLabel, STAFF_COMMERCIAL_ROLES } from "./roleInfo";
import { readSupportState } from "./supportAccess";

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
            // Enabled. Editing an account's platform attributes — name, platform role,
            // whether it may sign in — is platform governance, and PATCH /admin/users/{id}
            // no longer sits behind an ORG-009 support context (see its docstring). The
            // server is still the authority: it requires super_admin, requires an "identity"
            // elevation for the high-risk subset, and refuses anything that would empty the
            // active super-admin set.
            disabled={saving}
          >
            Save changes
          </Button>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        {/* No blocking banner. This modal used to refuse every edit — including fixing a
            typo in a name — until the customer approved a support session, which made
            ordinary platform administration impossible: the super admin had to ask the
            tenant for permission to manage an account the PLATFORM owns the governance of.
            The gate moved to where it belongs (see the endpoint), and what is left here is
            a statement of what the server will actually enforce.

            The support session notice is still shown when one IS active, because that is a
            real and relevant fact about the operator's current session — it is just no
            longer a precondition for saving. */}
        {!supportLoading && access.canWrite && (
          <p className="rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-2.5 text-xs text-emerald-800 dark:border-emerald-500/25 dark:bg-emerald-500/10 dark:text-emerald-200">
            Support session active for this organization.
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
