// Conversation building blocks shared by the Guide popup (GuideWidget) and the full Guide page
// (GuidePage): notices, failed sends, rate-limit and idle banners, the error/offline state
// panel, the flow-switch confirmation, and the timeline that renders every conversation item.
import { useEffect, useRef, useState } from "react";
import { AlertTriangle, Clock, Info, RotateCcw, XCircle } from "lucide-react";
import { RATE_LIMIT_COPY } from "../useGuide";
import AssistantMessage from "./AssistantMessage";
import SupportStep from "./SupportStep";
import LeadForm from "./LeadForm";
import Actions from "./Actions";
import { useNestedLayer } from "./nested";
import { FLOW_NAMES } from "./conversationKit";

const NOTICE_ICON = { warning: AlertTriangle, error: XCircle, info: Info, success: Info };

export function Notice({ item, onAction }) {
  const Icon = NOTICE_ICON[item.tone] || Info;
  return (
    <div>
      <div className={`zg-notice zg-notice--${item.tone}`} role={item.tone === "error" ? "alert" : "status"}>
        <Icon aria-hidden="true" />
        <span>{item.text}</span>
      </div>
      <Actions actions={item.actions} onAction={onAction} />
    </div>
  );
}

// A message that did not get an answer: the text stays, with Retry (Figma §14, CF §24).
const FAILED_COPY = {
  GUIDE_TIMEOUT: "Guide took too long to answer. Your message is kept, so you can retry.",
  GUIDE_STREAM_INTERRUPTED: "The answer was interrupted. Your message is kept, so you can retry.",
  GUIDE_NETWORK_ERROR: "Guide couldn't be reached. Your message is kept, so you can retry.",
  GUIDE_STATE_CONFLICT: "This message is still being answered. Retry in a moment.",
  GUIDE_RATE_LIMITED: "Your message wasn't sent yet. You can retry when the wait is over.",
};

export function FailedSend({ failed, disabled, onRetry, routes, onAction }) {
  const copy = FAILED_COPY[failed.code];
  const limited = failed.code === "GUIDE_RATE_LIMITED";
  return (
    <div className="zg-failed">
      <div className={`zg-notice zg-notice--${limited ? "warning" : "error"}`} role={limited ? "status" : "alert"}>
        {limited ? <AlertTriangle aria-hidden="true" /> : <XCircle aria-hidden="true" />}
        <span>{copy || "Guide is having trouble right now. You can retry or use the website directly."}</span>
      </div>
      <div className="zg-actions">
        <button type="button" className="zg-btn zg-btn--secondary" disabled={disabled} onClick={onRetry}>
          <RotateCcw aria-hidden="true" size={16} /> Retry
        </button>
      </div>
      {!copy && <Actions actions={routes} onAction={onAction} />}
    </div>
  );
}

// RATE_LIMITED (WF-16, CF §23, CF-AT-32): stable state with the approved copy, direct routes,
// and — only when the server sent Retry-After — an accessible countdown. The countdown text
// is a role="timer" (not announced every second) and works without animation.
export function RateLimitBanner({ rateLimit, routes, onAction }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!rateLimit.known) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [rateLimit]);
  const secs = rateLimit.known ? Math.max(0, Math.ceil((rateLimit.until - now) / 1000)) : null;
  return (
    <div className="zg-banner">
      <div className="zg-notice zg-notice--warning" role="status">
        <AlertTriangle aria-hidden="true" />
        <div>
          <span>{RATE_LIMIT_COPY}</span>
          {secs !== null && (
            <div className="zg-meta" role="timer" aria-live="off">
              {secs > 0 ? `You can try again in ${secs} ${secs === 1 ? "second" : "seconds"}.` : "You can try again now."}
            </div>
          )}
        </div>
      </div>
      <Actions actions={routes} onAction={onAction} />
    </div>
  );
}

// Polite, non-blocking warning before session expiry (Figma §13 "Timeout/session expiry").
export function IdleBanner({ warning, onStay }) {
  return (
    <div className="zg-banner">
      <div className="zg-notice" role="status">
        <Clock aria-hidden="true" />
        <span>
          {warning.canExtend
            ? "This conversation will end soon because it has been inactive."
            : "This conversation will end soon. You can start a new one at any time."}
        </span>
      </div>
      {warning.canExtend && (
        <div className="zg-actions" style={{ marginTop: 8 }}>
          <button type="button" className="zg-btn zg-btn--secondary" onClick={onStay}>
            Stay in conversation
          </button>
        </div>
      )}
    </div>
  );
}

// Guide/ErrorState (Figma §5: Unavailable, Offline, ...). Focus moves to the error heading,
// then the direct routes follow in tab order (Figma §13.2 "Unavailable").
export function StatePanel({ icon: Icon, title, text, children, role = "status", focusHeading = false }) {
  const ref = useRef(null);
  useEffect(() => {
    if (!focusHeading) return undefined;
    const id = requestAnimationFrame(() => ref.current?.focus());
    return () => cancelAnimationFrame(id);
  }, [focusHeading]);
  return (
    <div className="zg-state" role={role}>
      <div className="zg-state-icon"><Icon aria-hidden="true" /></div>
      <h3 ref={ref} tabIndex={-1} data-zg-initial-focus={focusHeading ? "" : undefined}>{title}</h3>
      <p>{text}</p>
      {children}
    </div>
  );
}

// C3: confirmation before a different viewer issue replaces the active flow's progress
// (CF §3 "reset/restart only the affected support flow with user confirmation"). In-panel,
// never a modal over the shell (Figma §9.1); Escape keeps the current steps.
export function ConfirmReplace({ pending, onConfirm, onKeep }) {
  const headingRef = useRef(null);
  useNestedLayer(true, onKeep);
  useEffect(() => {
    headingRef.current?.focus();
  }, []);
  const current = FLOW_NAMES[pending.activeFlowType] || "viewer help";
  const next = FLOW_NAMES[pending.flowType] || "viewer help";
  return (
    <section className="zg-card zg-confirm" role="alertdialog" aria-labelledby="zg-confirm-h" aria-describedby="zg-confirm-d">
      <h3 id="zg-confirm-h" tabIndex={-1} ref={headingRef}>Switch to {next}?</h3>
      <p id="zg-confirm-d" className="zg-confirm-text">
        Your progress in {current} will be cleared. You can come back to it later by starting it again.
      </p>
      <div className="zg-actions">
        <button type="button" className="zg-btn zg-btn--primary" onClick={onConfirm}>
          Switch topic
        </button>
        <button type="button" className="zg-btn zg-btn--ghost" onClick={onKeep}>
          Keep current steps
        </button>
      </div>
    </section>
  );
}

// Every conversation item (user turns, answers, viewer flows, lead forms, notices) plus the
// flow-switch confirmation, in order.
export function ConversationItems({
  g, directRoutes, online, rlActive, onAction, onResume, onQuick, onKeepFlow,
}) {
  const { state } = g;
  return (
    <>
      {state.items.map((item) => {
        if (item.kind === "user") {
          return (
            <div key={item.id} className="zg-msg zg-msg--user">
              <p className="zg-speaker" aria-hidden="true">You</p>
              <span className="zg-sr">You said:</span>
              <div className="zg-user">{item.text}</div>
              {item.failed && (
                <FailedSend
                  failed={item.failed}
                  disabled={state.sending || rlActive || !online}
                  onRetry={() => g.retry(item.id)}
                  routes={directRoutes}
                  onAction={onAction}
                />
              )}
            </div>
          );
        }
        if (item.kind === "assistant") {
          return (
            <AssistantMessage
              key={item.id}
              item={item}
              onAction={onAction}
              onResume={onResume}
              onSuggest={onQuick}
              onClarify={(label) => g.send(label)}
              onPatch={g.patchItem}
            />
          );
        }
        if (item.kind === "flow") {
          return <SupportStep key={item.id} item={item} onStep={g.stepFlow} onAction={onAction} onPatch={g.patchItem} />;
        }
        if (item.kind === "lead") return <LeadForm key={item.id} item={item} onAction={onAction} onPatch={g.patchItem} />;
        if (item.kind === "notice") return <Notice key={item.id} item={item} onAction={onAction} />;
        return null;
      })}
      {state.confirmReplace && (
        <ConfirmReplace
          pending={state.confirmReplace}
          onConfirm={() => g.startFlow(state.confirmReplace.flowType, { confirmReplace: true })}
          onKeep={() => {
            g.cancelReplace();
            onKeepFlow?.();
          }}
        />
      )}
    </>
  );
}
