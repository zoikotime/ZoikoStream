// Who sees the Operator Guide, and where: signed-in hosts and admins, on console pages only.
// The server re-checks the role on every request; this only avoids loading Guide for nobody.
export const GUIDE_ROLES = ["host", "org_admin", "super_admin"];
const CONSOLE = /^\/(organization|admin|host|dashboard)(\/|$)/;

export function isConsolePath(pathname) {
  return CONSOLE.test(pathname) && !pathname.startsWith("/organization/privacy/");
}

export function eventIdFrom(pathname, search) {
  const fromPath = pathname.match(/\/events\/([0-9a-f-]{36})/i);
  return fromPath ? fromPath[1] : new URLSearchParams(search).get("event");
}
