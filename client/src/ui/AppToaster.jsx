import toast, { Toaster, ToastBar } from "react-hot-toast";
import { FiX } from "react-icons/fi";
import { toasterProps } from "./Toast";

// The one <Toaster>, mounted in main.jsx. The default bar with a close button added: an error
// that stays up long enough to read (TOAST_DURATIONS) must also be dismissible the moment it
// has been read. Loading toasts keep no button — they end when their work does.
export default function AppToaster() {
  return (
    <Toaster {...toasterProps}>
      {(t) => (
        <ToastBar toast={t}>
          {({ icon, message }) => (
            <>
              {icon}
              {message}
              {t.type !== "loading" && (
                <button
                  type="button"
                  onClick={() => toast.dismiss(t.id)}
                  aria-label="Dismiss notification"
                  className="ml-1 shrink-0 rounded-md p-1 text-slate-400 transition hover:bg-white/10 hover:text-white focus:outline-none focus-visible:ring-2 focus-visible:ring-white/40"
                >
                  <FiX aria-hidden="true" />
                </button>
              )}
            </>
          )}
        </ToastBar>
      )}
    </Toaster>
  );
}
