import { ExternalLink, LayoutDashboard } from "lucide-react";

import { HAS_CONSOLES, WEB_APP_URL } from "../platform";
import { openExternal } from "./bridge";
import { useAuth } from "../auth/AuthContext";

// Account roles whose work is in a console this build does not carry. A host or a speaker
// sees nothing from this component — their console IS in the app, and telling them about a
// website would be noise.
const CONSOLE_ROLES = new Set(["org_admin", "billing_admin", "super_admin"]);

/**
 * Tells an admin, on the phone, where their console went.
 *
 * ── WHY THIS EXISTS ──────────────────────────────────────────────────────────────────────
 * The store build ships no /admin/* or /organization/* routes (see the HAS_CONSOLES gate in
 * App.jsx), and accountHome() therefore sends every console role to /events/mine instead of
 * to a dashboard. That is the right destination and it is also, without this, completely
 * unexplained: an org admin signs in on their phone and lands on a list of events they are
 * probably not assigned to any of, with no indication that the rest of the product exists.
 * "The app is broken" is the reasonable conclusion from that screen.
 *
 * So the omission is stated rather than hidden. It says which surfaces are not here, why, and
 * offers the one-tap way to reach them.
 *
 * ── WHY IT OPENS A BROWSER AND NOT A WEBVIEW ─────────────────────────────────────────────
 * Two independent reasons, either of which would be sufficient:
 *
 *   * The consoles were removed because they are unusable at phone width, so loading them in
 *     an in-app WebView would deliver exactly what was removed and undo the decision.
 *   * Billing lives inside the organization console and hands off to Stripe's hosted
 *     checkout. A subscription sold through a third-party checkout inside an Android app is
 *     the shape Google Play's payments policy exists to reject; leaving for the device
 *     browser is what keeps this a link to a website rather than an in-app purchase flow.
 *
 * Renders nothing on the web build, where HAS_CONSOLES is the literal `true` and the consoles
 * are one click away in the nav.
 */
export default function ConsoleOnWebNotice() {
  const { user } = useAuth();

  if (HAS_CONSOLES) return null;
  if (!user || !CONSOLE_ROLES.has(user.role)) return null;

  const isPlatform = user.role === "super_admin";
  const target = `${WEB_APP_URL}${isPlatform ? "/admin/dashboard" : "/organization/dashboard"}`;

  return (
    <div className="mb-6 rounded-2xl border border-slate-200 bg-white px-5 py-4">
      <div className="flex items-start gap-3">
        <LayoutDashboard className="mt-0.5 h-5 w-5 shrink-0 text-slate-400" aria-hidden="true" />
        <div className="min-w-0">
          <h2 className="text-[15px] font-semibold text-slate-900">
            {isPlatform ? "The platform console is on the web" : "Your organization console is on the web"}
          </h2>
          <p className="mt-1 text-[14px] leading-relaxed text-slate-600">
            {isPlatform
              ? "Organizations, users, commerce, governance and the rest of the platform console are built for a full screen, so they live on the web app rather than here."
              : "Events, recordings, members, analytics and billing are built for a full screen, so they live on the web app rather than here. This app is for joining and running the broadcasts you are on."}
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
