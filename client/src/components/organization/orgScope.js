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
  state: null,
  reload: () => {},
});

// Call from a page that reads the scope: returns the current filters and switches the
// topbar's controls on for as long as the page is mounted.
export function useOrgScope({ scoped = false } = {}) {
  const ctx = useContext(OrgScopeContext);
  const { enableScope } = ctx;
  useEffect(() => {
    if (scoped) enableScope();
  }, [scoped, enableScope]);
  return ctx;
}
