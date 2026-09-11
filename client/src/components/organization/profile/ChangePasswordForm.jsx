import { useState } from "react";
import { Check, KeyRound } from "lucide-react";
import api from "../../../api";
import Button from "../../../ui/Button";
import { PasswordField } from "../../../ui/forms";
import { cx } from "../../../ui/tokens";
import { TXT } from "./styles";

/**
 * Change your own password, in place, in Settings -> Security.
 *
 * This used to be a button to /forgot-password: a signed-in admin was sent to the
 * signed-OUT recovery flow to prove an identity the session had already proved. The real
 * change now happens here against PATCH /api/auth/password, which takes the account from
 * the bearer token — there is no user id or email in the body, so the form cannot be aimed
 * at anyone else.
 *
 * Deliberately NOT implemented here:
 *   • password policy. The server owns it (services/org_policy.password_violation), and
 *     its refusal is rendered verbatim below. A length check copied into this file would be
 *     a second policy that drifts from the first one the day an org changes its minimum.
 *     `minLength` is only ever a HINT, read from the payload Settings already fetched.
 *   • persistence of any kind. The three values live in component state for the duration of
 *     one submit and are wiped on success; nothing is written to localStorage or logged.
 *   • signing the user out. This platform does not revoke sessions on a credential change
 *     (auth.SESSION_EFFECT_NOT_REVOKED), and the emailed notification says exactly that, so
 *     inventing a logout here would make the product contradict its own security mail.
 */

const BLANK = { current: "", next: "", confirm: "" };

export default function ChangePasswordForm({ minLength }) {
  const [values, setValues] = useState(BLANK);
  const [fieldError, setFieldError] = useState({});
  const [formError, setFormError] = useState("");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  // Typing anywhere retires the previous outcome — a stale "Password updated" sitting above
  // a half-typed retry is the kind of thing people act on.
  const set = (key) => (e) => {
    const v = e.target.value;
    setValues((prev) => ({ ...prev, [key]: v }));
    setFieldError({});
    setFormError("");
    setSaved(false);
  };

  // The only two checks this component is entitled to make: both are about the FORM, not
  // about what makes a password acceptable.
  const mismatch = values.confirm.length > 0 && values.next !== values.confirm;
  const complete = values.current && values.next && values.confirm;
  const ready = Boolean(complete) && !mismatch;

  const submit = async (e) => {
    e.preventDefault();
    if (!ready || saving) return;

    setSaving(true);
    setFieldError({});
    setFormError("");
    setSaved(false);
    try {
      await api.patch("/auth/password", {
        current_password: values.current,
        new_password: values.next,
      });
      // Clear before showing success: the fields must not keep holding the old and new
      // passwords on a screen someone may now walk away from.
      setValues(BLANK);
      setSaved(true);
    } catch (err) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 400) {
        setFieldError({ current: "Current password is incorrect." });
      } else if (status === 422) {
        // A string detail is one of the server's own deliberate refusals ("at least N
        // characters for this Organization", "must be different from your current
        // password") and is written to be read. Anything else is schema noise from
        // Pydantic, which should not be shown to a person.
        setFieldError({
          next: typeof detail === "string" ? detail : "New password does not meet security requirements.",
        });
      } else if (status === 429) {
        setFormError(
          typeof detail === "string" ? detail : "Too many attempts. Please wait a moment and try again."
        );
      } else {
        setFormError("Unable to update password. Please try again.");
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate>
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-slate-100 text-slate-500 dark:bg-white/[0.06] dark:text-neutral-400">
          <KeyRound className="h-4.5 w-4.5" aria-hidden="true" />
        </span>
        <div>
          <p className={cx("text-[13px] font-semibold", TXT.heading)}>Password</p>
          <p className={cx("mt-0.5 text-[12px]", TXT.muted)}>Update your account password.</p>
        </div>
      </div>

      <div className="mt-5 max-w-md space-y-4">
        <PasswordField
          variant="form"
          label="Current password"
          name="current_password"
          autoComplete="current-password"
          value={values.current}
          onChange={set("current")}
          error={fieldError.current}
          disabled={saving}
        />
        <PasswordField
          variant="form"
          label="New password"
          name="new_password"
          autoComplete="new-password"
          value={values.next}
          onChange={set("next")}
          error={fieldError.next}
          // A hint, not a rule: the server re-checks and its message wins. Shown only when
          // the organization's configured minimum actually loaded.
          hint={
            typeof minLength === "number" && minLength > 0
              ? `At least ${minLength} characters, as required by this organization.`
              : undefined
          }
          disabled={saving}
        />
        <PasswordField
          variant="form"
          label="Confirm new password"
          name="confirm_password"
          autoComplete="new-password"
          value={values.confirm}
          onChange={set("confirm")}
          error={mismatch ? "Passwords do not match." : undefined}
          disabled={saving}
        />
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!ready || saving}>
          {saving ? "Updating…" : "Update password"}
        </Button>

        {saved && (
          <p
            role="status"
            className="inline-flex items-center gap-1.5 text-[13px] font-medium text-emerald-600 dark:text-emerald-400"
          >
            <Check className="h-4 w-4" aria-hidden="true" />
            Password updated. Your other sessions stay signed in.
          </p>
        )}
        {formError && (
          <p role="alert" className="text-[13px] font-medium text-rose-600 dark:text-rose-400">
            {formError}
          </p>
        )}
      </div>
    </form>
  );
}
