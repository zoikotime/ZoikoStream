import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

export default function ProtectedRoute() {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="grid min-h-screen place-items-center text-slate-400">Loading…</div>
    );
  }
  return user ? <Outlet /> : <Navigate to="/login" replace />;
}
