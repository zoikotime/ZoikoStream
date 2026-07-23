import { useState } from "react";
import { Outlet } from "react-router-dom";

// The one dashboard shell: sidebar + topbar + scrollable routed content, plus the
// mobile-drawer open/close state that every dashboard layout used to duplicate.
// Each area supplies its own sidebar/topbar via render props (they receive
// { open, setOpen }) so the console vs org chrome stays distinct without repeating
// the wrapper. `fullHeight` locks the viewport (admin console); default scrolls (org).
export default function AppShell({ renderSidebar, renderTopbar, fullHeight = false }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className={`flex ${
        fullHeight ? "h-screen overflow-hidden" : "min-h-screen"
      } bg-slate-50 text-slate-800 dark:bg-slate-950 dark:text-slate-200`}
    >
      {renderSidebar({ open, setOpen })}
      <div className="flex min-w-0 flex-1 flex-col">
        {renderTopbar({ open, setOpen })}
        <main className="flex-1 overflow-auto p-4 sm:p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
