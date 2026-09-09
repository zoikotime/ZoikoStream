import { createContext, useContext, useEffect } from "react";

// The org console's window scope (workspace + time range). The controls live in the topbar
// but the fetch lives in the page, so the two meet here rather than the page lifting the
// whole toolbar into itself or the layout owning a fetch it doesn't use.
//
// Default value keeps pages that render outside the layout (tests, storybook) working
// without a provider.
export const OrgScopeContext = createContext({
  filters: { workspace: null, range: "24h" },
  setFilters: () => {},
  enableScope: () => {},
  enableQuickActions: () => {},
  state: null,
  reload: () => {},
});

// Call from a page that reads the scope: returns the current filters and switches the
// topbar's controls on for as long as the page is mounted.
//
// `quickActions` opts the page into the topbar's Quick Actions menu, which then replaces the
// health verdict pill for that page only. Same shape as `scoped`, and for the same reason:
// the control lives in the topbar but only some pages want it, so the page asks rather than
// the topbar guessing. Scoping it this way keeps the live health verdict — and its retry
// affordance when the API is unreachable — on every other org page.
export function useOrgScope({ scoped = false, quickActions = false } = {}) {
  const ctx = useContext(OrgScopeContext);
  const { enableScope, enableQuickActions } = ctx;
  useEffect(() => {
    if (scoped) enableScope();
  }, [scoped, enableScope]);
  useEffect(() => {
    if (quickActions) enableQuickActions();
  }, [quickActions, enableQuickActions]);
  return ctx;
}
