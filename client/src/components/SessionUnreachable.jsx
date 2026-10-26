import { useState } from "react";
import { FiWifiOff } from "react-icons/fi";
import { useAuth } from "../auth/AuthContext";
import Button from "../ui/Button";

// What the route guards render while the session is UNREACHABLE (AuthContext): a credential
// is held but the server could not be asked about it — offline, a network still coming up
// after the app was backgrounded, a server error. It is NOT a sign-in prompt. The session is
// kept and re-checked automatically; if the server then says it has ended, the user goes to
// sign-in with the reason, exactly as before.
export default function SessionUnreachable() {
  const { retry } = useAuth();
  const [checking, setChecking] = useState(false);

  const tryAgain = async () => {
    setChecking(true);
    try {
      await retry();
    } finally {
      setChecking(false);
    }
  };

  return (
    // Rendered by the guards outside any layout, so it brings its own page background — without
    // it the dark theme put white text on the bare white body.
    <div className="grid min-h-screen place-items-center bg-slate-50 px-6 dark:bg-slate-950">
      <div role="status" aria-live="polite" className="max-w-sm text-center">
        <FiWifiOff aria-hidden="true" className="mx-auto text-3xl text-slate-400" />
        <h1 className="mt-4 text-lg font-semibold text-slate-900 dark:text-white">
          Can’t reach ZoikoStream
        </h1>
        <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">
          Your session is saved on this device. We’ll reconnect as soon as ZoikoStream can be
          reached.
        </p>
        <Button className="mt-5" onClick={tryAgain} disabled={checking}>
          {checking ? "Checking…" : "Try again"}
        </Button>
      </div>
    </div>
  );
}
