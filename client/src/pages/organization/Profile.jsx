import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { MotionConfig, motion } from "framer-motion";
import {
  AtSign, BadgeCheck, Building2, CreditCard, Database, Gauge, Layers, Mail,
  RefreshCw, ShieldCheck, TerminalSquare, User, Users,
} from "lucide-react";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { useAuth } from "../../auth/AuthContext";
import { cx, focusRing } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import { useOrgScope } from "../../components/organization/orgScope";
import ProfileHeader from "../../components/organization/profile/ProfileHeader";
import MetricCard from "../../components/organization/profile/MetricCard";
import InfoCard from "../../components/organization/profile/InfoCard";
import QuickActions from "../../components/organization/profile/QuickActions";
import ActivityTimeline from "../../components/organization/profile/ActivityTimeline";
import SecurityPanel from "../../components/organization/profile/SecurityPanel";
import Section from "../../components/organization/profile/Section";
import { CARD, TXT } from "../../components/organization/profile/styles";
import { inView, stagger } from "../../components/organization/profile/motion";
import {
  activityFeed, entitlement, fmtDate, fmtGB, fmtNum, roleLabel, securityPosture, usageTone,
} from "../../components/organization/profile/derive";

// Organization & Workspaces (/organization/profile) — the org admin's identity screen:
// who they are, what their organization is entitled to, what has been happening in the
// workspace, and the actions they reach for from here.
//
// EVERY figure on this page comes from an endpoint the console already serves:
//   /organization/console-state  — org, workspace list and service health (fetched once by
//                                  OrganizationLayout; read here through OrgScopeContext,
//                                  so this page adds no request for it)
//   /organization/overview       — entitlements (members/storage/hours), developer_ops, sessions
//   /organization/security       — the controls the posture score is computed from
//   /organization/domain         — domain verification (one posture check)
//   /organization/users          — member roster; also the caller's own joined-on date
//   /organization/invitations    — invitations, for the activity feed
//
// Nothing here was invented to fill a card. Where the platform genuinely doesn't measure
// something (per-organization API request volume; an account audit trail behind "you
// changed your password"), the surface states the gap instead of showing a placeholder
// number that would read as real.

// The four side requests are individually caught: a 403 or a blip on any one of them
// should degrade a single card, not blank the page. `overview` is the only required
// call — it is readable by every member and everything structural depends on it.
const soft = (p) => p.then((r) => r.data).catch(() => null);

const loadProfile = () =>
  Promise.all([
    api.get("/organization/overview", { params: { range: "30d" } }).then((r) => r.data),
    soft(api.get("/organization/security")),
    soft(api.get("/organization/domain")),
    soft(api.get("/organization/users", { params: { page_size: 100 } })),
    soft(api.get("/organization/invitations", { params: { page_size: 50 } })),
  ]).then(([overview, security, domain, users, invitations]) => ({
    overview, security, domain, users, invitations,
  }));

const PLAN_TONE = { active: "success", trial: "warning", past_due: "danger" };

export default function OrganizationProfile() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const { state, reload: reloadShell } = useOrgScope();
  const { data, loading, error, reload } = useApi(loadProfile);

  const overview = data?.overview;
  const ent = overview?.entitlements;
  const members = entitlement(overview, "Members");
  const storage = entitlement(overview, "Storage");
  const workspaces = state?.workspaces || [];

  // The caller's own member row carries the joined-on date and the live active flag —
  // neither is in the JWT-backed session object, so they come from the roster.
  const me = useMemo(
    () => (data?.users?.items || []).find((u) => u.email === user?.email) || null,
    [data, user]
  );

  const posture = useMemo(
    () => securityPosture({ security: data?.security, domain: data?.domain }),
    [data]
  );

  const activity = useMemo(
    () =>
      activityFeed({
        users: data?.users?.items || [],
        invitations: data?.invitations?.items || [],
        sessions: overview?.sessions?.items || [],
      }),
    [data, overview]
  );

  const refreshing = loading && Boolean(data);

  const refreshAll = () => {
    reload();
    reloadShell?.();
  };

  // No signed-in password change exists in this stack; the emailed one-time code at
  // /forgot-password is the real flow, so the button goes there instead of nowhere.
  const changePassword = () => navigate("/forgot-password");

  return (
    <MotionConfig reducedMotion="user">
      <div className="mx-auto max-w-7xl space-y-8 pb-4">
        <ProfileHeader
          user={user}
          organization={state?.organization || overview?.organization}
          workspace={state?.workspace || overview?.workspace}
          workspaceCount={workspaces.length}
          health={state?.health || overview?.service_health}
          plan={ent?.plan || state?.organization?.plan}
          memberSince={me?.created_at}
          loading={loading && !data}
          onChangePassword={changePassword}
        />

        {error && (
          <OrganizationErrorState
            error={error}
            onRetry={reload}
            title="Couldn't load organization metrics"
          />
        )}

        {/* ── Organization Overview ─────────────────────────────────────────── */}
        <Section
          title="Organization Overview"
          description="Live figures for this workspace, measured against your subscribed plan."
          action={
            <button
              type="button"
              onClick={refreshAll}
              disabled={loading}
              className={cx(
                "inline-flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-[13px] font-medium text-slate-600 transition-colors duration-150 hover:bg-slate-50 hover:text-slate-900 disabled:opacity-50 dark:border-white/10 dark:bg-white/[0.03] dark:text-neutral-300 dark:hover:bg-white/[0.07] dark:hover:text-white",
                focusRing
              )}
            >
              <RefreshCw
                className={cx("h-3.5 w-3.5", refreshing && "animate-spin motion-reduce:animate-none")}
                aria-hidden="true"
              />
              Refresh
            </button>
          }
        >
          <motion.div
            variants={stagger(0.06)}
            initial="hidden"
            whileInView="show"
            viewport={inView}
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-6"
          >
            <MetricCard
              icon={Users}
              tone="violet"
              label="Members"
              value={fmtNum(members?.used ?? data?.users?.total)}
              sub={members?.limit ? `of ${fmtNum(members.limit)} seats on ${ent?.plan || "your plan"}` : "No seat ceiling on this plan"}
              percent={members?.percent ?? null}
              to="/organization/users"
              loading={loading && !data}
            />

            <MetricCard
              icon={Layers}
              tone="indigo"
              label="Active Workspaces"
              value={fmtNum(workspaces.length || null)}
              sub={workspaces.map((w) => w.label).join(" · ") || "—"}
              badge={<Badge tone="brand">Default</Badge>}
              note="This platform runs one production workspace per organization; additional workspaces are not modelled yet."
              to="/organization/dashboard"
              loading={loading && !data}
            />

            <MetricCard
              icon={TerminalSquare}
              tone="blue"
              label="API Credentials"
              value={fmtNum(overview?.developer_ops?.credentials_active)}
              sub={`active of ${fmtNum(overview?.developer_ops?.credentials_total ?? 0)} issued · ${fmtNum(overview?.developer_ops?.webhooks_configured ?? 0)} webhooks`}
              note="Request volume and success rate are not attributed per organization, so no usage figure is shown."
              to="/organization/credentials"
              loading={loading && !data}
            />

            <MetricCard
              icon={Database}
              tone={usageTone(storage?.percent)}
              label="Storage Used"
              value={fmtGB(storage?.used)}
              sub={
                storage?.limit
                  ? `of ${fmtGB(storage.limit)} included`
                  : `${fmtNum(overview?.media_assets?.ready ?? 0)} recordings ready`
              }
              percent={storage?.percent ?? null}
              to="/organization/recordings"
              loading={loading && !data}
            />

            <MetricCard
              icon={ShieldCheck}
              tone={posture.score == null ? "slate" : posture.score >= 85 ? "emerald" : posture.score >= 60 ? "amber" : "rose"}
              label="Security Score"
              value={posture.score == null ? "—" : posture.score}
              unit={posture.score == null ? undefined : "/ 100"}
              sub={
                posture.score == null
                  ? "Security settings unavailable"
                  : `${posture.level} · ${posture.passed} of ${posture.total} controls enabled`
              }
              subTone={
                posture.score == null ? "muted" : posture.score >= 85 ? "emerald" : posture.score >= 60 ? "amber" : "rose"
              }
              percent={posture.score}
              percentLabel="of maximum"
              to="/organization/settings?tab=security"
              loading={loading && !data}
            />

            <MetricCard
              icon={CreditCard}
              tone="amber"
              label="Billing Status"
              value={ent?.plan || "No plan"}
              badge={
                ent?.status ? (
                  <Badge tone={PLAN_TONE[ent.status] || "neutral"} dot>
                    {ent.status.replace("_", " ")}
                  </Badge>
                ) : null
              }
              sub={
                ent?.trial_ends_at
                  ? `Trial ends ${fmtDate(ent.trial_ends_at)}`
                  : ent?.current_period_end
                    ? `Renews ${fmtDate(ent.current_period_end)}`
                    : "No active subscription period"
              }
              to="/organization/billing"
              loading={loading && !data}
            />
          </motion.div>
        </Section>

        {/* ── Personal Information ──────────────────────────────────────────── */}
        <Section
          title="Personal Information"
          description="Your account within this organization. Hover a field to copy it."
        >
          <motion.div
            variants={stagger(0.05)}
            initial="hidden"
            whileInView="show"
            viewport={inView}
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
          >
            <InfoCard icon={User} tone="violet" label="Full Name" value={user?.full_name} />
            <InfoCard icon={AtSign} tone="indigo" label="Username" value={user?.username} copy />
            <InfoCard icon={Mail} tone="blue" label="Email Address" value={user?.email} copy />
            <InfoCard icon={BadgeCheck} tone="emerald" label="Role" value={roleLabel(user?.role)} />
            <InfoCard
              icon={Building2}
              tone="amber"
              label="Organization"
              value={state?.organization?.name || user?.organization_name}
              copy
            />
            <InfoCard
              icon={Gauge}
              tone={me && me.is_active === false ? "rose" : "emerald"}
              label="Account Status"
              value={me && me.is_active === false ? "Inactive" : "Active"}
              loading={loading && !data}
              trailing={
                <Badge tone={me && me.is_active === false ? "danger" : "success"} dot>
                  {me && me.is_active === false ? "Suspended" : "Live"}
                </Badge>
              }
            />
          </motion.div>
        </Section>

        {/* ── Recent Activity + Security ────────────────────────────────────── */}
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)]">
          <Section
            title="Recent Activity"
            description="Member, invitation and streaming events across this workspace."
          >
            <div className={cx(CARD, "p-6")}>
              <ActivityTimeline entries={activity} loading={loading && !data} />
              {!loading && activity.length > 0 && (
                <p className={cx("mt-6 border-t border-slate-100 pt-4 text-[11px] leading-4 dark:border-white/10", TXT.faint)}>
                  Built from member, invitation and session records. Profile edits and password
                  changes are not listed — this platform keeps no account audit trail for them yet.
                </p>
              )}
            </div>
          </Section>

          <Section title="Security" description="Credentials and organization posture.">
            <SecurityPanel
              posture={posture}
              loading={loading && !data}
              onChangePassword={changePassword}
            />
          </Section>
        </div>

        {/* ── Quick Actions ─────────────────────────────────────────────────── */}
        <Section
          title="Quick Actions"
          description="The screens an organization owner reaches for most."
        >
          <QuickActions />
        </Section>
      </div>
    </MotionConfig>
  );
}
