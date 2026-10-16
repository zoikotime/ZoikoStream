// Ask the signed-in user to re-confirm their password, as a promise.
//
//   const stepUp = useStepUp();
//   const reference = await stepUp.request({ purpose: "high_risk_role_grant", description });
//   if (reference) await api.patch(url, body, { headers: { "X-Step-Up": reference } });
//   ...
//   return <>{page}{stepUp.dialog}</>;
//
// request() resolves with a one-time step-up reference, or null when the user cancels (or the
// component goes away first). Reactive by design: call it when the server answers
// STEP_UP_REQUIRED, so the client never carries its own copy of which actions are high-risk.
// Requests queue — each gets its own confirmation, because a reference is single-use.
import { useCallback, useEffect, useRef, useState } from "react";
import StepUpDialog from "./StepUpDialog";

export const STEP_UP_HEADER = "X-Step-Up";

// True when a failed request was refused only for want of a fresh password confirmation.
export function isStepUpRequired(error) {
  const detail = error?.response?.data?.detail;
  return error?.response?.status === 403 && !!detail && typeof detail === "object" && detail.code === "STEP_UP_REQUIRED";
}

export default function useStepUp() {
  const [queue, setQueue] = useState([]);
  const open = useRef(new Set());
  const seq = useRef(0);

  const request = useCallback((options = {}) => new Promise((resolve) => {
    seq.current += 1;
    const item = { key: seq.current, options, resolve };
    open.current.add(item);
    setQueue((q) => [...q, item]);
  }), []);

  const settle = useCallback((item, value) => {
    if (!open.current.delete(item)) return;
    item.resolve(value);
    setQueue((q) => q.filter((i) => i !== item));
  }, []);

  // Never leave a caller awaiting a dialog that can no longer be answered.
  useEffect(() => {
    const pending = open.current;
    return () => {
      pending.forEach((item) => item.resolve(null));
      pending.clear();
    };
  }, []);

  const head = queue[0];
  const dialog = head ? (
    <StepUpDialog
      key={head.key}
      purpose={head.options.purpose}
      title={head.options.title}
      description={head.options.description}
      confirmLabel={head.options.confirmLabel}
      onConfirmed={(reference) => settle(head, reference)}
      onCancel={() => settle(head, null)}
    />
  ) : null;

  return { request, dialog, pending: Boolean(head) };
}
