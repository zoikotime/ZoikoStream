import { ExternalLink, LayoutDashboard } from "lucide-react";

import { HAS_ADMIN_CONSOLE, WEB_APP_URL } from "../platform";
import { openExternal } from "./bridge";
import { useAuth } from "../auth/AuthContext";

// Only the PLATFORM console is absent from the native build now, and only a super_admin has
// one. org_admin and billing_admin used to be listed here and no longer are: their console
// ships in the app, so telling them it lives on a website would be both noise and false.
const CONSOLE_ROLES = new Set(["super_admin"]);

/**
 * Tells a super admin, on the phone, where the PLATFORM console went.
 *
 * ── WHAT THIS DOES AND NO LONGER DOES ────────────────────────────────────────────────────
 * It used to cover both consoles, because the native build carried neither. The organization
 * console now ships (see the HAS_ORG_CONSOLE gate in App.jsx), so the only surface still
 * absent here is /admin/* — 21 pages of platform operator tooling used by a handful of people
 * who are at a desk when they use them.
 *
 * Narrowing it matters more than it sounds. Shown to an org_admin today it would be actively
 * wrong: it would send someone to a browser for a console sitting one tap away in their own
 * nav, and it would read as "the app is a cut-down version" to exactly the person for whom it
 * no longer is.
 *
 * ── WHY IT OPENS A BROWSER AND NOT A WEBVIEW ─────────────────────────────────────────────
 * An in-app WebView pointed at the platform console would deliver precisely the dense
 * multi-column operator surfaces that were left out, inside a frame with no address bar and
 * no way to tell the user where they are. The device browser is the honest destination: it is
 * the web app, on its own origin, with the session the user already has there.
 *
 * Renders nothing on the web build, and nothing for any role whose console is in the app.
 */
export default function ConsoleOnWebNotice() {
  const { user } = useAuth();

  if (HAS_ADMIN_CONSOLE) return null;
  if (!user || !CONSOLE_ROLES.has(user.role)) return null;

  const target = `${WEB_APP_URL}/admin/dashboard`;

  return (
    <div className="mb-6 rounded-2xl border border-slate-200 bg-white px-5 py-4">
      <div className="flex items-start gap-3">
        <LayoutDashboard className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" />
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-slate-900">
            The platform console is on the web
          </h2>
          <p className="mt-1 text-[14px] leading-relaxed text-slate-600">
            Organizations, users, commerce, governance and the rest of the platform console are
            built for a full screen, so they live on the web app rather than here. Your
            organization console is in this app, in the menu.
          </p>
          <button
            type="button"
            onClick={() => openExternal(target)}
            className="mt-3 inline-flex items-center gap-1.5 rounded-lg border border-slate-300 px-3 py-2 text-[13px] font-semibold text-slate-700 hover:bg-slate-50"
          >
            Open in your browser
            <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
        </div>
      </div>
    </div>
  );
}
