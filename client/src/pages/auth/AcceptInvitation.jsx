import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { roleHome } from "../../auth/roleHome";
import { Field, PasswordField, SubmitButton } from "../../ui/forms";

const ROLE_LABEL = {
  host: "Host",
  moderator: "Moderator",
  viewer: "Viewer",
  speaker: "Speaker",
  org_admin: "Organization Admin",
};

// Invited members land here from their invitation email link, e.g.
// /accept-invitation?token=...&email=host@acme.com. `role`/`org` in the URL (if present)
// are cosmetic only, for the copy below -- the server (POST /auth/accept-invitation)
// is the actual source of truth for what role/org the token grants.
export default function AcceptInvitation() {
  const [params] = useSearchParams();
  const { setSession } = useAuth();
  const navigate = useNavigate();

  const token = params.get("token") || "";
  const email = params.get("email") || "";
  const role = ROLE_LABEL[params.get("role")] ? params.get("role") : "viewer";
  const org = params.get("org") || "your organization";

  const [name, setName] = useState(params.get("name") || "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({});

  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!name.trim()) next.name = "Your name is required.";
    if (password.length < 8) next.password = "Use at least 8 characters.";
    if (confirm !== password) next.confirm = "Passwords do not match.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setLoading(true);
    try {
      const { data } = await api.post("/auth/accept-invitation", {
        token,
        email,
        full_name: name.trim(),
        password,
      });
      setSession(data);
      notify.success("Welcome to ZoikoStream!");
      // The server decides the real role/org the token grants -- route off its response,
      // not the (cosmetic) URL params.
      const dest = data.user.role === "viewer" ? "/" : roleHome(data.user.role) || "/";
      navigate(dest, { replace: true });
    } catch (error) {
      notify.error(errMsg(error, "This invitation link is invalid or has expired."));
    } finally {
      setLoading(false);
    }
  };

  return (
    <Card padding="xl">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Accept Invitation</h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
        You've been invited to join <span className="font-semibold text-slate-700 dark:text-slate-200">{org}</span> as a{" "}
        <span className="font-semibold text-emerald-700 dark:text-emerald-400">{ROLE_LABEL[role]}</span>. Set a password to
        continue.
      </p>

      <form onSubmit={submit} noValidate className="mt-8 space-y-4">
        {email && (
          <Field label="Work Email" value={email} readOnly disabled className="opacity-70" />
        )}
        <Field
          label="Full Name"
          autoComplete="name"
          placeholder="Jane Doe"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={errors.name}
        />
        <PasswordField
          label="Create Password"
          autoComplete="new-password"
          placeholder="At least 8 characters"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          error={errors.password}
        />
        <PasswordField
          label="Confirm Password"
          autoComplete="new-password"
          placeholder="Re-enter password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={errors.confirm}
        />

        <SubmitButton loading={loading}>Set Password & Continue</SubmitButton>

        <p className="text-center text-sm text-slate-500 dark:text-slate-400">
          Already accepted?{" "}
          <Link to="/login" className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">
            Sign In
          </Link>
        </p>
      </form>
    </Card>
  );
}
