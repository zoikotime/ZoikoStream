import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  FiCalendar, FiCheckCircle, FiClock, FiLogIn, FiMail, FiSlash, FiUserCheck, FiXCircle,
} from "react-icons/fi";
import Card from "../../ui/Card";
import Badge from "../../ui/Badge";
import Skeleton from "../../ui/Skeleton";
import { notify } from "../../ui/Toast";
import api, { errMsg } from "../../api";
import { useAuth } from "../../auth/AuthContext";
import { roleHome } from "../../auth/roleHome";
import { Field, PasswordField, SubmitButton } from "../../ui/forms";
import { ConsoleButton as Button } from "../../ui/Button";
import { cx, focusRing } from "../../ui/tokens";

// Invitation acceptance. Route: /accept-invitation#token=<raw>
//
// Everything shown here comes from GET /organization/invitations/preview — never from the
// URL. The previous version read ?role= and ?org= out of the query string and minted a
// matching client-side session, so /accept-invitation?role=org_admin rendered "Organization
// Admin" and opened every org-admin route in the SPA.
//
// The token travels in the URL FRAGMENT, which browsers never transmit: it stays out of
// server access logs, Referer headers and CDN logs. It is read here and POSTed in a body.
//
// NOTHING happens on page load. Mail scanners, link previewers and corporate security
// proxies fetch every URL in an email — a page that accepted (or declined) on mount would be
// triggered by a robot before the human ever read the invitation.

/** Read the token from the fragment, falling back to ?token= for links that predate the
 *  fragment form (or a mail client that mangles the hash). */
function readToken(searchParams) {
  const hash = typeof window !== "undefined" ? window.location.hash.replace(/^#/, "") : "";
  const fromHash = new URLSearchParams(hash).get("token");
  return fromHash || searchParams.get("token") || "";
}

const fmtWhen = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d)
    ? null
    : d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
};

const fmtDay = (iso) => {
  if (!iso) return null;
  const d = new Date(iso);
  return isNaN(d) ? null : d.toLocaleDateString(undefined, { dateStyle: "medium" });
};

function Detail({ icon: Icon, label, children }) {
  return (
    <div className="flex items-start gap-3">
      <span
        className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"
        aria-hidden="true"
      >
        <Icon className="text-sm" />
      </span>
      <div className="min-w-0">
        <p className="text-[11px] font-medium uppercase tracking-wider text-slate-400">{label}</p>
        <p className="text-sm font-medium text-slate-800 dark:text-slate-100">{children}</p>
      </div>
    </div>
  );
}

/** Terminal states: the link is spent, invalid, or the invitee just answered. One component
 *  so every outcome looks deliberate rather than like a half-rendered form. */
function Outcome({ icon: Icon, tone, title, detail, action }) {
  const chip = {
    ok: "bg-emerald-100 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400",
    bad: "bg-rose-100 text-rose-600 dark:bg-rose-500/15 dark:text-rose-400",
    muted: "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400",
  }[tone];
  return (
    <Card padding="xl">
      <div className="flex flex-col items-center py-4 text-center">
        <span className={cx("grid h-12 w-12 place-items-center rounded-full", chip)} aria-hidden="true">
          <Icon className="text-xl" />
        </span>
        <h1 className="mt-4 text-xl font-bold tracking-tight text-slate-900 dark:text-white">{title}</h1>
        {detail && <p className="mt-1.5 max-w-sm text-sm text-slate-500 dark:text-slate-400">{detail}</p>}
        <div className="mt-6">{action}</div>
      </div>
    </Card>
  );
}

export default function AcceptInvitation() {
  const [searchParams] = useSearchParams();
  const { setSession } = useAuth();
  const navigate = useNavigate();

  const token = useMemo(() => readToken(searchParams), [searchParams]);
  // ?decline=1 comes from the email's Decline button. It only PRESELECTS the decline view —
  // the invitee still has to press the button, because arriving here can be a robot.
  const declineFirst = searchParams.get("decline") === "1";

  const [preview, setPreview] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [mode, setMode] = useState(declineFirst ? "decline" : "accept");
  const [done, setDone] = useState(null); // "accepted" | "declined" | "login"
  const [busy, setBusy] = useState(false);

  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [errors, setErrors] = useState({});

  // One read on mount. Strictly a GET — the server performs no write for it, because mail
  // scanners and link previewers fetch this URL before the human does.
  //
  // A missing token needs no request at all, and it is knowable during render, so it is
  // resolved here rather than by setting state from an effect (which would render once
  // claiming to be loading something that was never fetched).
  const [checked, setChecked] = useState(false);
  if (!token && !checked) {
    setChecked(true);
    setLoading(false);
    setLoadError("missing");
  }

  useEffect(() => {
    if (!token) return undefined;
    let alive = true;
    api
      .get("/organization/invitations/preview", { params: { token } })
      .then((r) => alive && setPreview(r.data))
      .catch((e) => alive && setLoadError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [token]);

  const accept = useCallback(async () => {
    const next = {};
    if (preview?.needs_account) {
      if (!name.trim()) next.name = "Your name is required.";
      if (password.length < 8) next.password = "Use at least 8 characters.";
      if (confirm !== password) next.confirm = "Passwords do not match.";
    }
    setErrors(next);
    if (Object.keys(next).length) return;

    setBusy(true);
    try {
      const { data } = await api.post("/organization/invitations/accept", {
        token,
        // Sent only for a brand-new account. An address that already has credentials gets
        // neither — the server refuses to change an existing account's password from here.
        ...(preview?.needs_account ? { full_name: name.trim(), password } : {}),
      });
      if (data.access_token) {
        setSession({ access_token: data.access_token, user: data.user });
        notify.success("Welcome to ZoikoStream!");
        // Straight to the place their new role actually uses.
        navigate(
          data.event_id ? `/events/${data.event_id}/watch` : roleHome(data.user?.role) || "/",
          { replace: true }
        );
        return;
      }
      // Existing account: access granted, but no session is issued from an emailed link.
      setDone("login");
      notify.success("Invitation accepted. Please sign in.");
    } catch (e) {
      notify.error(errMsg(e, "This invitation could not be completed."));
      setBusy(false);
    }
  }, [preview, name, password, confirm, token, setSession, navigate]);

  const decline = async () => {
    setBusy(true);
    try {
      await api.post("/organization/invitations/reject", { token });
      setDone("declined");
    } catch (e) {
      notify.error(errMsg(e, "This invitation could not be declined."));
    } finally {
      setBusy(false);
    }
  };

  // ── loading / invalid ─────────────────────────────────────────────────────
  if (loading) {
    return (
      <Card padding="xl">
        <Skeleton className="h-7 w-56 rounded-lg" />
        <Skeleton className="mt-3 h-4 w-full rounded" />
        <div className="mt-6 space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-11 w-full rounded-lg" />)}
        </div>
        <Skeleton className="mt-6 h-11 w-full rounded-xl" />
      </Card>
    );
  }

  if (loadError || !preview) {
    // The server answers every invalid case identically — unknown, already used, expired,
    // revoked, cancelled — so this page must not guess which one it was.
    return (
      <Outcome
        icon={FiXCircle}
        tone="bad"
        title="This invitation link isn't valid"
        detail="It may have already been used, expired, or been withdrawn. Ask your administrator to send a new one."
        action={
          <Button href="/login" leftIcon={FiLogIn}>Go to sign in</Button>
        }
      />
    );
  }

  // ── terminal states ───────────────────────────────────────────────────────
  if (done === "declined") {
    return (
      <Outcome
        icon={FiSlash}
        tone="muted"
        title="Invitation declined"
        detail={`We've let ${preview.inviter_name || "the organizer"} know. Nothing else will happen.`}
        action={<Button variant="secondary" href="/">Back to ZoikoStream</Button>}
      />
    );
  }

  if (done === "login") {
    return (
      <Outcome
        icon={FiCheckCircle}
        tone="ok"
        title="You're in"
        detail={
          preview.event_title
            ? `You've been added to ${preview.event_title} as ${preview.event_role_label || preview.role_label}. Sign in with your existing ZoikoStream password to continue.`
            : `You've joined ${preview.org_name}. Sign in with your existing ZoikoStream password to continue.`
        }
        action={<Button href="/login" leftIcon={FiLogIn}>Sign in</Button>}
      />
    );
  }

  const expires = fmtDay(preview.expires_at);
  const eventWhen = fmtWhen(preview.event_starts_at);

  return (
    <Card padding="xl">
      <div className="flex items-center gap-3">
        {preview.org_logo_url ? (
          <img
            src={preview.org_logo_url}
            alt=""
            className="h-10 w-10 shrink-0 rounded-lg object-cover"
          />
        ) : (
          <span
            className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-emerald-100 text-sm font-bold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400"
            aria-hidden="true"
          >
            {(preview.org_name || "?").slice(0, 2).toUpperCase()}
          </span>
        )}
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-bold tracking-tight text-slate-900 dark:text-white">
            {mode === "decline" ? "Decline invitation" : "You're invited"}
          </h1>
          <p className="truncate text-sm text-slate-500 dark:text-slate-400">{preview.org_name}</p>
        </div>
      </div>

      <p className="mt-4 text-sm text-slate-600 dark:text-slate-300">
        {preview.inviter_name || "An administrator"} invited you
        {preview.event_title ? (
          <> to join <span className="font-semibold text-slate-800 dark:text-slate-100">{preview.event_title}</span></>
        ) : (
          <> to join <span className="font-semibold text-slate-800 dark:text-slate-100">{preview.org_name}</span></>
        )}{" "}
        as{" "}
        <span className="font-semibold text-emerald-700 dark:text-emerald-400">
          {preview.event_role_label || preview.role_label}
        </span>
        .
      </p>

      {preview.message && (
        <blockquote className="mt-4 border-l-2 border-emerald-500 bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
          {preview.message}
        </blockquote>
      )}

      <div className="mt-5 grid grid-cols-1 gap-4 rounded-xl border border-slate-200 p-4 sm:grid-cols-2 dark:border-slate-700">
        <Detail icon={FiMail} label="Invited address">{preview.email_hint}</Detail>
        <Detail icon={FiUserCheck} label="Role">
          {preview.event_role_label || preview.role_label}
        </Detail>
        {eventWhen && <Detail icon={FiCalendar} label="Event starts">{eventWhen}</Detail>}
        {expires && (
          <Detail icon={FiClock} label="Invitation expires">
            <span className="inline-flex items-center gap-1.5">
              {expires}
              <Badge tone="warning" size="sm">Single use</Badge>
            </span>
          </Detail>
        )}
      </div>

      {mode === "decline" ? (
        <div className="mt-6 space-y-4">
          <p className="text-sm text-slate-600 dark:text-slate-300">
            Declining tells {preview.inviter_name || "the organizer"} you won't be taking part.
            This can't be undone — they'd need to invite you again.
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <Button variant="danger" size="lg" className="flex-1" loading={busy} disabled={busy} onClick={decline}>
              Decline invitation
            </Button>
            <Button variant="secondary" size="lg" className="flex-1" disabled={busy} onClick={() => setMode("accept")}>
              Back
            </Button>
          </div>
        </div>
      ) : preview.needs_account ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            accept();
          }}
          noValidate
          className="mt-6 space-y-4"
        >
          <Field
            label="Full name"
            autoComplete="name"
            placeholder="Jane Doe"
            value={name}
            onChange={(e) => setName(e.target.value)}
            error={errors.name}
          />
          <PasswordField
            label="Create a password"
            autoComplete="new-password"
            placeholder="At least 8 characters"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            error={errors.password}
          />
          <PasswordField
            label="Confirm password"
            autoComplete="new-password"
            placeholder="Re-enter password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
            error={errors.confirm}
          />
          <SubmitButton loading={busy}>Accept &amp; create account</SubmitButton>
          <button
            type="button"
            onClick={() => setMode("decline")}
            className={cx(
              "w-full rounded text-center text-sm font-medium text-slate-500 hover:text-slate-700",
              "dark:text-slate-400 dark:hover:text-slate-200",
              focusRing
            )}
          >
            Decline this invitation
          </button>
        </form>
      ) : (
        <div className="mt-6 space-y-4">
          <p className="rounded-xl bg-slate-50 px-4 py-3 text-sm text-slate-600 dark:bg-slate-800/60 dark:text-slate-300">
            This address already has a ZoikoStream account. Accepting adds the new access to
            it — you'll then sign in with your existing password.
          </p>
          <Button size="lg" className="w-full" loading={busy} disabled={busy} onClick={accept}>
            Accept invitation
          </Button>
          <button
            type="button"
            onClick={() => setMode("decline")}
            className={cx(
              "w-full rounded text-center text-sm font-medium text-slate-500 hover:text-slate-700",
              "dark:text-slate-400 dark:hover:text-slate-200",
              focusRing
            )}
          >
            Decline this invitation
          </button>
        </div>
      )}

      <p className="mt-6 text-center text-sm text-slate-500 dark:text-slate-400">
        Already accepted?{" "}
        <Link to="/login" className="font-semibold text-emerald-700 hover:underline dark:text-emerald-400">
          Sign in
        </Link>
      </p>
    </Card>
  );
}
