import { useEffect, useState } from "react";
import { FiLock } from "react-icons/fi";
import Modal from "../../ui/Modal";
import { ConsoleButton as Button } from "../../ui/Button";
import { Input, Select, Label, Switch } from "../../ui/forms";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { ASSIGNABLE_PLATFORM_ROLES, roleLabel, STAFF_COMMERCIAL_ROLES } from "./roleInfo";
import { readSupportState } from "./supportAccess";
import useElevation from "../../components/admin/useElevation";

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

  // ── "identity" elevation: shown before the click, not discovered after it ────────────
  // The server refuses the high-risk subset of this PATCH without an active "identity"
  // elevation (routers/admin._is_high_risk_user_change). This mirrors that predicate so the
  // modal can say so and offer the way out, the same way EndSessionDialog does for
  // "broadcast". It is a mirror, not the authority: the PATCH still decides, and a 403 from
  // it is still surfaced below.
  //
  // The staff scope is the value the server would RECEIVE. It used to be sent on every save
  // ("" for anyone not a super admin), and the server treats any value it receives as a
  // staff-scoping change, so renaming an Org Admin demanded the same step-up as minting a
  // Super Admin. It is now sent only when it actually changes.
  const staffScope = form.role === "super_admin" ? form.staff_commercial_role : "";
  const staffChanged = Boolean(user) && staffScope !== (user.staff_commercial_role || "");
  const needsIdentity = Boolean(user) && (
    (form.role !== user.role && (form.role === "super_admin" || user.role === "super_admin"))
    || (!form.is_active && user.role === "super_admin")
    || staffChanged
  );
  const identity = useElevation("identity");
  // Unknown is not "not elevated": with the console state unreadable the modal cannot claim
  // the operator lacks the grant, so it leaves Save to the server rather than blocking it.
  const elevationBlocked = needsIdentity && !identity.ok && identity.status !== "unknown";
  const [elevationError, setElevationError] = useState(null);

  const elevateForIdentity = async () => {
    setElevationError(null);
    try {
      const granted = await identity.elevate("Change a platform role from Identity & Access");
      if (!granted) {
        setElevationError(
          "You already hold a different elevation, and the platform opens one at a time. " +
          "End it from the rail (“End now”), then elevate again."
        );
      }
    } catch (e2) {
      setElevationError(errMsg(e2));
    }
  };

  const close = () => !saving && !identity.elevating && onClose();

  const submit = async (e) => {
    e.preventDefault();
    if (!user) return;
    if (!form.full_name.trim()) return notify.error("Name is required");
    if (elevationBlocked) return;
    setSaving(true);
    try {
      await api.patch(`/admin/users/${user.id}`, {
        full_name: form.full_name.trim(),
        role: form.role,
        is_active: form.is_active,
        // "" clears it; a non-empty value is only valid for a super_admin
        // (schemas.admin.UserUpdate). Omitted when unchanged — see staffChanged above.
        ...(staffChanged ? { staff_commercial_role: staffScope } : {}),
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
          <Button variant="secondary" size="sm" onClick={close} disabled={saving || identity.elevating}>Cancel</Button>
          {elevationBlocked ? (
            <Button size="sm" onClick={elevateForIdentity} loading={identity.elevating} disabled={identity.elevating}>
              Elevate access
            </Button>
          ) : (
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
          )}
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
        {needsIdentity && !identity.ok && (
          <div role="status" className="flex gap-2.5 rounded-lg bg-amber-50 px-3 py-2.5 text-xs text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">
            <FiLock aria-hidden="true" className="mt-0.5 shrink-0" />
            <p>
              {identity.status === "unknown"
                ? "Access state unknown — the console API is unreachable, so it cannot be confirmed that you hold the identity elevation this change needs. The server will decide when you save."
                : identity.status === "expired"
                  ? "Your elevation has expired. Elevate again to make this change."
                  : identity.status === "wrong_scope"
                    ? "Your current elevation does not cover identity changes. Elevate for identity to continue."
                    : "Granting or removing Super Admin, deactivating a Super Admin, or changing commercial staff scope needs an active identity elevation (15 minutes, audited)."}
            </p>
          </div>
        )}
        {needsIdentity && identity.ok && identity.seconds > 0 && (
          <p className="text-xs text-slate-500 dark:text-slate-400">
            Identity elevation active · {Math.floor(identity.seconds / 60)}m {identity.seconds % 60}s remaining.
          </p>
        )}
        {elevationError && (
          <p role="alert" className="rounded-lg bg-rose-50 px-3 py-2.5 text-xs text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">
            {elevationError}
          </p>
        )}
      </form>
    </Modal>
  );
}
