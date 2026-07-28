import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { PageSpinner } from "../ui/Spinner";

export default function ProtectedRoute() {
  const { user, loading } = useAuth();

  if (loading) return <PageSpinner />;
  return user ? <Outlet /> : <Navigate to="/login" replace />;
}
