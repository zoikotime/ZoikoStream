import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { cx } from "../../ui/tokens";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { FiLock, FiMail } from "react-icons/fi";
import { Field, PasswordField, CheckField } from "../../ui/forms";
import AuthCard from "./AuthCard";
import AuthSubmit from "./AuthSubmit";
import { authLink } from "./authStyles";
import VerificationSentModal from "./VerificationSentModal";


const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());
const slugify = (s) =>
  s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

// New organizations only. The creator automatically becomes the Organization Admin —
// no role/username field. Hosts/Speakers/Viewers never land here (they're invited).
export default function CreateOrganization() {
  // ZST-EC-001 IDN-001: registration no longer returns a session. The API answers
  // 202 EMAIL_VERIFICATION_REQUIRED with a masked address, and the account stays
  // unverified until the emailed link is redeemed — so there is nothing to sign in with
  // here and no roleHome() redirect to make.
  const [pending, setPending] = useState(null);   // { email, expires_in_minutes }
  const [resending, setResending] = useState(false);

  const [form, setForm] = useState({
    name: "",
    slug: "",
    adminName: "",
    email: "",
    password: "",
    confirm: "",
  });
  const [agree, setAgree] = useState(false);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({});
  // The slug is optional (blank = auto-generated), so it stays behind a disclosure until asked
  // for — or until it holds something the person typed.
  const slugId = useId();
  const [showSlugRequested, setShowSlug] = useState(false);
  const showSlug = showSlugRequested || !!form.slug || !!errors.slug;
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!form.name.trim()) next.name = "Organization name is required.";
    if (!form.adminName.trim()) next.adminName = "Your name is required.";
    if (!isEmail(form.email)) next.email = "Enter a valid work email.";
    if (form.password.length < 8) next.password = "Use at least 8 characters.";
    if (form.confirm !== form.password) next.confirm = "Passwords do not match.";
    if (!agree) next.agree = "Please accept the Terms and Privacy Policy.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setLoading(true);
    try {
      const { data } = await api.post("/auth/register", {
        full_name: form.adminName.trim(),
        organization_name: form.name.trim(),
        email: form.email.trim(),
        password: form.password,
      });
      setPending({ email: data.email, expires_in_minutes: data.expires_in_minutes });
    } catch (error) {
      notify.error(errMsg(error, "Could not create your organization."));
    } finally {
      setLoading(false);
    }
  };

  // Uses the address the person just typed, not the masked one echoed back — the server
  // never returns the full address and the client must not reconstruct it.
  const resend = async () => {
    setResending(true);
    try {
      const { data } = await api.post("/auth/resend-verification", { email: form.email.trim() });
      notify.success(data?.message || "If that address needs verification, a new link has been sent.");
    } catch (error) {
      notify.error(errMsg(error, "Could not send a new link right now."));
    } finally {
      setResending(false);
    }
  };

  return (
    <>
      {pending && (
        <VerificationSentModal
          maskedEmail={pending.email}
          expiresInMinutes={pending.expires_in_minutes}
          onResend={resend}
          resending={resending}
        />
      )}
    {/* The modal above stays a SIBLING of AuthCard: the card's float transform and glass
        blur would otherwise become the fixed-position modal's containing block. */}
    <AuthCard
      tabs
      dense
      title="Create your organization"
      subtitle="Register your organization and start streaming."
    >
      {/* One field per row, always. `dense` tightens the desktop rhythm so every required
          field and the submit button fit the viewport (measured 1280×720 up to 1920×1080);
          the optional slug opens on request, and only then may a short screen scroll. */}
      <form onSubmit={submit} noValidate className="mt-[var(--auth-s3)] space-y-[var(--auth-s2)]">
        <Field
          label="Organization Name"
          placeholder="Acme Inc."
          className="h-[var(--auth-ctl)] py-0!"
          value={form.name}
          onChange={set("name")}
          error={errors.name}
          hint={
            !showSlug && (
              <button
                type="button"
                aria-expanded="false"
                aria-controls={slugId}
                onClick={() => setShowSlug(true)}
                className={cx("text-xs", authLink)}
              >
                Customize event link (optional)
              </button>
            )
          }
        />
        {showSlug && (
          <div id={slugId}>
            <Field
              label="Organization Slug (optional)"
              placeholder={slugify(form.name) || "acme-inc"}
              className="h-[var(--auth-ctl)] py-0!"
              value={form.slug}
              onChange={set("slug")}
              hint="Used in your event links — leave blank to auto-generate."
            />
          </div>
        )}
        <Field
          label="Organization Admin Name"
          autoComplete="name"
          placeholder="Jane Doe"
          className="h-[var(--auth-ctl)] py-0!"
          value={form.adminName}
          onChange={set("adminName")}
          error={errors.adminName}
        />
        <Field
          icon={FiMail}
          label="Work Email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          className="h-[var(--auth-ctl)] py-0!"
          value={form.email}
          onChange={set("email")}
          error={errors.email}
        />
        <PasswordField
          icon={FiLock}
          label="Password"
          autoComplete="new-password"
          placeholder="At least 8 characters"
          className="h-[var(--auth-ctl)] py-0! pr-12"
          value={form.password}
          onChange={set("password")}
          error={errors.password}
        />
        <PasswordField
          icon={FiLock}
          label="Confirm Password"
          autoComplete="new-password"
          placeholder="Re-enter password"
          className="h-[var(--auth-ctl)] py-0! pr-12"
          value={form.confirm}
          onChange={set("confirm")}
          error={errors.confirm}
        />

        <CheckField
          label="I agree to the Terms and Privacy Policy."
          checked={agree}
          onChange={(e) => setAgree(e.target.checked)}
          error={errors.agree}
        />

        <AuthSubmit loading={loading}>Create Organization</AuthSubmit>

        <p className="text-center text-[15px] text-slate-500 dark:text-slate-400">
          Already have an account?{" "}
          <Link to="/login" className={authLink}>
            Log in
          </Link>
        </p>
      </form>
    </AuthCard>
    </>
  );
}
