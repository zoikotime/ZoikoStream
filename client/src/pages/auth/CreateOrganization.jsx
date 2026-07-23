import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import { useAuth } from "../../auth/AuthContext";
import api, { errMsg } from "../../api";
import { Field, PasswordField, SubmitButton } from "./fields";

const isEmail = (v) => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v.trim());

// New organizations only. The creator automatically becomes the Organization Admin —
// no role/username field. Hosts/Moderators/Viewers never land here (they're invited).
export default function CreateOrganization() {
  const { setSession } = useAuth();
  const navigate = useNavigate();

  const [form, setForm] = useState({
    name: "",
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
      const res = await api.post("/auth/register", {
        full_name: form.adminName.trim(),
        organization_name: form.name.trim(),
        email: form.email.trim(),
        password: form.password,
      });
      setSession(res.data);
      notify.success("Organization created! Welcome to ZoikoStream.");
      navigate("/organization/dashboard", { replace: true });
    } catch (err) {
      notify.error(errMsg(err, "Failed to create your organization"));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card padding="xl">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">
        Create Your Organization
      </h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
        Create your organization and start streaming professional live events.
      </p>

      <form onSubmit={submit} noValidate className="mt-8 space-y-4">
        <Field
          label="Organization Name"
          placeholder="Acme Inc."
          value={form.name}
          onChange={set("name")}
          error={errors.name}
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
          label="Work Email"
          type="email"
          autoComplete="email"
          placeholder="you@company.com"
          value={form.email}
          onChange={set("email")}
          error={errors.email}
        />
        <PasswordField
          label="Password"
          autoComplete="new-password"
          placeholder="At least 8 characters"
          value={form.password}
          onChange={set("password")}
          error={errors.password}
        />
        <PasswordField
          label="Confirm Password"
          autoComplete="new-password"
          placeholder="Re-enter password"
          value={form.confirm}
          onChange={set("confirm")}
          error={errors.confirm}
        />

        <div>
          <label className="flex items-start gap-2.5 text-sm text-slate-600 dark:text-slate-300">
            <input
              type="checkbox"
              checked={agree}
              onChange={(e) => setAgree(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded accent-emerald-600"
            />
            <span>I agree to the Terms and Privacy Policy.</span>
          </label>
          {errors.agree && (
            <p className="mt-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">{errors.agree}</p>
          )}
        </div>

        <SubmitButton loading={loading}>Create Organization</SubmitButton>

        <p className="text-center text-sm text-slate-500 dark:text-slate-400">
          Already have an account?{" "}
          <Link to="/login" className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">
            Sign In
          </Link>
        </p>
      </form>
    </Card>
  );
}
