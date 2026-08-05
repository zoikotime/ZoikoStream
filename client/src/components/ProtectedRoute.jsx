import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { PageSpinner } from "../ui/Spinner";

export default function ProtectedRoute() {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) return <PageSpinner />;
  // See RoleRoute.jsx — same reasoning for carrying the requested URL through login.
  return user ? <Outlet /> : <Navigate to="/login" state={{ from: location }} replace />;
}
