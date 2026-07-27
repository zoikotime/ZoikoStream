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
import AdminDashboard from "./pages/admin/Dashboard";
import Organizations from "./pages/admin/Organizations";
<<<<<<< Updated upstream
import AuthPage from "./pages/AuthPage";
=======
import LiveEvents from "./pages/admin/LiveEvents";
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
>>>>>>> Stashed changes

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
function RootRedirect() {
  const { user, loading } = useAuth();
  if (loading) return null;
  return <Navigate to={user ? roleHome(user.role) : "/login"} replace />;
}

// Menu items that don't have a page yet — kept in-layout so the sidebar doesn't bounce the user out.
const adminStubs = ["users", "events", "analytics", "billing", "settings"];
const orgStubs = ["events", "recordings", "analytics", "users", "billing", "settings"];

// Legacy generic dashboard (speaker/viewer land here until they get their own).
const legacyStubs = [
  ["events", "Events"], ["speakers", "Speakers"], ["viewers", "Viewers"],
  ["recordings", "Recordings"], ["analytics", "Analytics"], ["users", "Users"],
  ["invitations", "Invitations"], ["billing", "Billing"], ["settings", "Settings"],
];

const cap = (s) => s[0].toUpperCase() + s.slice(1);

export default function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<AuthPage />} />

            {/* Super admin (platform) area */}
            <Route element={<RoleRoute allow={["super_admin"]} />}>
              <Route element={<AdminLayout />}>
                <Route path="/admin/dashboard" element={<AdminDashboard />} />
                <Route path="/admin/organizations" element={<Organizations />} />
                {adminStubs.map((p) => (
                  <Route key={p} path={`/admin/${p}`} element={<Placeholder title={cap(p)} />} />
                ))}
              </Route>
            </Route>

            {/* Organization admin area */}
            <Route element={<RoleRoute allow={["org_admin"]} />}>
              <Route element={<OrganizationLayout />}>
                <Route path="/organization/dashboard" element={<OrganizationDashboard />} />
<<<<<<< Updated upstream
                {orgStubs.map((p) => (
                  <Route key={p} path={`/organization/${p}`} element={<Placeholder title={cap(p)} />} />
                ))}
=======
                <Route path="/organization/events" element={<OrganizationEvents />} />
                <Route path="/organization/recordings" element={<OrganizationRecordings />} />
                <Route path="/organization/analytics" element={<OrganizationAnalytics />} />
                <Route path="/organization/billing" element={<OrganizationBilling />} />
                <Route path="/organization/settings" element={<OrganizationSettings />} />
                <Route path="/organization/events/:id" element={<EventDetails />} />
                <Route path="/organization/users" element={<InviteMembers />} />
                <Route path="/organization/profile" element={<OrganizationProfile />} />
>>>>>>> Stashed changes
              </Route>
            </Route>

            {/* Legacy generic dashboard for other roles */}
            <Route element={<ProtectedRoute />}>
              <Route element={<MainLayout />}>
                <Route index element={<Dashboard />} />
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
