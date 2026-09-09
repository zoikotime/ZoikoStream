import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import Card from "../../ui/Card";
import Spinner from "../../ui/Spinner";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { roleHome } from "../../auth/roleHome";
import { Field, PasswordField, SubmitButton } from "../../ui/forms";

const ROLE_LABEL = {
  org_admin: "Organization Admin",
  host: "Host",
  speaker: "Speaker",
  viewer: "Viewer",
};

// Invited members land here from their invitation email:
// {CORS origin}/accept-invite?token=<raw token> — built by _invite_url() in
// routers/organization.py. The token identifies the invite; everything else (who invited
// them, their role, the org name) is fetched from the server rather than trusted from the URL.
export default function AcceptInvitation() {
  const [params] = useSearchParams();
  const { setSession } = useAuth();
  const navigate = useNavigate();
  const token = params.get("token") || "";

  const [invite, setInvite] = useState(null);
  // A link with no token needs no request, so both of these start at their final values
  // instead of being written by a synchronous setState at the top of the effect below. That
  // also removes a frame in which a plainly malformed link showed a loading spinner.
  const [loadError, setLoadError] = useState(token ? "" : "This invitation link is missing its token.");
  const [loadingInvite, setLoadingInvite] = useState(!!token);

  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    // Nothing to fetch and nothing to set — the initial state above already says so.
    if (!token) return;
    let cancelled = false;
    api
      .get("/organization/invitations/preview", { params: { token } })
      .then(({ data }) => {
        if (!cancelled) setInvite(data);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(errMsg(error, "This invitation is invalid or has expired."));
      })
      .finally(() => {
        if (!cancelled) setLoadingInvite(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const submit = async (e) => {
    e.preventDefault();
    const next = {};
    if (!name.trim()) next.name = "Your name is required.";
    if (password.length < 8) next.password = "Use at least 8 characters.";
    if (confirm !== password) next.confirm = "Passwords do not match.";
    setErrors(next);
    if (Object.keys(next).length) return;

    setSubmitting(true);
    try {
      const { data } = await api.post("/organization/invitations/accept", {
        token,
        full_name: name.trim(),
        password,
      });
      // Awaited for the same reason as Login: setSession confirms the token with
      // GET /auth/me, so the destination is the server's answer and the route is not
      // entered while auth state is still resolving.
      const account = await setSession(data);
      if (!account) {
        notify.error("Your account was created, but the session could not be confirmed. "
                     + "Please sign in.");
        navigate("/login", { replace: true });
        return;
      }
      notify.success("Welcome to ZoikoStream!");
      navigate(roleHome(account.role) || "/", { replace: true });
    } catch (error) {
      notify.error(errMsg(error, "Couldn't accept this invitation."));
    } finally {
      setSubmitting(false);
    }
  };

  if (loadingInvite) {
    return (
      <Card padding="xl" className="flex items-center justify-center">
        <Spinner />
      </Card>
    );
  }

  if (loadError || !invite) {
    return (
      <Card padding="xl">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Invitation not found</h1>
        <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
          {loadError || "This invitation link is invalid."}
        </p>
        <p className="mt-8 text-center text-sm text-slate-500 dark:text-slate-400">
          <Link to="/login" className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">
            Back to Sign In
          </Link>
        </p>
      </Card>
    );
  }

  const roleLabel = ROLE_LABEL[invite.role] || invite.role;

  return (
    <Card padding="xl">
      <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-white">Accept Invitation</h1>
      <p className="mt-1.5 text-sm text-slate-500 dark:text-slate-400">
        You've been invited to join{" "}
        <span className="font-semibold text-slate-700 dark:text-slate-200">{invite.organization_name}</span> as a{" "}
        <span className="font-semibold text-emerald-700 dark:text-emerald-400">{roleLabel}</span>. Set a password to
        continue.
      </p>

      <form onSubmit={submit} noValidate className="mt-8 space-y-4">
        <Field label="Work Email" value={invite.email} readOnly disabled className="opacity-70" />
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

        <SubmitButton loading={submitting}>Set Password & Continue</SubmitButton>

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
