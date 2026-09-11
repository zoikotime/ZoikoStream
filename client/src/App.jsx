import { lazy } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation } from "react-router-dom";
import { ThemeProvider } from "./theme/ThemeContext";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { roleHome } from "./auth/roleHome";
import ProtectedRoute from "./components/ProtectedRoute";
import { PageSpinner } from "./ui/Spinner";
import RoleRoute from "./components/RoleRoute";
import EventConsoleRoute from "./components/EventConsoleRoute";
import MainLayout from "./layouts/MainLayout";
import OrganizationLayout from "./layouts/OrganizationLayout";
import AdminLayout from "./layouts/AdminLayout";
import Dashboard from "./pages/Dashboard";
import OrganizationDashboard from "./pages/organization/Dashboard";
import OrganizationEvents from "./pages/organization/Events";
import OrganizationRecordings from "./pages/organization/Recordings";
import OrganizationAnalytics from "./pages/organization/Analytics";
import OrganizationBilling from "./pages/organization/Billing";
import OrganizationSettings from "./pages/organization/Settings";
import EventDetails from "./pages/organization/EventDetails";
import InviteMembers from "./pages/organization/InviteMembers";
import AuthLayout from "./layouts/AuthLayout";
import Login from "./pages/auth/Login";
import CreateOrganization from "./pages/auth/CreateOrganization";
import ForgotPassword from "./pages/auth/ForgotPassword";
import AcceptInvitation from "./pages/auth/AcceptInvitation";
import VerifyEmail from "./pages/auth/VerifyEmail";
import EventRegistration from "./pages/EventRegistration";
import CustomerDelivery from "./pages/CustomerDelivery";
import HostDashboard from "./pages/host/Dashboard";
import EventWatch from "./pages/watch/EventWatch";
import SpeakerBackstage from "./pages/speaker/Backstage";
import Landing from "./pages/Landing";
import MyEvents from "./pages/MyEvents";
import Contact from "./pages/Contact";
import Status from "./pages/Status";
import Trust from "./pages/Trust";
import SecurityReport from "./pages/SecurityReport";
import EmailPreferences from "./pages/EmailPreferences";

// Super Admin console — code-split as one area. Only super admins can reach /admin/*, so
// shipping these 14 pages (plus their charts and tables) in the main bundle made every
// visitor to the public homepage download them. Same pattern as Home above.
const AdminDashboard = lazy(() => import("./pages/admin/Dashboard"));
const Organizations = lazy(() => import("./pages/admin/Organizations"));
const LiveEvents = lazy(() => import("./pages/admin/LiveEvents"));
const AdminEventDetail = lazy(() => import("./pages/admin/EventDetail"));
const AdminUsers = lazy(() => import("./pages/admin/Users"));
const Subscriptions = lazy(() => import("./pages/admin/Subscriptions"));
const Analytics = lazy(() => import("./pages/admin/Analytics"));
const AuditLogs = lazy(() => import("./pages/admin/AuditLogs"));
const PlatformSettings = lazy(() => import("./pages/admin/Settings"));
const SystemStatus = lazy(() => import("./pages/admin/SystemStatus"));
const FeatureFlags = lazy(() => import("./pages/admin/FeatureFlags"));
const ReleaseCenter = lazy(() => import("./pages/admin/ReleaseCenter"));
const Support = lazy(() => import("./pages/admin/Support"));
const Roles = lazy(() => import("./pages/admin/Roles"));
const Developers = lazy(() => import("./pages/admin/Developers"));
const EventReadiness = lazy(() => import("./pages/admin/EventReadiness"));
const AdminMedia = lazy(() => import("./pages/admin/Media"));
const TrustSafety = lazy(() => import("./pages/admin/Security"));
const AdminGovernance = lazy(() => import("./pages/admin/Governance"));
const Commerce = lazy(() => import("./pages/admin/Commerce"));
const Infrastructure = lazy(() => import("./pages/admin/Infrastructure"));

// Organization console — the eight Build/Operate/Manage pages, code-split for the same reason
// as the admin console above: only org admins reach /organization/*, so shipping them in the
// main bundle made every visitor to the public homepage download them. AppShell already
// provides the Suspense boundary these render inside.
// Organization & Workspaces is split too — it is the only screen carrying framer-motion,
// and eagerly importing it put that library in the bundle every homepage visitor downloads.
const OrganizationProfile = lazy(() => import("./pages/organization/Profile"));
const DeveloperPlatform = lazy(() => import("./pages/organization/DeveloperPlatform"));
const Credentials = lazy(() => import("./pages/organization/Credentials"));
const LiveInputs = lazy(() => import("./pages/organization/LiveInputs"));
const StreamingSessions = lazy(() => import("./pages/organization/StreamingSessions"));
const PlaybackAccess = lazy(() => import("./pages/organization/PlaybackAccess"));
const AudienceAccess = lazy(() => import("./pages/organization/AudienceAccess"));
const SupportStatus = lazy(() => import("./pages/organization/SupportStatus"));

// ponytail: one placeholder for routes not built yet — replace each with a real page as it lands
function Placeholder({ title }) {
  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{title}</h1>
      <p className="mt-2 text-sm text-slate-500 dark:text-neutral-400">Coming soon.</p>
    </div>
  );
}

// Send unknown URLs to the user's own dashboard (or login), not a hardcoded page.
// Roles without an app home (viewers) fall through to the public homepage.
function RootRedirect() {
  const { user, loading } = useAuth();
  // `loading` is now real (AuthContext validates the token against /auth/me), so this window
  // is a live moment rather than a never-taken branch. A spinner, not null: deciding WHERE to
  // send someone depends on their server-confirmed role, and a blank screen while that
  // resolves reads as a broken page.
  if (loading) return <PageSpinner />;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={roleHome(user.role) || "/"} replace />;
}

// "/" shows the public landing page to visitors and to roles without an app dashboard
// (viewers); logged-in staff/admins go to their dashboard.
function LandingOrDashboard() {
  const { user, loading } = useAuth();
  // Only ever true when a token exists (see AuthContext) — a signed-out visitor renders the
  // landing page on the first paint with no spinner and no flash.
  if (loading) return <PageSpinner />;
  const home = user && roleHome(user.role);
  if (home) return <Navigate to={home} replace />;
  return <Landing />;
}

// The retired moderator console's URL. Forwards to the host console, carrying ?event=<id>
// through so an old assignment email still opens the RIGHT event rather than the bare
// dashboard. `replace` so the dead URL doesn't sit in the browser's back history.
function LegacyModeratorRedirect() {
  const { search } = useLocation();
  return <Navigate to={`/host/dashboard${search}`} replace />;
}

// Super Admin sidebar destinations without a page yet — kept in-layout (Placeholder)
// so the console nav never 404s. Everything else in the sidebar is a real page below.
const adminStubs = [];

// Legacy generic dashboard (speaker/viewer land here until they get their own).
const legacyStubs = [
  ["events", "Events"], ["speakers", "Speakers"], ["viewers", "Viewers"],
  ["recordings", "Recordings"], ["analytics", "Analytics"], ["users", "Users"],
  ["invitations", "Invitations"], ["billing", "Billing"], ["settings", "Settings"],
];

export default function App() {
  return (
    <ThemeProvider>
      {/* BrowserRouter wraps AuthProvider (not the other way round) so the provider can
          navigate on logout and on a session that expires mid-visit. Nothing outside
          <Routes> consumes auth, so the swap changes nothing else. */}
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            {/* Public landing page */}
            <Route path="/" element={<LandingOrDashboard />} />

            {/* Public contact form — where the landing page's "Talk to an expert" goes.
                Deliberately NOT behind LandingOrDashboard: a signed-in operator should still
                be able to reach it without being bounced to their dashboard. */}
            <Route path="/contact" element={<Contact />} />

            {/* Public status page. Unauthenticated by design and NOT behind
                LandingOrDashboard: it is what every STS-001..006 email links to, and a
                status page that needs a session is useless during an outage that stops
                people signing in. It also carries the subscription confirm/manage
                links (/status?confirm=..., /status?t=...). */}
            <Route path="/status" element={<Status />} />

            {/* Trust Center and the marketing preference centre. Public and NOT
                behind LandingOrDashboard, for the same reason /status is not: a
                security researcher has no account here, a vendor-security reviewer
                at a prospect does not either, and requiring a login to unsubscribe
                is what makes people report mail as spam instead. Every one of these
                paths is the CTA of an email we send, so a missing route here is a
                dead link in a security advisory. */}
            <Route path="/trust" element={<Trust />} />
            <Route path="/security/report/:reference" element={<SecurityReport />} />
            <Route path="/preferences" element={<EmailPreferences mode="preferences" />} />
            <Route path="/unsubscribe" element={<EmailPreferences mode="unsubscribe" />} />

            {/* Authentication — one login for every role; brand panel shared via AuthLayout.
                All dummy: no API calls. After login, roleHome() picks the dashboard. */}
            <Route element={<AuthLayout />}>
              <Route path="/login" element={<Login />} />
              <Route path="/signup" element={<CreateOrganization />} />
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/accept-invite" element={<AcceptInvitation />} />
              {/* IDN-001 — landing page for the emailed verification link. */}
              <Route path="/verify-email" element={<VerifyEmail />} />
            </Route>

            {/* Public event registration landing (shareable link) */}
            <Route path="/e/:id" element={<EventRegistration />} />

            {/* Controlled customer export / post-event report — token-gated, unauthenticated.
                The recipient is never a platform user (BRD LE-AC-18). */}
            <Route path="/deliveries/:token" element={<CustomerDelivery />} />

            {/* Viewer Portal — attendee watch page from an invite link (public) */}
            <Route path="/events/:eventId/watch" element={<EventWatch />} />

            {/* Host broadcasting studio — standalone full-screen page (own header/sidebar).
                Unauthenticated -> /login; wrong role -> their own home. Assignment to a
                *specific* event is still enforced server-side (canHost/canModerate from
                resolve_ctx) — this only stops an anonymous or wrong-role visitor from ever
                loading the console in the first place.

                The separate moderator console that used to live here is gone: it was never
                more than a second layout over the SAME hook and the SAME
                components/moderation/* widgets the host console already composes as tabs
                (see components/host/HostPanel.jsx), so removing it took no capability with
                it.

                "moderator" stays in this allow-list on purpose, as a TRANSITIONAL entry.
                It is not a capability — this guard only decides who may load the page shell;
                every control inside is gated on canModerate/canHost from the server's
                snapshot, so a legacy moderator sees exactly what resolve_ctx says they may
                do and no more. It is here because roleHome() maps the retired role to this
                same path: dropping it from the list while roleHome points here would make
                RoleRoute redirect the role to a route that rejects it, i.e. an infinite
                redirect loop. Remove BOTH together once the backend's
                retire_moderator_role.py has run everywhere and no `users.role` row carries
                the value. */}
            <Route element={<RoleRoute allow={["host", "moderator", "org_admin", "super_admin"]} />}>
              {/* RoleRoute (outer) answers "may this ACCOUNT use consoles at all".
                  EventConsoleRoute answers the question it cannot: "does this person run
                  THIS event?" — a server-confirmed EventAssignment, checked per event id.
                  Without it a host-persona account reached the Producer Console for an
                  event it had no claim on, read-only, which was the reported bug.

                  There is deliberately no /moderator/dashboard route inside this guard: the
                  separate moderator console was retired (LegacyModeratorRedirect above
                  forwards its old URL here, carrying ?event=<id>), so the host console is
                  the only event console this group protects. */}
              <Route element={<EventConsoleRoute capability={["can_host", "can_moderate"]} />}>
                <Route path="/host/dashboard" element={<HostDashboard />} />
              </Route>
            </Route>

            {/* Compatibility redirect, not a route: assignment-notification emails sent
                before the role was retired still point at /moderator/dashboard?event=<id>,
                and so do bookmarks. Dropping the path outright would send those to the
                catch-all and lose the event id, so this forwards to the host console with
                the query string intact. Deliberately OUTSIDE the RoleRoute above — an
                unauthenticated visitor should be redirected and then asked to sign in by
                that route, which preserves the destination through login (see RoleRoute). */}
            <Route path="/moderator/dashboard" element={<LegacyModeratorRedirect />} />

            {/* Contributor (speaker) backstage — same standalone-page pattern as the host
                console above. Assignment to a *specific* event as a speaker is enforced
                server-side (can_contribute from resolve_ctx); this route gate only stops a
                wrong-role visitor from loading the page shell. */}
            <Route element={<RoleRoute allow={["speaker", "org_admin", "super_admin"]} />}>
              <Route element={<EventConsoleRoute capability="can_contribute" />}>
                <Route path="/speaker/backstage" element={<SpeakerBackstage />} />
              </Route>
            </Route>

            {/* Super admin (platform) area */}
            <Route element={<RoleRoute allow={["super_admin"]} />}>
              <Route element={<AdminLayout />}>
                <Route path="/admin/dashboard" element={<AdminDashboard />} />
                <Route path="/admin/organizations" element={<Organizations />} />
                <Route path="/admin/live-events" element={<LiveEvents />} />
                <Route path="/admin/live-events/:eventId" element={<AdminEventDetail />} />
                <Route path="/admin/users" element={<AdminUsers />} />
                <Route path="/admin/subscriptions" element={<Subscriptions />} />
                <Route path="/admin/analytics" element={<Analytics />} />
                <Route path="/admin/audit" element={<AuditLogs />} />
                <Route path="/admin/settings" element={<PlatformSettings />} />
                <Route path="/admin/status" element={<SystemStatus />} />
                <Route path="/admin/feature-flags" element={<FeatureFlags />} />
                <Route path="/admin/releases" element={<ReleaseCenter />} />
                <Route path="/admin/support" element={<Support />} />
                <Route path="/admin/roles" element={<Roles />} />
                <Route path="/admin/developers" element={<Developers />} />
                <Route path="/admin/event-readiness" element={<EventReadiness />} />
                <Route path="/admin/media" element={<AdminMedia />} />
                <Route path="/admin/security" element={<TrustSafety />} />
                <Route path="/admin/governance" element={<AdminGovernance />} />
                <Route path="/admin/commerce" element={<Commerce />} />
                <Route path="/admin/infrastructure" element={<Infrastructure />} />
                {adminStubs.map(([path, title]) => (
                  <Route key={path} path={`/admin/${path}`} element={<Placeholder title={title} />} />
                ))}
              </Route>
            </Route>

            {/* The organization dashboard is open to ANY member of the organization, not
                just admins — which is what stops the bounce loop that produced the reported
                bug. When it was admin-only, a host/moderator/speaker/billing_admin/viewer
                account that asked for it was rejected by RoleRoute and sent to its own
                accountHome; with the host persona's home being an event console, that landed
                them in a Producer Console for an event they were not assigned to.

                It is also what the BACKEND already allows: /organization/overview and
                /console-state authorize with `get_my_org` (any member), not with
                require_org_admin, so this closes a gap between the two rather than opening
                one. Every genuinely admin-only page stays in the admin group below. */}
            <Route element={<ProtectedRoute />}>
              <Route element={<OrganizationLayout />}>
                <Route path="/organization/dashboard" element={<OrganizationDashboard />} />
              </Route>
            </Route>

            {/* Organization admin area */}
            <Route element={<RoleRoute allow={["org_admin"]} />}>
              <Route element={<OrganizationLayout />}>
                <Route path="/organization/events" element={<OrganizationEvents />} />
                <Route path="/organization/recordings" element={<OrganizationRecordings />} />
                <Route path="/organization/analytics" element={<OrganizationAnalytics />} />
                <Route path="/organization/billing" element={<OrganizationBilling />} />
                <Route path="/organization/settings" element={<OrganizationSettings />} />
                <Route path="/organization/events/:id" element={<EventDetails />} />
                <Route path="/organization/users" element={<InviteMembers />} />
                <Route path="/organization/profile" element={<OrganizationProfile />} />
                {/* Build */}
                <Route path="/organization/developers" element={<DeveloperPlatform />} />
                <Route path="/organization/credentials" element={<Credentials />} />
                <Route path="/organization/live-inputs" element={<LiveInputs />} />
                {/* Operate */}
                <Route path="/organization/sessions" element={<StreamingSessions />} />
                <Route path="/organization/playback" element={<PlaybackAccess />} />
                <Route path="/organization/audience" element={<AudienceAccess />} />
                {/* Manage */}
                <Route path="/organization/support" element={<SupportStatus />} />
              </Route>
            </Route>

            {/* Legacy generic dashboard for other roles (moved off "/" so the homepage can live there) */}
            {/* Where a host/moderator/speaker PERSONA lands after login. Their account role
                says what they do, not which event they run, so they choose from the events
                they are actually assigned to. */}
            <Route element={<ProtectedRoute />}>
              <Route path="/events/mine" element={<MyEvents />} />
            </Route>

            <Route element={<ProtectedRoute />}>
              <Route element={<MainLayout />}>
                <Route path="/dashboard" element={<Dashboard />} />
                {legacyStubs.map(([path, title]) => (
                  <Route key={path} path={path} element={<Placeholder title={title} />} />
                ))}
              </Route>
            </Route>

            <Route path="*" element={<RootRedirect />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}
