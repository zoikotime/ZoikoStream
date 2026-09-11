import { useCallback, useState } from "react";
import api from "../api";
import useApi from "../hooks/useApi";
import useInterval from "../hooks/useInterval";
import { CONSOLE } from "../ui/tokens";
import AppShell from "./AppShell";
import Sidebar from "../components/Dashboard/Sidebar";
import Topbar from "../components/Dashboard/Topbar";
import { OrgScopeContext } from "../components/organization/orgScope";

// Shell for all /organization/* pages.
//
// The chrome needs live org state on every page (nav badges, workspace identity, the
// service-health verdict), so it is fetched once here and handed to both bars — one request
// per shell, not two per page. Identity comes from the SERVER (/organization/console-state)
// rather than the JWT, so the sidebar's role label always matches what the API will
// actually authorize.
//
// The window scope (workspace + range) lives here too, because the topbar owns the controls
// while the Overview page owns the fetch; it reaches the page through OrgScopeContext.
const REFRESH_MS = 30_000;

// The rail's collapsed state is a per-operator preference, so it outlives the mount rather
// than resetting on every navigation. Reading localStorage is wrapped because it throws in
// private-mode Safari and with storage disabled — a themed preference is never worth a
// blank console.
const COLLAPSE_KEY = "zk.org.sidebar.collapsed";
const readCollapsed = () => {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
};

export default function OrganizationLayout() {
  const { data: state, loading, error, reload } = useApi(() =>
    api.get("/organization/console-state").then((r) => r.data)
  );
  useInterval(reload, REFRESH_MS);

  // Set by the page that wants the topbar's Quick Actions menu (Profile).
  const [quickActions, setQuickActions] = useState(false);
  const [collapsed, setCollapsed] = useState(readCollapsed);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => {
      const next = !v;
      try {
        localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        /* preference is best-effort; the session still works without it */
      }
      return next;
    });
  }, []);

  // Returns its own undo, which useOrgScope hands back as an effect cleanup, so the menu
  // belongs to the page that asked for it. Without that it was a one-way switch: the first
  // page to turn it on left it on for every page after.
  const enableQuickActions = useCallback(() => {
    setQuickActions(true);
    return () => setQuickActions(false);
  }, []);

  const unknown = Boolean(error) || (!state && !loading);

  return (
    <OrgScopeContext.Provider
      value={{ enableQuickActions, state, reload }}
    >
      <AppShell
        surface={`${CONSOLE.page} text-slate-800 dark:text-neutral-200`}
        // One content container for every organization page, so a table screen and the
        // dashboard start and end on the same vertical lines. Capped rather than fluid: past
        // ~1400px a data table becomes a scan across the full width of a 27" display. Pages
        // that set their own narrower max-width still win inside this.
        mainClass="mx-auto w-full max-w-[1400px] p-4 sm:p-6 lg:px-8 lg:py-7"
        renderSidebar={({ open, setOpen }) => (
          <Sidebar
            open={open}
            onClose={() => setOpen(false)}
            state={state}
            collapsed={collapsed}
          />
        )}
        renderTopbar={({ setOpen }) => (
          <Topbar
            onMenuClick={() => setOpen((v) => !v)}
            state={state}
            unknown={unknown}
            onRetry={reload}
            quickActions={quickActions}
            // The collapse control lives in the header, at the sidebar boundary. The STATE
            // still belongs here, because both bars read it — the rail to size itself, the
            // header to pick its icon.
            collapsed={collapsed}
            onToggleCollapse={toggleCollapsed}
          />
        )}
      />
    </OrgScopeContext.Provider>
  );
}
