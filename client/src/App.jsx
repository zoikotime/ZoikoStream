import { lazy, Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { ThemeProvider } from "./theme/ThemeContext";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { roleHome } from "./auth/roleHome";
import ProtectedRoute from "./components/ProtectedRoute";
import RoleRoute from "./components/RoleRoute";
import MainLayout from "./layouts/MainLayout";
import OrganizationLayout from "./layouts/OrganizationLayout";
import AdminLayout from "./layouts/AdminLayout";
import Dashboard from "./pages/Dashboard";
import AuthLayout from "./layouts/AuthLayout";
import Login from "./pages/auth/Login";
import CreateOrganization from "./pages/auth/CreateOrganization";
import ForgotPassword from "./pages/auth/ForgotPassword";
import AcceptInvitation from "./pages/auth/AcceptInvitation";
import EventRegistration from "./pages/EventRegistration";
import HostDashboard from "./pages/host/Dashboard";
import ModeratorDashboard from "./pages/moderator/Dashboard";
import SpeakerDashboard from "./pages/speaker/Dashboard";
import AttendeeDashboard from "./pages/attendee/Dashboard";

// Host live control room — code-split because it pulls in livekit-client for publishing,
// which the host's landing page never needs.
const HostControlRoom = lazy(() => import("./pages/host/ControlRoom"));

// Moderation Center — code-split for the same reason as the org/admin areas: it pulls in nine
// live panels a moderator only needs once they open an event, and the landing page above is
// what they actually arrive on.
const ModeratorConsole = lazy(() => import("./pages/moderator/Console"));

// Speaker session console — code-split because it pulls in livekit-client to PUBLISH (a speaker
// now gets a source-scoped grant, see services/speaker.py) plus the presenter and whiteboard
// surfaces, none of which the landing page needs.
const SpeakerConsole = lazy(() => import("./pages/speaker/Console"));

// Organization console — code-split as one area, same reasoning as the admin console below.
// Only org admins can reach /organization/*, so shipping these nine pages (plus recharts and
// the event form) in the main bundle made every visitor to the public homepage download
// them. AppShell already provides the Suspense boundary both consoles render inside.
const OrganizationDashboard = lazy(() => import("./pages/organization/Dashboard"));
const OrganizationEvents = lazy(() => import("./pages/organization/Events"));
const EventDetails = lazy(() => import("./pages/organization/EventDetails"));
const OrganizationRecordings = lazy(() => import("./pages/organization/Recordings"));
const OrganizationAnalytics = lazy(() => import("./pages/organization/Analytics"));
const OrganizationBilling = lazy(() => import("./pages/organization/Billing"));
const OrganizationSettings = lazy(() => import("./pages/organization/Settings"));
const OrganizationProfile = lazy(() => import("./pages/organization/Profile"));
const InviteMembers = lazy(() => import("./pages/organization/InviteMembers"));
const OrganizationInvitations = lazy(() => import("./pages/organization/Invitations"));

// Public marketing homepage — code-split from the app bundle.
const Home = lazy(() => import("./pages/Home/Home"));

// Attendee watch page — code-split for the same reason as the two areas below: it is one
// standalone route reached from an event link, and everyone else pays for it in the main
// bundle otherwise. (livekit-client is split again inside it, and only downloads when
// playback actually starts.)
const EventWatch = lazy(() => import("./pages/watch/EventWatch"));

// Super Admin console — code-split as one area. Only super admins can reach /admin/*, so
// shipping these 14 pages (plus their charts and tables) in the main bundle made every
// visitor to the public homepage download them. Same pattern as Home above.
const AdminDashboard = lazy(() => import("./pages/admin/Dashboard"));
const Organizations = lazy(() => import("./pages/admin/Organizations"));
const LiveEvents = lazy(() => import("./pages/admin/LiveEvents"));
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

// ponytail: one placeholder for routes not built yet — replace each with a real page as it lands
function Placeholder({ title }) {
  return (
    <div>
      <h1 className="text-2xl font-bold text-slate-900 dark:text-white">{title}</h1>
      <p className="mt-2 text-sm text-slate-500 dark:text-neutral-400">Coming soon.</p>
    </div>
  );
}

// Send unknown URLs to the user's own dashboard (or login), not a hardcoded page. Every role
// has a home now (see auth/roleHome), so the `|| "/"` fallback only covers an unknown role.
function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={roleHome(user.role) || "/"} replace />;
}

// "/" shows the public homepage to VISITORS; anybody signed in goes to their own dashboard —
// attendees included, now that they have one.
function LandingOrDashboard() {
  const { user, loading } = useAuth();
  if (loading) return null;
  const home = user && roleHome(user.role);
  if (home) return <Navigate to={home} replace />;
  return (
    <Suspense fallback={<div className="min-h-screen bg-slate-950" />}>
      <Home />
    </Suspense>
  );
}

// Super Admin sidebar destinations without a page yet — kept in-layout (Placeholder)
// so the console nav never 404s. Everything else in the sidebar is a real page below.
const adminStubs = [
  ["infrastructure", "Media Infrastructure"],
  ["security", "Trust & Safety"],
  ["media", "Media"],
  ["event-readiness", "Event Readiness"],
  ["governance", "Governance"],
];

// Organization console destinations without a page yet — kept in-layout (Placeholder) so the
// org nav never 404s. Everything else in that sidebar is a real page below.
const orgStubs = [
  ["developers", "Developer Platform"],
  ["credentials", "Credentials"],
  ["webhooks", "Webhooks"],
  ["live-inputs", "Live Inputs"],
  ["sessions", "Streaming Sessions"],
  ["playback", "Playback & Access"],
  ["audience", "Audience Access"],
  ["support", "Support & Status"],
];

// Legacy generic dashboard. Both speaker and viewer have their own pages now; these paths stay
// so old links and the sidebar never 404.
const legacyStubs = [
  ["events", "Events"], ["speakers", "Speakers"], ["viewers", "Viewers"],
  ["recordings", "Recordings"], ["analytics", "Analytics"], ["users", "Users"],
  ["invitations", "Invitations"], ["billing", "Billing"], ["settings", "Settings"],
];

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            {/* Public marketing homepage */}
            <Route path="/" element={<LandingOrDashboard />} />

            {/* Authentication — one login for every role; brand panel shared via AuthLayout.
                All dummy: no API calls. After login, roleHome() picks the dashboard. */}
            <Route element={<AuthLayout />}>
              <Route path="/login" element={<Login />} />
              <Route path="/signup" element={<CreateOrganization />} />
              <Route path="/forgot-password" element={<ForgotPassword />} />
              <Route path="/accept-invitation" element={<AcceptInvitation />} />
            </Route>

            {/* Public event registration landing (shareable link) */}
            <Route path="/e/:id" element={<EventRegistration />} />

            {/* Viewer Portal — attendee dashboard + watch page. Signed in, any role: the SERVER
                decides who may see an event (services/viewer.access_for — org members always, plus
                anyone for a public/unlisted event), so this only enforces "has a session".
                Gating it to role=viewer here would lock organizers out of their own event, and
                every other role legitimately attends events too. */}
            <Route element={<ProtectedRoute />}>
              <Route path="/attendee/dashboard" element={<AttendeeDashboard />} />
              <Route
                path="/events/:eventId/watch"
                element={
                  // Own boundary: this page is standalone (no AppShell, which is where the
                  // console areas get theirs). Fallback matches the page's own true-black
                  // surface so the split doesn't flash a different background.
                  <Suspense fallback={<div className="min-h-screen bg-slate-50 dark:bg-black" />}>
                    <EventWatch />
                  </Suspense>
                }
              />
            </Route>

            {/* Host broadcasting studio — standalone full-screen page (own header/sidebar).
                Gated to roles that can actually run a broadcast. The socket enforces the real
                rule (an assignment on THAT event, services/moderation.resolve_ctx); this stops
                anonymous visitors loading the console shell at all. */}
            <Route element={<RoleRoute allow={["host", "org_admin", "super_admin"]} />}>
              {/* Landing: the events this host is assigned to run. roleHome sends them here. */}
              <Route path="/host/dashboard" element={<HostDashboard />} />
              {/* The broadcast surface, opened per event from the landing page. Code-split: it
                  pulls in livekit-client for publishing, which the landing page never needs. */}
              <Route
                path="/host/live"
                element={
                  <Suspense fallback={<div className="min-h-screen bg-slate-50 dark:bg-slate-950" />}>
                    <HostControlRoom />
                  </Suspense>
                }
              />
            </Route>

            {/* Moderator area — same split as the host's, for the same reason: the landing page
                shows the events you're responsible for, /moderator/live is the console for one
                of them. A host may also moderate, so both roles are allowed through;
                per-event authority is still the socket's decision (resolve_ctx). */}
            <Route element={<RoleRoute allow={["moderator", "host", "org_admin", "super_admin"]} />}>
              <Route path="/moderator/dashboard" element={<ModeratorDashboard />} />
              <Route
                path="/moderator/live"
                element={
                  <Suspense fallback={<div className="min-h-screen bg-slate-50 dark:bg-slate-950" />}>
                    <ModeratorConsole />
                  </Suspense>
                }
              />
            </Route>

            {/* Speaker / panellist area. Same split again: the landing page is the green room
                (schedule, files, device check), /speaker/live is the session. Hosts and
                moderators are allowed through because they present their own slides — the socket
                still decides per-event authority (resolve_ctx sets can_speak from the
                assignment). */}
            <Route element={<RoleRoute allow={["speaker", "moderator", "host", "org_admin", "super_admin"]} />}>
              <Route path="/speaker/dashboard" element={<SpeakerDashboard />} />
              <Route
                path="/speaker/live"
                element={
                  <Suspense fallback={<div className="min-h-screen bg-slate-50 dark:bg-slate-950" />}>
                    <SpeakerConsole />
                  </Suspense>
                }
              />
            </Route>

            {/* Super admin (platform) area */}
            <Route element={<RoleRoute allow={["super_admin"]} />}>
              <Route element={<AdminLayout />}>
                <Route path="/admin/dashboard" element={<AdminDashboard />} />
                <Route path="/admin/organizations" element={<Organizations />} />
                <Route path="/admin/live-events" element={<LiveEvents />} />
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
                {adminStubs.map(([path, title]) => (
                  <Route key={path} path={`/admin/${path}`} element={<Placeholder title={title} />} />
                ))}
              </Route>
            </Route>

            {/* Organization admin area */}
            <Route element={<RoleRoute allow={["org_admin"]} />}>
              <Route element={<OrganizationLayout />}>
                <Route path="/organization/dashboard" element={<OrganizationDashboard />} />
                <Route path="/organization/events" element={<OrganizationEvents />} />
                <Route path="/organization/recordings" element={<OrganizationRecordings />} />
                <Route path="/organization/analytics" element={<OrganizationAnalytics />} />
                <Route path="/organization/billing" element={<OrganizationBilling />} />
                <Route path="/organization/settings" element={<OrganizationSettings />} />
                <Route path="/organization/events/:id" element={<EventDetails />} />
                <Route path="/organization/users" element={<InviteMembers />} />
                <Route path="/organization/invitations" element={<OrganizationInvitations />} />
                <Route path="/organization/profile" element={<OrganizationProfile />} />
                {orgStubs.map(([path, title]) => (
                  <Route key={path} path={`/organization/${path}`} element={<Placeholder title={title} />} />
                ))}
              </Route>
            </Route>

            {/* Legacy generic dashboard for other roles (moved off "/" so the homepage can live there) */}
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
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  );
}
