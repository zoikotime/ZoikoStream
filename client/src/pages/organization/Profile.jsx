import { useMemo } from "react";

import { MotionConfig, motion } from "framer-motion";
import { AtSign, BadgeCheck, Building2, Gauge, Mail, User } from "lucide-react";
import api from "../../api";
import useApi from "../../hooks/useApi";
import { useAuth } from "../../auth/AuthContext";
import { cx } from "../../ui/tokens";
import Badge from "../../ui/Badge";
import OrganizationErrorState from "../../components/organization/OrganizationErrorState";
import { useOrgScope } from "../../components/organization/orgScope";
import ProfileHeader from "../../components/organization/profile/ProfileHeader";
import InfoCard from "../../components/organization/profile/InfoCard";
import ActivityTimeline from "../../components/organization/profile/ActivityTimeline";
import Section from "../../components/organization/profile/Section";
import { CARD, TXT } from "../../components/organization/profile/styles";
import { inView, stagger } from "../../components/organization/profile/motion";
import { activityFeed, roleLabel } from "../../components/organization/profile/derive";

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

export default function OrganizationProfile() {
  const { user } = useAuth();
  // `quickActions: true` puts the Quick Actions menu in the topbar for this page, in the
  // slot the health pill occupies elsewhere. Asked for here rather than set in the layout
  // so no other org page loses its live health verdict.
  const { state } = useOrgScope({ quickActions: true });
  const { data, loading, error, reload } = useApi(loadProfile);

  const overview = data?.overview;
  const ent = overview?.entitlements;
  const workspaces = state?.workspaces || [];

  // The caller's own member row carries the joined-on date and the live active flag —
  // neither is in the JWT-backed session object, so they come from the roster.
  const me = useMemo(
    () => (data?.users?.items || []).find((u) => u.email === user?.email) || null,
    [data, user]
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
        />

        {error && (
          <OrganizationErrorState
            error={error}
            onRetry={reload}
            title="Couldn't load organization metrics"
          />
        )}

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

        {/* ── Recent Activity ───────────────────────────────────────────────────
            The Security panel used to sit in a 400px column beside this. It has moved to
            Settings -> Security, where the controls the score is computed FROM already live
            — a posture readout on one page and the switches that change it on another is a
            split nobody benefits from. The grid went with it rather than being left as an
            empty track. */}
        <div>
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
        </div>

        {/* The Quick Actions card grid that used to close this page is gone: the topbar now
            carries the same seven destinations (components/organization/quickActions.js), and
            two copies of one shortcut set on one screen is repetition, not emphasis. The
            grid component itself is retained for any page that wants the expanded form. */}
      </div>
    </MotionConfig>
  );
}
