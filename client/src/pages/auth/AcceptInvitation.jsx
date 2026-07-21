import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Card from "../../ui/Card";
import { notify } from "../../ui/Toast";
import { useAuth } from "../../auth/AuthContext";
import { roleHome } from "../../auth/roleHome";
import { fakeSession } from "../../auth/dummy";
import { Field, PasswordField, SubmitButton } from "./fields";

const ROLE_LABEL = {
  host: "Host",
  moderator: "Moderator",
  viewer: "Viewer",
  speaker: "Speaker",
  org_admin: "Organization Admin",
};

// Invited Hosts / Moderators / Viewers land here from their invitation email. They set a
// password and are dropped straight into their assigned dashboard. Everything is read from
// the (dummy) invite link, e.g. /accept-invitation?email=host@acme.com&role=host&org=Acme.
export default function AcceptInvitation() {
  const [params] = useSearchParams();
  const { setSession } = useAuth();
  const navigate = useNavigate();

  const email = params.get("email") || "";
  const role = ROLE_LABEL[params.get("role")] ? params.get("role") : "viewer";
  const org = params.get("org") || "your organization";
  const eventId = params.get("event"); // viewers are invited to a specific event

  // Where the accepted user goes. Viewers have no dashboard: an invited viewer lands on
  // their event's watch page (or the public site if no event was attached); staff/admins
  // go to their role dashboard.
  const destination =
    role === "viewer" ? (eventId ? `/events/${eventId}/watch` : "/") : roleHome(role) || "/";

  const [name, setName] = useState(params.get("name") || "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({});

  const submit = (e) => {
    e.preventDefault();
    const next = {};
    if (!name.trim()) next.name = "Your name is required.";
    if (password.length < 8) next.password = "Use at least 8 characters.";
    if (confirm !== password) next.confirm = "Passwords do not match.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setLoading(true);
    // ponytail: dummy — accept the invite and enter the role's dashboard. Swap for
    // POST /auth/accept-invitation later.
    setTimeout(() => {
      const session = fakeSession(email, { role, full_name: name.trim(), organization_name: org });
      setSession(session);
      notify.success("Welcome to ZoikoStream!");
      navigate(destination, { replace: true });
    }, 600);
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
