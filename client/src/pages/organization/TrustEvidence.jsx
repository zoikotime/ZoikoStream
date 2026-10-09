import { Link, useLocation, useNavigate } from "react-router-dom";
import { FiArrowLeft, FiHeadphones, FiShield } from "react-icons/fi";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { CONSOLE, cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import { ConsoleButton } from "../../ui/Button";
import Skeleton from "../../ui/Skeleton";
import Panel from "../../components/admin/Panel";
import OrganizationPageHeader from "../../components/organization/OrganizationPageHeader";

// Trust & evidence detail pages — Security Overview and Data Residency — opened from the cards
// on Support & Status. Until these existed both cards opened Settings → Security, which is the
// change-password form and answers neither question.
//
// Every statement below is traceable to code or to a record the platform keeps (the source is
// named beside each group). What the platform does not have is said plainly — "Not available",
// "Not currently published" — rather than filled in: a security page that overstates is worse
// than one that is short. In particular nothing here claims a certification, an encryption
// guarantee or a hosting region, because nothing in this codebase establishes one.
const SUPPORT_PATH = "/organization/support";

const STATUS = {
  in_place: { tone: "success", text: "In place" },
  unavailable: { tone: "neutral", text: "Not available" },
  unpublished: { tone: "neutral", text: "Not currently published" },
  on_request: { tone: "info", text: "On request" },
  provider: { tone: "info", text: "Provider-selected" },
};

// Back goes BACK when this page was opened from Support & Status (the cards pass that in link
// state), so history never grows a support → detail → support loop. Opened directly — a shared
// link, a new tab — there is nothing to go back to, so it navigates. Modified clicks fall
// through to the real href, so "open in new tab" still works.
function BackToSupport() {
  const navigate = useNavigate();
  const location = useLocation();
  const fromSupport = location.state?.from === SUPPORT_PATH;
  return (
    <Link
      to={SUPPORT_PATH}
      onClick={(e) => {
        if (!fromSupport || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
        e.preventDefault();
        navigate(-1);
      }}
      className={cx(
        "inline-flex items-center gap-1.5 rounded text-sm font-medium text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200",
        focusRing
      )}
    >
      <FiArrowLeft aria-hidden="true" /> Back to Support &amp; Status
    </Link>
  );
}

// One fact per row: what it is, what is true about it, and a status a reader can scan.
function FactList({ facts }) {
  return (
    <ul className={cx("divide-y", CONSOLE.divideY)}>
      {facts.map(({ label, status, detail }) => {
        const pill = STATUS[status];
        return (
          <li
            key={label}
            className="flex flex-col gap-2 py-3.5 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:justify-between sm:gap-6"
          >
            <div className="min-w-0">
              <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{label}</p>
              <p className={cx("mt-1 max-w-3xl text-[12px] leading-[18px]", CONSOLE.muted)}>{detail}</p>
            </div>
            <Badge tone={pill.tone} className="shrink-0 self-start whitespace-nowrap">
              {pill.text}
            </Badge>
          </li>
        );
      })}
    </ul>
  );
}

// Where to go for anything the page does not answer. Both destinations exist: /contact is the
// support form and /trust the public Trust Center (evidence requests, vulnerability reports).
function MoreHelp({ children }) {
  return (
    <Panel title="Need more detail?" description={children}>
      <div className="flex flex-wrap items-center gap-2">
        <ConsoleButton href="/contact" size="sm" leftIcon={FiHeadphones}>
          Contact Support
        </ConsoleButton>
        <ConsoleButton href="/trust" variant="secondary" size="sm" leftIcon={FiShield}>
          Trust Center
        </ConsoleButton>
      </div>
    </Panel>
  );
}

function TrustPage({ title, subtitle, children }) {
  return (
    <div className="mx-auto max-w-[1500px] space-y-6">
      <BackToSupport />
      <OrganizationPageHeader title={title} subtitle={subtitle} />
      {children}
    </div>
  );
}

// ── Security Overview ────────────────────────────────────────────────────────────────────

const SECURITY = [
  {
    // server/app/security.py (bcrypt), routers/auth.py (verified email, rate limits, step-up),
    // services/identity_security.py (block on sustained failures — thresholds deliberately
    // not disclosed), services/org_policy.py (8-character baseline an org may raise).
    title: "Authentication & account protection",
    facts: [
      { label: "Password storage", status: "in_place",
        detail: "Passwords are stored only as salted bcrypt hashes. ZoikoStream never stores or shows them in plain text." },
      { label: "Email verification", status: "in_place",
        detail: "An account must verify its email address before it can sign in." },
      { label: "Sign-in abuse protection", status: "in_place",
        detail: "Sign-in attempts are rate limited, and a sustained run of failed attempts temporarily blocks the targeted account." },
      { label: "Password policy", status: "in_place",
        detail: "Every password must be at least 8 characters. Organization admins can raise the minimum length for their members in Settings." },
      { label: "Password reset", status: "in_place",
        detail: "Resets use a short-lived code sent to the account's verified email address." },
      { label: "Re-authentication for sensitive actions", status: "in_place",
        detail: "Granting the organization admin role and transferring organization ownership require the acting admin to re-enter their password." },
    ],
  },
  {
    // services/org.py security_support: two_factor_available / sso_available are False, and
    // require_2fa / enforce_sso are stored preferences nothing in the sign-in path consults.
    title: "Multi-factor authentication & single sign-on",
    facts: [
      { label: "Two-factor authentication (2FA)", status: "unavailable",
        detail: "Not currently offered. The “Require two-factor authentication” preference in Settings is recorded, but sign-in does not enforce it yet." },
      { label: "Single sign-on (SAML)", status: "unavailable",
        detail: "Not currently offered. The “Enforce SSO” preference in Settings is recorded, but sign-in does not enforce it yet." },
    ],
  },
  {
    // security.py role ladder + require_org_admin, org_scoped/get_my_org tenant binding,
    // routers/organization.py self-demotion and self-deactivation guards.
    title: "Access control",
    facts: [
      { label: "Role-based access", status: "in_place",
        detail: "Each member holds a role — Organization admin, Host, Speaker or Viewer — and the server checks it on every request. Admin-only areas are enforced by the API, not only hidden in the interface." },
      { label: "Organization isolation", status: "in_place",
        detail: "Every request is scoped to the signed-in member's own organization, so one organization's data is never returned to another." },
      { label: "Admin safeguards", status: "in_place",
        detail: "Admins cannot remove their own admin role or deactivate themselves, so an organization cannot lock itself out by accident." },
    ],
  },
  {
    // config.py ACCESS_TOKEN_HOURS / REMEMBER_TOKEN_DAYS, org_policy session timeout,
    // security.py rejecting inactive users per request; no server-side session store.
    title: "Sessions",
    facts: [
      { label: "Session expiry", status: "in_place",
        detail: "Sign-in sessions expire after 24 hours, or 30 days with “Remember me”. Organization admins can set a shorter session timeout." },
      { label: "Revoking a member's access", status: "in_place",
        detail: "Deactivating or removing a member ends their access on their next request." },
      { label: "Remote sign-out of other sessions", status: "unavailable",
        detail: "Not currently available. Signing out ends the session in that browser; sessions on other devices stay valid until they expire." },
    ],
  },
  {
    // models/audit_log.py; the only reader is the super-admin /admin/audit-logs endpoint.
    title: "Audit logging",
    facts: [
      { label: "Administrative audit trail", status: "in_place",
        detail: "Security-relevant administrative actions — API key and webhook changes, ownership transfers, access reviews and data exports, among others — are recorded with the acting user and the time." },
      { label: "Audit log access for your organization", status: "unavailable",
        detail: "The audit log is not viewable in this console yet. Contact support for an extract covering your organization." },
    ],
  },
  {
    // Hashed API keys/invitations/verification tokens; config.py refuses a non-https APP_URL
    // in production and email.py requires https links. No field encryption, no security
    // headers middleware — so neither is claimed.
    title: "Data protection & secure transport",
    facts: [
      { label: "Credential and token storage", status: "in_place",
        detail: "API keys, invitation links and verification links are stored as one-way hashes. A new API key is shown once, when it is created." },
      { label: "HTTPS", status: "in_place",
        detail: "Production requires an HTTPS application address, and links in ZoikoStream emails use HTTPS." },
      { label: "Encryption at rest", status: "unpublished",
        detail: "ZoikoStream does not currently publish an encryption-at-rest statement. Contact support for details." },
      { label: "Browser security headers", status: "unpublished",
        detail: "Policies such as HSTS and Content-Security-Policy are not currently published." },
    ],
  },
  {
    // routers/trust.py: documents are request-and-approve; no certification is recorded.
    title: "Certifications & compliance",
    facts: [
      { label: "Security certifications (e.g. SOC 2, ISO 27001)", status: "unpublished",
        detail: "ZoikoStream does not currently publish third-party security certifications or audit reports." },
      { label: "Compliance documents", status: "on_request",
        detail: "Documents the platform publishes can be requested through the Trust Center. Each request is reviewed before access is granted." },
    ],
  },
  {
    // Support & Status (lifecycle health), routers/trust.py advisories + security reports;
    // disclosure_policy reports bounty and coordinated_disclosure_policy as False.
    title: "Incident response & security reporting",
    facts: [
      { label: "Platform status", status: "in_place",
        detail: "Live service health and any active incident are shown on Support & Status." },
      { label: "Security advisories", status: "in_place",
        detail: "Published security advisories are listed in the Trust Center." },
      { label: "Vulnerability reporting", status: "in_place",
        detail: "Anyone can report a vulnerability through the Trust Center and receives a reference to follow its status." },
      { label: "Disclosure policy and bug bounty", status: "unpublished",
        detail: "No coordinated-disclosure policy or bug bounty programme is currently published." },
    ],
  },
];

export function SecurityOverview() {
  return (
    <TrustPage
      title="Security Overview"
      subtitle="How ZoikoStream protects accounts, access and data — limited to what the platform actually does today."
    >
      {SECURITY.map((group) => (
        <Panel key={group.title} title={group.title}>
          <FactList facts={group.facts} />
        </Panel>
      ))}
      <MoreHelp>
        For security questionnaires or anything listed here as not available, contact support.
        Compliance documents can be requested, and vulnerabilities reported, in the Trust Center.
      </MoreHelp>
    </TrustPage>
  );
}

// ── Data Residency ───────────────────────────────────────────────────────────────────────

const RESIDENCY = [
  // No hosting/database region is configured anywhere the app can read. Deployment files name
  // a region, but one is inert and the other deploys to a host from secrets — neither is
  // authoritative, so no location is shown.
  { label: "Application and database location", status: "unavailable",
    detail: "The region where ZoikoStream's application servers and database run is not recorded in the platform's configuration, so it is not shown here. Contact support for residency details." },
  // Organization.region is a free-text label (default "US East") set by platform staff;
  // media_comms.py says it is unrelated to media routing. It is not a residency control.
  { label: "Organization residency setting", status: "unavailable",
    detail: "ZoikoStream does not offer a per-organization data-residency control. The region label on Support & Status is a display setting; it does not decide where data is processed or stored." },
  // media_comms.REGION_LABEL: no LIVEKIT_REGION, LiveKit selects the ingest edge.
  { label: "Live media processing", status: "provider",
    detail: "Live audio and video are carried by LiveKit. No media region is configured; LiveKit selects the ingest edge for each stream, so it can vary." },
  // GCS_BUCKET has no location setting; privacy_comms seeds Google Cloud Storage, region None.
  { label: "Recordings and files", status: "unavailable",
    detail: "Recordings, exports and generated documents are kept in object storage (Google Cloud Storage). The storage location is not recorded in the platform's configuration." },
  // media_retention.RESIDUAL_COPIES: provider backups are outside the platform and not claimed.
  { label: "Backups", status: "unavailable",
    detail: "ZoikoStream does not publish a backup location or schedule. Backup retention inside the cloud storage provider is outside ZoikoStream's control." },
  // media_retention.py: retention_expires_at is a keep-guarantee; no automatic deletion.
  { label: "Recording retention", status: "in_place",
    detail: "Each finished recording carries a retention date from the platform's retention policy. Until then it is kept and cannot be deleted by an organization admin; after it, the recording becomes eligible for deletion. Nothing is deleted automatically." },
];

const DATA_KINDS = [
  {
    title: "Control-plane data",
    what: "Accounts, organization settings, events, registrations and audit records.",
    where: "Held in ZoikoStream's application database.",
  },
  {
    title: "Streaming and media data",
    what: "Live audio and video, and the recordings made from them.",
    where: "Live media flows through LiveKit; recordings are stored in object storage.",
  },
];

// The published processor list (GET /privacy/subprocessors — the same list the Privacy Center
// shows). A region appears only where one is recorded; today none is, and the table says so.
function Subprocessors() {
  const { data, loading, error, reload } = useApi(() =>
    api.get("/privacy/subprocessors").then((r) => r.data)
  );
  const rows = Array.isArray(data) ? data : [];
  const cell = "px-5 py-3 text-[13px] align-top";
  const head = cx("px-5 py-2.5 text-left text-[10px] font-semibold uppercase tracking-[0.1em]", CONSOLE.faint);

  return (
    <Panel
      title="Subprocessors"
      description="Third-party providers that process data for ZoikoStream, from the published subprocessor list. A region is shown only where one is recorded."
      flush
    >
      {loading && !data ? (
        <div className="space-y-3 px-5 pb-5" aria-label="Loading subprocessors">
          {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} variant="line" className="w-full" />)}
        </div>
      ) : error && !data ? (
        <div className="px-5 pb-5">
          <p className={cx("text-[13px]", CONSOLE.muted)}>Couldn’t load the subprocessor list.</p>
          <ConsoleButton variant="secondary" size="sm" className="mt-3" onClick={reload}>
            Try again
          </ConsoleButton>
        </div>
      ) : rows.length === 0 ? (
        <p className={cx("px-5 pb-5 text-[13px]", CONSOLE.muted)}>No subprocessors are currently listed.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px]">
            <thead>
              <tr className={cx("border-y", CONSOLE.divider)}>
                <th scope="col" className={head}>Provider</th>
                <th scope="col" className={head}>Service</th>
                <th scope="col" className={head}>Purpose</th>
                <th scope="col" className={head}>Region</th>
              </tr>
            </thead>
            <tbody className={cx("divide-y", CONSOLE.divideY)}>
              {rows.map((s) => (
                <tr key={s.name}>
                  <td className={cx(cell, "font-medium", CONSOLE.heading)}>{s.name}</td>
                  <td className={cx(cell, CONSOLE.body)}>{s.service}</td>
                  <td className={cx(cell, CONSOLE.muted)}>{s.processing_purpose}</td>
                  <td className={cx(cell, s.region ? CONSOLE.body : CONSOLE.faint)}>{s.region || "Not available"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function DataResidency() {
  return (
    <TrustPage
      title="Data Residency"
      subtitle="Where your organization's data is processed and stored — stated only where ZoikoStream records it."
    >
      <Panel title="Residency at a glance">
        <FactList facts={RESIDENCY} />
      </Panel>

      <Panel title="Control-plane and media data" description="The two kinds of data the platform handles, and where each goes.">
        <div className="grid gap-4 md:grid-cols-2">
          {DATA_KINDS.map((k) => (
            <div key={k.title} className={cx(CONSOLE.inset, "p-4")}>
              <p className={cx("text-[13px] font-semibold", CONSOLE.heading)}>{k.title}</p>
              <p className={cx("mt-1 text-[12px] leading-[18px]", CONSOLE.body)}>{k.what}</p>
              <p className={cx("mt-1 text-[12px] leading-[18px]", CONSOLE.muted)}>{k.where}</p>
              <p className={cx("mt-3 text-[12px]", CONSOLE.faint)}>
                Location: <span className="font-medium">Not available</span>
              </p>
            </div>
          ))}
        </div>
      </Panel>

      <Subprocessors />

      <MoreHelp>
        Contact support for residency details for a contract or review. To request a copy or the
        deletion of personal data, use the{" "}
        <Link to="/organization/privacy" className={cx("rounded font-semibold", CONSOLE.link, focusRing)}>
          Privacy Center
        </Link>
        .
      </MoreHelp>
    </TrustPage>
  );
}
