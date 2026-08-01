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
import EventRegistration from "./pages/EventRegistration";
import HostDashboard from "./pages/host/Dashboard";
import EventWatch from "./pages/watch/EventWatch";
import ModeratorDashboard from "./pages/moderator/Dashboard";
import OrganizationProfile from "./pages/organization/Profile";

// Public marketing homepage — code-split from the app bundle.
const Home = lazy(() => import("./pages/Home/Home"));

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

// Send unknown URLs to the user's own dashboard (or login), not a hardcoded page.
// Roles without an app home (viewers) fall through to the public homepage.
function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={roleHome(user.role) || "/"} replace />;
}

// "/" shows the public homepage to visitors and to roles without an app dashboard
// (viewers); logged-in staff/admins go to their dashboard.
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

// Legacy generic dashboard (speaker/viewer land here until they get their own).
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
              <Route path="/accept-invite" element={<AcceptInvitation />} />
            </Route>

            {/* Public event registration landing (shareable link) */}
            <Route path="/e/:id" element={<EventRegistration />} />

            {/* Viewer Portal — attendee watch page from an invite link (public) */}
            <Route path="/events/:eventId/watch" element={<EventWatch />} />

            {/* Host broadcasting studio — standalone full-screen page (own header/sidebar).
                ponytail: open route for the demo; gate to a "host" role once auth supports it. */}
            <Route path="/host/dashboard" element={<HostDashboard />} />

            {/* Moderator console — standalone real-time audience-management page.
                ponytail: open route for the demo; gate to a "moderator" role once auth supports it. */}
            <Route path="/moderator/dashboard" element={<ModeratorDashboard />} />

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
