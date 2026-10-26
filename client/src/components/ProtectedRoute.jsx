import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { PageSpinner } from "../ui/Spinner";
import SessionUnreachable from "./SessionUnreachable";

export default function ProtectedRoute() {
  const { user, loading, unreachable } = useAuth();
  const location = useLocation();

  if (loading) return <PageSpinner />;
  // Session held but the server could not be reached: reconnect, do not ask for a password.
  if (unreachable) return <SessionUnreachable />;
  // See RoleRoute.jsx — same reasoning for carrying the requested URL through login.
  return user ? <Outlet /> : <Navigate to="/login" state={{ from: location }} replace />;
}
