import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { roleHome } from "../auth/roleHome";
import { PageSpinner } from "../ui/Spinner";

// Gate a route to specific roles. Unauthenticated -> login; wrong role -> their own home.
export default function RoleRoute({ allow }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <PageSpinner />;
  // Carry the originally-requested URL (path + ?event=<id> etc.) through login — an
  // assignment-notification link (e.g. /moderator/dashboard?event=<id>) hit while signed
  // out must land back on that SAME event's console after signing in, not the bare
  // dashboard. See Login.jsx, which reads this back out of location.state.
  if (!user) return <Navigate to="/login" state={{ from: location }} replace />;
  if (!allow.includes(user.role)) return <Navigate to={roleHome(user.role) || "/"} replace />;
  return <Outlet />;
}
