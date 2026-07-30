import api from "../api";
import useApi from "../hooks/useApi";
import useInterval from "../hooks/useInterval";
import { CONSOLE } from "../ui/tokens";
import AppShell from "./AppShell";
import AdminSidebar from "../components/admin/AdminSidebar";
import AdminTopbar from "../components/admin/AdminTopbar";

// Super Admin console shell. The chrome needs live platform state on EVERY admin page
// (sidebar attention badges, the topbar's systems verdict, the elevation countdown), so
// it is fetched once here and handed to both bars rather than each of them fetching —
// one request per shell, not two per page.
//
// /admin/console-state is deliberately small (health verdict + three counts + elevation);
// the heavy page payload is /admin/command-center, fetched by the dashboard itself.
const REFRESH_MS = 30_000;

export default function AdminLayout() {
  const { data: state, loading, error, reload } = useApi(() =>
    api.get("/admin/console-state").then((r) => r.data)
  );
  useInterval(reload, REFRESH_MS);

  // `unknown` is passed down explicitly so the chrome can say "I don't know" instead of
  // falling back to a healthy-looking default. A console that claims "All systems
  // operational" because its own status call failed is worse than one that admits it.
  const unknown = Boolean(error) || (!state && !loading);

  return (
    <AppShell
      fullHeight
      surface={`${CONSOLE.page} text-slate-800 dark:text-neutral-200`}
      mainClass="p-4 sm:p-6 lg:px-8 lg:py-7"
      renderSidebar={({ open, setOpen }) => (
        <AdminSidebar
          open={open}
          onClose={() => setOpen(false)}
          state={state}
          unknown={unknown}
          onChange={reload}
        />
      )}
      renderTopbar={({ setOpen }) => (
        <AdminTopbar
          onMenuClick={() => setOpen((v) => !v)}
          state={state}
          unknown={unknown}
          onRetry={reload}
        />
      )}
    />
  );
}
