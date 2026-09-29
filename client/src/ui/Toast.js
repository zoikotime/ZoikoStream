// Toast — thin wrapper over the already-installed react-hot-toast so the whole
// app has one themed notification API (no second toast system).
import toast, { Toaster } from "react-hot-toast";

// Shared look for the <Toaster> (mount once, e.g. in main.jsx). Themed for dark mode
// via CSS vars set by Tailwind's `.dark` class would need runtime; keep neutral-dark
// styling that reads well on both.
// How long each kind stays up. An error has to be READ, often acted on, and sometimes copied
// into a support request; react-hot-toast's 4s default dismissed a two-line infrastructure
// error before it could be read. Every toast also has a close button (AppToaster.jsx) and
// pauses while hovered, so a long duration never traps the reader.
export const TOAST_DURATIONS = { success: 3500, warning: 5500, error: 9000 };

export const toasterProps = {
  position: "top-right",
  toastOptions: {
    style: {
      borderRadius: "12px",
      background: "#0f172a",
      color: "#f8fafc",
      fontSize: "14px",
      border: "1px solid rgba(255,255,255,0.08)",
    },
    success: { duration: TOAST_DURATIONS.success, iconTheme: { primary: "#8b5cf6", secondary: "#0f172a" } },
    error: { duration: TOAST_DURATIONS.error, iconTheme: { primary: "#f43f5e", secondary: "#0f172a" } },
  },
};

export const notify = {
  success: (msg, opts) => toast.success(msg, opts),
  error: (msg, opts) => toast.error(msg, opts),
  warning: (msg, opts) => toast(msg, { icon: "⚠️", duration: TOAST_DURATIONS.warning, ...opts }),
  info: (msg, opts) => toast(msg, opts),
  loading: (msg, opts) => toast.loading(msg, opts),
  promise: (p, msgs, opts) => toast.promise(p, msgs, opts),
  dismiss: (id) => toast.dismiss(id),
  // Live-event activity alert (a viewer's chat/poll/announcement, or an operator's
  // action reaching a viewer) — a bell icon so it reads as "something happened" at a
  // glance, distinct from a plain info toast. Paired with utils/sound.js's chime at every
  // call site; this is the visible half of that alert.
  alert: (msg, opts) => toast(msg, { icon: "🔔", ...opts }),
};

export { toast, Toaster };
