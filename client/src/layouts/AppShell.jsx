import { Suspense, useEffect, useState } from "react";
import { Outlet } from "react-router-dom";
import Spinner from "../ui/Spinner";
import { register } from "../ui/dismissStack";

// The one dashboard shell: sidebar + topbar + scrollable routed content, plus the
// mobile-drawer open/close state that every dashboard layout used to duplicate.
// Each area supplies its own sidebar/topbar via render props (they receive
// { open, setOpen }) so the console vs org chrome stays distinct without repeating
// the wrapper. `fullHeight` locks the viewport (admin console); default scrolls (org).
// `surface` overrides the page background — the admin console runs a true-black dark
// theme while the org area keeps slate. Default preserves the original look exactly.
export default function AppShell({
  renderSidebar,
  renderTopbar,
  fullHeight = false,
  surface = "bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-200",
  mainClass = "p-4 sm:p-6 lg:p-8",
}) {
  const [open, setOpen] = useState(false);

  // The mobile drawer is a dismissable layer, and it is the ONE layer that does not go
  // through ui/Overlay — Sidebar draws its own backdrop and slides itself, because on large
  // screens the same element is the permanent rail rather than a drawer.
  //
  // Registering it here is what makes Android's Back close the menu instead of navigating
  // the page behind it. Without this the console's most-used control is also the one that
  // makes Back look broken: the route changes underneath while the menu stays open.
  //
  // Registered only while open, so an ordinary Back on a page with no menu showing falls
  // straight through to history, which is what it should do.
  useEffect(() => {
    if (!open) return undefined;
    const layer = register(() => setOpen(false));
    return layer.release;
  }, [open]);

  return (
    <div className={`flex ${fullHeight ? "h-screen overflow-hidden" : "min-h-screen"} ${surface}`}>
      {renderSidebar({ open, setOpen })}
      <div className="flex min-w-0 flex-1 flex-col">
        {renderTopbar({ open, setOpen })}
        {/* Suspense so a code-split routed page (the admin console area) can stream in
            without the shell unmounting. A no-op for areas whose pages are eager. */}
        <main className={`flex-1 overflow-auto ${mainClass}`}>
          <Suspense
            fallback={
              <div className="grid place-items-center py-24">
                <Spinner />
              </div>
            }
          >
            <Outlet />
          </Suspense>
        </main>
      </div>
    </div>
  );
}
