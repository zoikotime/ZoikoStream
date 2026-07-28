import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { roleHome } from "../auth/roleHome";
import { PageSpinner } from "../ui/Spinner";

// Gate a route to specific roles. Unauthenticated -> login; wrong role -> their own home.
export default function RoleRoute({ allow }) {
  const { user, loading } = useAuth();

  if (loading) return <PageSpinner />;
  if (!user) return <Navigate to="/login" replace />;
  if (!allow.includes(user.role)) return <Navigate to={roleHome(user.role) || "/"} replace />;
  return <Outlet />;
}
