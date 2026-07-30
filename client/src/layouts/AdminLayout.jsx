import AppShell from "./AppShell";
import AdminSidebar from "../components/admin/AdminSidebar";
import AdminTopbar from "../components/admin/AdminTopbar";

// Super Admin shell: grouped platform sidebar + command-bar topbar, on the shared
// AppShell (fullHeight = locked viewport). Sidebar/topbar stay admin-specific.
export default function AdminLayout() {
  return (
    <AppShell
      fullHeight
      renderSidebar={({ open, setOpen }) => <AdminSidebar open={open} onClose={() => setOpen(false)} />}
      renderTopbar={({ setOpen }) => <AdminTopbar onMenuClick={() => setOpen((v) => !v)} />}
    />
  );
}
