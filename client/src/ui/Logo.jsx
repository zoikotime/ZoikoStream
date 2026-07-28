// Single source of truth for the ZoikoStream brand mark — the full wordmark logo
// (icon + "ZOIKO STREAM"). Purely presentational; wrap it in a <Link> at the call
// site when a click target is needed. Asset lives in /public so the same file backs
// the favicon and the email templates.
//
// The wordmark's lettering is dark navy, so the image sits on a subtle light chip
// that keeps it legible on any surface — dark hero, footer, or dark-mode sidebar —
// using the same logo in both themes. On light surfaces the chip is imperceptible.
// ponytail: one chip beats shipping a second white-text logo asset.
export default function Logo({ height = "h-8", className = "", children }) {
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <span className="rounded-lg bg-white/95 px-2 py-1">
        <img
          src="/zoiko-logo.png"
          alt="ZoikoStream"
          className={`${height} block w-auto object-contain`}
        />
      </span>
      {children}
    </span>
  );
}
