import { useState } from "react";
import { FiRadio } from "react-icons/fi";
import Modal from "../../ui/Modal";
import Button from "../../ui/Button";
import OrgLogo from "../../ui/OrgLogo";
import { ACCENT, cx } from "../../ui/tokens";

// Settings -> Branding's preview: the organization's CURRENT form values (saved or not) applied
// to a mock event header and its primary action.
//
// A mock-up by construction. This module imports no API client and no broadcast or LiveKit
// code, so nothing in it can start a session, create one or change an event: the "Go Live"
// inside it only demonstrates how the branded action looks and responds (hover, focus, press).
//
// It also says plainly what the brand reaches today: the logos appear in the viewer page's
// header (the version for the viewer's theme, via ui/OrgLogo — the same component this preview
// uses, so the two cannot disagree); the color is not applied anywhere yet.
export default function BrandingPreview({ open, onClose, accent, logoUrl, logoUrlDark, orgName }) {
  const [presses, setPresses] = useState(0);
  const tone = ACCENT[accent] || ACCENT.violet;
  const close = () => {
    setPresses(0);
    onClose();
  };
  const name = (orgName || "").trim() || "Your organization";

  return (
    <Modal
      open={open}
      onClose={close}
      title="Branding preview"
      size="lg"
      closeOnBackdrop
      footer={<Button variant="secondary" size="sm" onClick={close}>Close</Button>}
    >
      <p className="text-xs text-slate-500 dark:text-slate-400">
        A mock-up using your current branding, including unsaved changes, in the console&apos;s
        current theme. Nothing here starts a broadcast. Your logo appears in the header of your
        events&apos; viewer pages; the brand color is not applied yet, and emails and the console
        keep the ZoikoStream palette and mark.
      </p>

      <div
        data-testid="branding-preview-stage"
        className="mt-4 rounded-2xl border border-slate-200 bg-slate-50 p-5 dark:border-slate-700 dark:bg-slate-950"
      >
        <div className="flex items-center gap-3">
          {/* The logo exactly as the viewer page's brand bar draws it: the same component, the
              same chain (this theme's logo, then the light one, then the ZoikoStream mark on its
              light chip), the bar's own height, on the bar's own surface. The box is a fixed
              size, so the title never shifts while an image loads, and wide enough for a
              wordmark: it used to be a 48px square, which drew a 2.8:1 wordmark at 46x16 px, and
              the fallback was the bare wordmark on a slate tile, which hid its navy lettering. */}
          <span
            data-testid="branding-preview-logo"
            className="flex h-12 w-28 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-slate-200 bg-white px-2 sm:w-32 dark:border-slate-700 dark:bg-slate-950"
          >
            <OrgLogo light={logoUrl} dark={logoUrlDark} name={name} height="h-6 sm:h-7" className="max-w-full" />
          </span>
          <div className="min-w-0">
            <p className="truncate text-xs font-semibold uppercase tracking-wide text-slate-400">{name}</p>
            <p className="truncate font-semibold text-slate-900 dark:text-white">Sample event · Quarterly all-hands</p>
          </div>
          <span className="ml-auto shrink-0 rounded-full bg-slate-200 px-2.5 py-1 text-xs font-semibold text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            Ready
          </span>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            autoFocus
            onClick={() => setPresses((n) => n + 1)}
            aria-describedby="branding-preview-note"
            className={cx(
              "inline-flex items-center gap-2 rounded-lg px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition",
              "hover:brightness-110 active:scale-[0.97] motion-reduce:transition-none motion-reduce:active:scale-100",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-900 focus-visible:ring-offset-2 dark:focus-visible:ring-white dark:focus-visible:ring-offset-slate-950",
              tone.solid
            )}
          >
            <FiRadio aria-hidden="true" /> Go Live
          </button>
          <p id="branding-preview-note" role="status" aria-live="polite" className="text-xs text-slate-500 dark:text-slate-400">
            {presses
              ? "Preview only — nothing was started. In the producer console this button begins the broadcast."
              : "Hover, focus or press it to see each state. Preview only — nothing is started."}
          </p>
        </div>
      </div>
    </Modal>
  );
}
