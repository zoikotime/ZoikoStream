import {
  FiHome,
  FiCalendar,
  FiPlayCircle,
  FiBarChart2,
  FiUsers,
  FiCreditCard,
  FiSettings,
} from "react-icons/fi";
import AppShell from "./AppShell";
import Sidebar from "../components/Dashboard/Sidebar";
import Topbar from "../components/Dashboard/Topbar";
import { useAuth } from "../auth/AuthContext";

const nav = [
  { to: "/organization/dashboard", label: "Dashboard", icon: FiHome, end: true },
  { to: "/organization/events", label: "Events", icon: FiCalendar },
  { to: "/organization/recordings", label: "Recordings", icon: FiPlayCircle },
  { to: "/organization/analytics", label: "Analytics", icon: FiBarChart2 },
  { to: "/organization/users", label: "Users", icon: FiUsers },
  { to: "/organization/billing", label: "Billing", icon: FiCreditCard },
  { to: "/organization/settings", label: "Settings", icon: FiSettings },
];

// Shell for all /organization/* pages: shared Sidebar + Topbar on the shared AppShell.
export default function OrganizationLayout() {
  const { user } = useAuth();

  // ponytail: org name isn't on the user payload yet — show a sensible default until it is.
  const orgName = user?.organization_name || "Zoiko Organization";

  return (
    <AppShell
      renderSidebar={({ open, setOpen }) => <Sidebar nav={nav} open={open} onClose={() => setOpen(false)} />}
      renderTopbar={({ setOpen }) => (
        <Topbar orgName={orgName} subtitle="Organization" onMenuClick={() => setOpen((v) => !v)} />
      )}
    />
  );
}
