import { useCallback, useEffect, useRef, useState } from "react";
import { errMsg } from "../api";
import { notify } from "../ui/Toast";

// The write half of the data layer, mirroring useApi's read half.
//
//   const save = useMutation({ success: "Event published", onDone: reload });
//   await save.run(() => api.patch(`/events/${id}`, { status: "published" }));
//
// Exists because the try/await/notify.success/reload/catch/notify.error block was
// hand-copied ~15 times across the org and admin screens, and this module is about to add
// twenty more call sites. One implementation means the error path (errMsg — which must
// always return a STRING, see api.js) can never be forgotten in one of them.
//
// `run` RESOLVES to the response on success and to `null` on failure, so callers can branch
// without a try/catch:  if (await save.run(...)) closeTheDialog();
//
// ponytail: no retry, no queue, no cache invalidation graph. Callers pass onDone (usually
// useApi's reload). Revisit if a screen needs optimistic rollback across several lists.
export default function useMutation({ success, error, onDone } = {}) {
  const [busy, setBusy] = useState(false);
  // Guards the busy reset against a component that unmounted mid-request — a row action
  // that navigates away (EventDetails' delete does exactly that) would otherwise set state
  // on a gone component. The flag is owned by an effect so it cannot go stale.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback(
    async (fn, opts = {}) => {
      setBusy(true);
      try {
        const res = await fn();
        const msg = opts.success ?? success;
        if (msg) notify.success(typeof msg === "function" ? msg(res) : msg);
        (opts.onDone ?? onDone)?.(res);
        return res ?? true;
      } catch (e) {
        notify.error(errMsg(e, opts.error ?? error ?? "Something went wrong"));
        return null;
      } finally {
        if (alive.current) setBusy(false);
      }
    },
    [success, error, onDone]
  );

  return { run, busy };
}
