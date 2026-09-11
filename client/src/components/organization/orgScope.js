import { createContext, useContext, useEffect } from "react";

// Shared organization-console state: the live shell payload (/organization/console-state)
// and the one topbar control a page can still ask for.
//
// This used to also carry a window scope (workspace + time range) and a `simpleShell` flag.
// Both are gone: no page read the scope any more, so it was rendering dead controls on every
// screen, and the simplified shell is now simply THE shell for /organization/* rather than
// something one page opts into. Keeping either would have meant two organization layouts
// again, which is the thing that made the console change appearance as you navigated it.
//
// Default value keeps pages that render outside the layout (tests, storybook) working
// without a provider.
export const OrgScopeContext = createContext({
  enableQuickActions: () => {},
  state: null,
  reload: () => {},
});

/**
 * Read the shell's state, and optionally claim the topbar's Quick Actions slot.
 *
 * `quickActions` is page-scoped: the enabler returns its own undo, which is returned here as
 * the effect's cleanup, so the menu belongs to the page that asked for it and leaves with
 * it. It was a one-way latch before — the first page to switch it on left it on for the rest
 * of the session.
 */
export function useOrgScope({ quickActions = false } = {}) {
  const ctx = useContext(OrgScopeContext);
  const { enableQuickActions } = ctx;
  useEffect(() => {
    if (quickActions) return enableQuickActions();
    return undefined;
  }, [quickActions, enableQuickActions]);
  return ctx;
}
