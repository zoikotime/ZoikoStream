import { lazy } from "react";
import ConsoleTabs from "./ConsoleTabs";
import Users from "./Users";
import Organizations from "./Organizations";
import SystemStatus from "./SystemStatus";

// The three merges of the console consolidation, in one file so the whole of "what moved
// where" is readable in one screen rather than spread across three page files.
//
// Each host page keeps its own route and its own component untouched; the merged page is
// lazy so opening Identity & Access does not also download the roles reference. The moved
// page's original route is still registered in App.jsx — these are an additional way in.
const Roles = /* @__PURE__ */ lazy(() => import("./Roles"));
const Developers = /* @__PURE__ */ lazy(() => import("./Developers"));
const Infrastructure = /* @__PURE__ */ lazy(() => import("./Infrastructure"));

// /admin/users — the accounts, plus the read-only reference for the roles being assigned.
// services/admin.roles() derives that list from security._ROLE_RANK; it was never editable,
// which is exactly why it reads as reference material next to the table rather than as a
// destination of its own.
export function IdentityAccess() {
  return (
    <ConsoleTabs
      label="Identity and access"
      idPrefix="identity"
      tabs={[
        { key: "users", label: "Users", render: () => <Users /> },
        { key: "roles", label: "Roles & Capabilities", render: () => <Roles /> },
      ]}
    />
  );
}

// /admin/organizations — Developer Platform is a view OF the organizations (it fetched
// /admin/organizations and nothing else), so it belongs beside them.
export function OrganizationsConsole() {
  return (
    <ConsoleTabs
      label="Organizations"
      idPrefix="orgs"
      tabs={[
        { key: "organizations", label: "Organizations", render: () => <Organizations /> },
        { key: "developers", label: "Developer Platform", render: () => <Developers /> },
      ]}
    />
  );
}

// /admin/status — System Status and Media Infrastructure both called GET
// /admin/platform-health. Two rail entries rendering one endpoint was the clearest
// duplication in the console, and the one most likely to have operators comparing two
// screens for a disagreement that could not exist.
export function SystemStatusConsole() {
  return (
    <ConsoleTabs
      label="System status"
      idPrefix="status"
      tabs={[
        { key: "status", label: "System Status", render: () => <SystemStatus /> },
        { key: "infrastructure", label: "Media Infrastructure", render: () => <Infrastructure /> },
      ]}
    />
  );
}
