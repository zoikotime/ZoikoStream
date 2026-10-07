import { useState } from "react";
import { FiImage, FiUploadCloud } from "react-icons/fi";
import { Input } from "../../ui/forms";
import Logo from "../../ui/Logo";
import { cx } from "../../ui/tokens";
import { LOGO_MAX_LENGTH, logoUrlError } from "../../utils/orgLogo";

// One organization logo URL, previewed on the background it is FOR: the light logo on white,
// the dark logo on near-black, whatever theme the console itself is in. Previewing both on
// the console's own background hid exactly the contrast problem this exists to catch.
//
// The preview shows what a viewer will actually get:
//   • the image, once it loads;
//   • "didn't load" for a link that does not resolve to an image (a warning, not a block —
//     a CDN can be briefly down, and the server cannot fetch the URL to check);
//   • for an empty dark field, the light logo standing in — on the dark background, so a
//     logo that disappears in dark mode is obvious before anyone saves;
//   • with nothing set, the ZoikoStream mark viewers fall back to.
// The tile is a fixed size with object-contain, so no URL can change the layout around it.
const SURFACE = {
  light: { tile: "border-slate-200 bg-white", icon: "text-slate-400", name: "light" },
  dark: { tile: "border-slate-700 bg-slate-950", icon: "text-slate-500", name: "dark" },
};

export default function LogoField({ id, surface, label, hint, value, onChange, onBlur, error, fallbackUrl, orgName }) {
  const own = (value || "").trim();
  const ownValid = Boolean(own) && !logoUrlError(own);
  const standIn = (fallbackUrl || "").trim();
  // The fallback stands in only when this field is EMPTY — a typed-but-broken dark URL is
  // the dark URL's problem to report, not something to paper over with the light logo.
  const url = ownValid ? own : !own && standIn && !logoUrlError(standIn) ? standIn : null;
  const usingFallback = Boolean(url) && url !== own;

  // Load state belongs to one URL; a new URL starts over (render-phase reset, no effect).
  const [load, setLoad] = useState({ url, status: "loading" });
  if (load.url !== url) setLoad({ url, status: "loading" });
  const status = url ? load.status : own ? "invalid" : "empty";

  const s = SURFACE[surface];
  const noteId = `${id}-note`;
  let note = null;
  if (!error) {
    // Order matters: why a stand-in is showing outranks whether it has finished loading.
    if (status === "error") {
      note = usingFallback
        ? "Your light logo didn't load. Check that its link opens the image itself."
        : "This link didn't load as an image. Check that it opens the image itself.";
    } else if (usingFallback) {
      note = "Not set — your light logo is used on dark backgrounds, as shown.";
    } else if (status === "loading") {
      note = "Loading the preview…";
    } else if (status === "empty") {
      note = "Not set — viewers see the ZoikoStream logo.";
    } else if (status === "ok") {
      note = `Previewed on a ${s.name} background.`;
    }
  }

  return (
    <div>
      <label htmlFor={id} className="mb-1.5 block text-sm font-medium text-slate-700 dark:text-slate-300">
        {label}
      </label>
      <div className="flex flex-wrap items-center gap-4">
        <div
          data-testid={`logo-preview-${surface}`}
          data-status={status}
          className={cx("relative grid h-16 w-28 shrink-0 place-items-center overflow-hidden rounded-xl border p-2", s.tile)}
        >
          {url && status !== "error" && (
            <img
              src={url}
              alt={`${(orgName || "").trim() || "Organization"} logo, ${s.name} background preview`}
              className={cx("max-h-full max-w-full object-contain", status === "loading" && "opacity-0")}
              onLoad={() => setLoad({ url, status: "ok" })}
              onError={() => setLoad({ url, status: "error" })}
            />
          )}
          {status === "loading" && (
            <span className="absolute inset-2 animate-pulse rounded-lg bg-slate-400/20" aria-hidden="true" />
          )}
          {status === "empty" && <Logo height="h-5" />}
          {(status === "error" || status === "invalid") && <FiImage className={cx("text-xl", s.icon)} aria-hidden="true" />}
        </div>
        <div className="min-w-[240px] flex-1">
          <div className="relative">
            <FiUploadCloud className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <Input
              id={id}
              variant="form"
              className="pl-9"
              type="url"
              inputMode="url"
              maxLength={LOGO_MAX_LENGTH}
              error={error}
              aria-describedby={noteId}
              placeholder={surface === "dark" ? "https://cdn.yourcompany.com/logo-dark.png" : "https://cdn.yourcompany.com/logo.png"}
              value={value}
              onChange={(e) => onChange(e.target.value)}
              onBlur={onBlur}
            />
          </div>
        </div>
      </div>
      <div id={noteId}>
        {error ? (
          <p className="mt-1.5 text-xs font-medium text-rose-600 dark:text-rose-400">{error}</p>
        ) : (
          <p className="mt-1.5 text-xs text-slate-400">{hint}</p>
        )}
        {note && (
          <p
            role="status"
            className={cx(
              "mt-0.5 text-xs",
              status === "error" ? "font-medium text-amber-600 dark:text-amber-400" : "text-slate-500 dark:text-slate-400"
            )}
          >
            {note}
          </p>
        )}
      </div>
    </div>
  );
}
