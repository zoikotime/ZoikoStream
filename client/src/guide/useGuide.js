// Guide conversation state. The server is authoritative for every workflow state (viewer
// flows, lead flows, session state); this hook only mirrors what the API returned and never
// invents a branch, an outcome or a link (Conversation Flow §1.2).
//
// The hook lives in GuideWidget, which stays mounted while the panel opens and closes, so
// everything kept here (timeline, lead-form drafts, failed messages awaiting retry) survives
// closing and reopening Guide within the session (Figma §10.1, PR-013). Nothing is written to
// localStorage (Security/Privacy §8); ending or restarting the conversation clears it all.
import { useCallback, useEffect, useReducer, useRef } from "react";
import * as api from "./guideApi";
import { track } from "./guideAnalytics";

let seq = 0;
const nextId = () => `g${++seq}`;

// Warn this long before idle expiry (Figma §13 "Timeout/session expiry").
export const IDLE_WARNING_MS = 120_000;

export const RATE_LIMIT_COPY = "You have sent several requests in a short period. Please wait a moment and try again."; // WF-16 exact copy

const initial = {
  status: "closed", // closed | starting | ready | unavailable | ended
  sessionError: null,
  items: [],
  sending: false,
  // RATE_LIMITED (CF §23, CF-AT-32): { until, known }. `known` only when the server sent
  // Retry-After; without it there is no countdown and nothing is invented.
  rateLimit: null,
  eventContext: null,
  welcome: null,
  suggested: [],
  idleTimeoutSeconds: null,
  expiresAt: null,
  activityAt: 0,
  idleWarning: null, // null | { canExtend }
};

function reducer(state, action) {
  switch (action.type) {
    case "starting":
      return { ...state, status: "starting", sessionError: null };
    case "started":
      return {
        ...state,
        status: "ready",
        welcome: action.welcome,
        suggested: action.suggested || [],
        eventContext: action.eventContext || null,
        idleTimeoutSeconds: action.idleTimeoutSeconds || null,
        expiresAt: action.expiresAt || null,
        activityAt: Date.now(),
        idleWarning: null,
      };
    case "unavailable":
      return { ...state, status: "unavailable", sessionError: action.message || null };
    case "ended":
      return { ...initial, status: "ended", sessionError: action.message || null };
    case "reset":
      return { ...initial };
    case "suggested":
      return { ...state, suggested: action.suggested || state.suggested };
    case "sending":
      return { ...state, sending: action.value };
    case "rate_limited":
      return { ...state, rateLimit: action.value };
    case "rate_limit_cleared":
      return state.rateLimit ? { ...state, rateLimit: null } : state;
    case "activity":
      return { ...state, activityAt: action.at };
    case "idle_warning":
      return { ...state, idleWarning: action.value };
    case "push":
      return { ...state, items: [...state.items, action.item] };
    case "patch":
      return {
        ...state,
        items: state.items.map((it) => (it.id === action.id ? { ...it, ...action.patch } : it)),
      };
    case "remove":
      return { ...state, items: state.items.filter((it) => it.id !== action.id) };
    default:
      return state;
  }
}

// Failures where the visitor's text is kept and a Retry is offered (Figma §14, CF §24).
const RETRYABLE = new Set([
  "GUIDE_NETWORK_ERROR", "GUIDE_TIMEOUT", "GUIDE_STREAM_INTERRUPTED", "GUIDE_UNAVAILABLE",
  "GUIDE_RATE_LIMITED", "GUIDE_STATE_CONFLICT",
]);
const isRetryable = (err) => RETRYABLE.has(err?.code) || err?.status >= 500;

const answerEvent = { knowledge_gap: "knowledge_gap", restricted: "restricted", degraded: "degraded" };

function trackAnswer(msg) {
  const sources = Array.isArray(msg?.sources) ? msg.sources : [];
  track("answer_rendered", {
    response_id: msg?.message_id,
    response_type: msg?.answer_class,
    grounded: !!msg?.grounded,
    source_count: sources.length,
    source_ids: sources.map((s) => s.source_id),
    action_count: Array.isArray(msg?.actions) ? msg.actions.length : 0,
  });
  if (answerEvent[msg?.answer_class]) track(answerEvent[msg.answer_class], { response_id: msg.message_id });
}

export function useGuide({ getPageContext, directRoutes }) {
  const [state, dispatch] = useReducer(reducer, initial);
  // Ids are assigned at dispatch time, never inside the reducer (StrictMode may run a reducer
  // twice, which would desynchronise an id generated there).
  const push = (item) => {
    const id = nextId();
    dispatch({ type: "push", item: { id, ...item } });
    return id;
  };
  const patchItem = useCallback((id, patch) => dispatch({ type: "patch", id, patch }), []);
  const createdAtRef = useRef(0);
  const endTrackedRef = useRef(false);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  // Every accepted session call refreshes the server's idle clock; mirror it.
  useEffect(() => api.onActivity((at) => dispatch({ type: "activity", at })), []);

  // "Session-end event is emitted exactly once" (WF-17).
  const trackEnded = useCallback((reason) => {
    if (endTrackedRef.current) return;
    endTrackedRef.current = true;
    track("session_ended", { end_reason: reason });
  }, []);

  const notice = useCallback(
    (tone, text, actions = []) => push({ kind: "notice", tone, text, actions }),
    [],
  );

  const rateLimited = useCallback((err) => {
    const known = Number.isFinite(err?.retryAfter) && err.retryAfter > 0;
    dispatch({ type: "rate_limited", value: { until: known ? Date.now() + err.retryAfter * 1000 : 0, known } });
    track("rate_limited", { retry_after_known: known });
  }, []);

  // One place that turns API problems into the approved UI states (Figma §14).
  const handleError = useCallback(
    (err) => {
      const code = err?.code;
      if (code === "GUIDE_RATE_LIMITED") {
        rateLimited(err); // a stable RATE_LIMITED banner, not a notice per click (CF-AT-32)
        return;
      }
      track("error", { error_class: code || "GUIDE_UNKNOWN", status: err?.status ?? 0 });
      if (code === "GUIDE_SESSION_INVALID") {
        api.clearSession();
        // One clear end state instead of a notice per click. A session that is rejected
        // seconds after it was created means the browser never sent its cookie back.
        const young = Date.now() - (createdAtRef.current || 0) < 60_000;
        trackEnded(young ? "session_rejected" : "expired");
        dispatch({
          type: "ended",
          message: young
            ? "Guide couldn't keep a secure session in this browser. Cookies for this site may be blocked; allow them and start again."
            : "This conversation has ended. Start a new one to keep going.",
        });
        return;
      }
      if (code === "GUIDE_PAYLOAD_TOO_LARGE") {
        notice("warning", err.message || "That message is too long. Please shorten it.");
        return;
      }
      if (code === "GUIDE_STATE_CONFLICT") {
        notice("info", err.message || "That step is no longer current.");
        return;
      }
      if (code === "GUIDE_UNAVAILABLE" || code === "GUIDE_NETWORK_ERROR" || err?.status >= 500 || err?.status === 0) {
        track("degraded", { error_class: code || "GUIDE_UNKNOWN" });
        notice("error", "Guide is having trouble right now. You can still use the website directly.", directRoutes);
        return;
      }
      notice("error", err?.message || "Something went wrong. Please try again.");
    },
    [directRoutes, notice, rateLimited, trackEnded],
  );

  const open = useCallback(async () => {
    if (api.getSession() && stateRef.current.status === "ready") return;
    dispatch({ type: "starting" });
    try {
      const data = await api.createSession(getPageContext("launcher"));
      createdAtRef.current = Date.now();
      endTrackedRef.current = false;
      dispatch({
        type: "started",
        welcome: data.welcome,
        suggested: data.suggested_intents,
        eventContext: data.event_context,
        idleTimeoutSeconds: data.idle_timeout_seconds,
        expiresAt: data.expires_at ? Date.parse(data.expires_at) || null : null,
      });
    } catch (err) {
      track("error", { error_class: err?.code || "GUIDE_UNKNOWN", status: err?.status ?? 0 });
      dispatch({
        type: "unavailable",
        message:
          err?.code === "GUIDE_RATE_LIMITED"
            ? "Guide is busy right now. Please try again in a moment."
            : "Guide is unavailable right now.",
      });
    }
  }, [getPageContext]);

  const refreshContext = useCallback(
    async (entryPoint) => {
      if (!api.getSession()) return;
      try {
        const data = await api.updateContext(getPageContext(entryPoint));
        dispatch({ type: "suggested", suggested: data.suggested_intents });
      } catch {
        /* context refresh is best-effort; never interrupts the visitor */
      }
    },
    [getPageContext],
  );

  const rateLimitActive = () => {
    const rl = stateRef.current.rateLimit;
    return !!(rl?.known && Date.now() < rl.until);
  };

  // Clear the RATE_LIMITED state when the server's Retry-After window has passed.
  useEffect(() => {
    const rl = state.rateLimit;
    if (!rl?.known) return undefined;
    const t = setTimeout(() => dispatch({ type: "rate_limit_cleared" }), Math.max(0, rl.until - Date.now()));
    return () => clearTimeout(t);
  }, [state.rateLimit]);

  // ── Messages ────────────────────────────────────────────────────────────────────────────
  // Delivers the visitor's message `userId`. The client_message_id is the API's idempotency
  // key: once the server has accepted a message it stores the answer under that id and a
  // repeat returns the same stored answer (orchestrator._replay). So a retry reuses the id
  // when the server accepted the message, and uses a new id only when it never did.
  // Resolves to "recover" when the text should go back into the composer (too long, or the
  // session ended) so the visitor never has to retype it (Figma §14 "Expired session").
  const deliver = useCallback(
    async (userId, { text, intentHint, cmid, isRetry = false }) => {
      const placeholderId = push({ kind: "assistant", msg: { text: "" }, streaming: true, replyTo: userId });
      dispatch({ type: "sending", value: true });
      let accepted = false;
      let partial = "";
      track("message_sent", { message_id: cmid, intent_id: intentHint, is_retry: isRetry });
      try {
        const result = await api.sendMessage(
          { clientMessageId: cmid, text, intentHint },
          {
            onAccepted: () => {
              accepted = true;
            },
            onDelta: (t) => {
              partial = t;
              dispatch({ type: "patch", id: placeholderId, patch: { msg: { text: t } } });
            },
          },
        );
        dispatch({
          type: "patch",
          id: placeholderId,
          patch: { msg: result.assistant_message, streaming: false, sessionState: result.session_state },
        });
        dispatch({ type: "patch", id: userId, patch: { failed: null } });
        dispatch({ type: "rate_limit_cleared" });
        trackAnswer(result.assistant_message);
        return "answered";
      } catch (err) {
        if (err?.code === "GUIDE_STATE_CONFLICT") accepted = true; // server holds it, still answering
        if (partial) {
          // Partial text stays, clearly labelled incomplete; never presented as the answer.
          dispatch({ type: "patch", id: placeholderId, patch: { streaming: false, incomplete: true } });
        } else {
          dispatch({ type: "remove", id: placeholderId });
        }
        if (isRetryable(err)) {
          dispatch({ type: "patch", id: userId, patch: { failed: { code: err.code || "GUIDE_UNAVAILABLE", accepted } } });
          if (err.code === "GUIDE_RATE_LIMITED") rateLimited(err);
          else track("error", { error_class: err.code || "GUIDE_UNAVAILABLE", status: err?.status ?? 0 });
          return "failed";
        }
        if (err?.code === "GUIDE_PAYLOAD_TOO_LARGE") dispatch({ type: "remove", id: userId });
        handleError(err);
        return err?.code === "GUIDE_PAYLOAD_TOO_LARGE" || err?.code === "GUIDE_SESSION_INVALID" ? "recover" : "failed";
      } finally {
        dispatch({ type: "sending", value: false });
      }
    },
    [handleError, rateLimited],
  );

  const send = useCallback(
    async (text, intentHint) => {
      const trimmed = text.trim();
      if (!trimmed || stateRef.current.sending || rateLimitActive()) return "skipped";
      const cmid = api.uuid();
      const userId = push({ kind: "user", text: trimmed, intentHint, cmid, failed: null });
      return deliver(userId, { text: trimmed, intentHint, cmid });
    },
    [deliver],
  );

  const retry = useCallback(
    async (userId) => {
      const item = stateRef.current.items.find((it) => it.id === userId);
      if (!item?.failed || stateRef.current.sending || rateLimitActive()) return;
      const cmid = item.failed.accepted ? item.cmid : api.uuid();
      dispatch({ type: "patch", id: userId, patch: { failed: null, cmid } });
      await deliver(userId, { text: item.text, intentHint: item.intentHint, cmid, isRetry: true });
    },
    [deliver],
  );

  // ── Viewer support flows ────────────────────────────────────────────────────────────────
  const startFlow = useCallback(
    async (flowType) => {
      try {
        const flow = await api.startSupportFlow(flowType);
        push({ kind: "flow", flow });
        track("support_flow_started", { flow_type: flow?.flow_type || flowType, step_id: flow?.step?.step_id });
      } catch (err) {
        handleError(err);
      }
    },
    [handleError],
  );

  const stepFlow = useCallback(
    async (itemId, flow, { optionId, command }) => {
      dispatch({ type: "patch", id: itemId, patch: { busy: true } });
      try {
        const next = await api.submitSupportStep(flow.flow_id, {
          stepId: flow.step.step_id,
          optionId,
          command,
        });
        dispatch({ type: "patch", id: itemId, patch: { flow: next, busy: false } });
        track("support_flow_step", { flow_type: flow.flow_type, step_id: flow.step.step_id });
        if (next.status === "resolved" || next.status === "escalated") {
          track("support_flow_completed", { flow_type: next.flow_type || flow.flow_type, outcome: next.status });
        }
        if (next.next_flow) push({ kind: "flow", flow: next.next_flow });
      } catch (err) {
        // A stale step returns the authoritative current state; re-render from it.
        const current = err?.problem?.current;
        dispatch({ type: "patch", id: itemId, patch: { busy: false, ...(current ? { flow: current } : {}) } });
        if (!current) handleError(err);
      }
    },
    [handleError],
  );

  // ── Lead flows ──────────────────────────────────────────────────────────────────────────
  const startLead = useCallback(
    async (kind, sourceIntent) => {
      try {
        const flow = await api.startLeadFlow(kind, sourceIntent);
        // Resuming the same flow: replace the earlier card instead of showing two forms, and
        // carry over what the visitor already typed so nothing is asked twice (PR-013).
        const existing = stateRef.current.items.find((it) => it.kind === "lead" && it.flow.flow_id === flow.flow_id);
        const carried = existing ? { values: existing.values, consent: existing.consent } : {};
        if (existing) dispatch({ type: "remove", id: existing.id });
        push({ kind: "lead", flow, phase: "collecting", ...carried });
        if (!existing) track("lead_started", { flow_kind: flow.flow_kind || kind, source_intent: sourceIntent });
      } catch (err) {
        handleError(err);
      }
    },
    [handleError],
  );

  const handoffTo = useCallback(
    async (target) => {
      track("escalation_selected", { route_type: target });
      try {
        const result = await api.handoff(target);
        push({ kind: "notice", tone: "info", text: result.message, actions: result.actions || [] });
      } catch (err) {
        handleError(err);
      }
    },
    [handleError],
  );

  // ── Session lifetime ────────────────────────────────────────────────────────────────────
  // Any accepted session call resets the server's idle clock; a context refresh is the
  // lightest one the API already offers, so "Stay in conversation" uses it.
  const stayActive = useCallback(async () => {
    if (!api.getSession()) return;
    try {
      const data = await api.updateContext(getPageContext("launcher"));
      dispatch({ type: "suggested", suggested: data.suggested_intents });
      dispatch({ type: "idle_warning", value: null });
    } catch (err) {
      handleError(err);
    }
  }, [getPageContext, handleError]);

  // Warn ~2 minutes before idle (or absolute) expiry; end locally at the deadline, as the
  // server does, so the visitor is never surprised by a rejected message.
  useEffect(() => {
    if (state.status !== "ready" || !state.idleTimeoutSeconds) return undefined;
    const idleDeadline = state.activityAt + state.idleTimeoutSeconds * 1000;
    const absDeadline = state.expiresAt || Infinity;
    const deadline = Math.min(idleDeadline, absDeadline);
    const canExtend = idleDeadline <= absDeadline;
    const now = Date.now();
    const timers = [];
    if (now >= deadline - IDLE_WARNING_MS) {
      if (!state.idleWarning || state.idleWarning.canExtend !== canExtend) {
        dispatch({ type: "idle_warning", value: { canExtend } });
      }
    } else {
      if (state.idleWarning) dispatch({ type: "idle_warning", value: null });
      timers.push(setTimeout(() => dispatch({ type: "idle_warning", value: { canExtend } }), deadline - IDLE_WARNING_MS - now));
    }
    timers.push(
      setTimeout(() => {
        api.clearSession();
        trackEnded("idle_expiry");
        dispatch({ type: "ended", message: "This conversation ended because it was inactive. Start a new one to keep going." });
      }, Math.max(0, deadline - now)),
    );
    return () => timers.forEach(clearTimeout);
  }, [state.status, state.idleTimeoutSeconds, state.expiresAt, state.activityAt, state.idleWarning, trackEnded]);

  const end = useCallback(async () => {
    try {
      await api.endSession();
    } catch {
      /* ending is best-effort; the local conversation is cleared regardless */
    }
    trackEnded("user_ended");
    dispatch({ type: "ended" });
  }, [trackEnded]);

  const restart = useCallback(async () => {
    api.clearSession();
    dispatch({ type: "reset" });
    await open();
  }, [open]);

  return {
    state,
    dispatch,
    open,
    refreshContext,
    send,
    retry,
    startFlow,
    stepFlow,
    startLead,
    handoffTo,
    patchItem,
    stayActive,
    end,
    restart,
    handleError,
    notice,
  };
}
