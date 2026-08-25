import { useState } from "react";
import { Link } from "react-router-dom";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { FiLock, FiMail } from "react-icons/fi";
import { Field, PasswordField, SubmitButton, CheckField } from "../../ui/forms";
import AuthTabs from "./AuthTabs";
import VerificationSentModal from "./VerificationSentModal";


const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());
const slugify = (s) =>
  s.toLowerCase().trim().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

// New organizations only. The creator automatically becomes the Organization Admin —
// no role/username field. Hosts/Moderators/Viewers never land here (they're invited).
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
    <Card
      padding="xl"
      className="zk-fade-in rounded-[20px] shadow-xl shadow-slate-900/5 transition-shadow duration-300 hover:shadow-2xl hover:shadow-violet-900/10 dark:shadow-black/30"
    >
      <AuthTabs />
      <h1 className="text-[26px] font-bold tracking-tight text-slate-900 dark:text-white">
        Create Your Organization
      </h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
        Create your organization and start streaming professional live events.
      </p>

      <form onSubmit={submit} noValidate className="mt-6 space-y-4">
        <Field
          label="Organization Name"
          placeholder="Acme Inc."
          value={form.name}
          onChange={set("name")}
          error={errors.name}
        />
        <Field
          label="Organization Slug (optional)"
          placeholder={slugify(form.name) || "acme-inc"}
          value={form.slug}
          onChange={set("slug")}
          hint="Used in your event links — leave blank to auto-generate."
        />
        <Field
          label="Organization Admin Name"
          autoComplete="name"
          placeholder="Jane Doe"
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
          value={form.email}
          onChange={set("email")}
          error={errors.email}
        />
        <PasswordField
          icon={FiLock}
          label="Password"
          autoComplete="new-password"
          placeholder="At least 8 characters"
          value={form.password}
          onChange={set("password")}
          error={errors.password}
        />
        <PasswordField
          icon={FiLock}
          label="Confirm Password"
          autoComplete="new-password"
          placeholder="Re-enter password"
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

        <SubmitButton loading={loading} variant="gradient">Create Organization</SubmitButton>

        <p className="text-center text-sm text-slate-500 dark:text-slate-400">
          Already have an account?{" "}
          <Link
            to="/login"
            className="rounded-lg font-semibold text-emerald-700 underline-offset-4 transition-colors duration-200 hover:text-fuchsia-600 hover:underline dark:text-emerald-400 dark:hover:text-fuchsia-400"
          >
            Sign In
          </Link>
        </p>
      </form>
    </Card>
    </>
  );
}
