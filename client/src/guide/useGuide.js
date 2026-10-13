// Guide conversation state. The server is authoritative for every workflow state (viewer
// flows, lead flows, session state); this hook only mirrors what the API returned and never
// invents a branch, an outcome or a link (Conversation Flow §1.2).
import { useCallback, useEffect, useReducer, useRef } from "react";
import * as api from "./guideApi";

let seq = 0;
const nextId = () => `g${++seq}`;

const initial = {
  status: "closed", // closed | starting | ready | unavailable | ended
  sessionError: null,
  items: [],
  sending: false,
  rateLimitedUntil: 0,
  eventContext: null,
  welcome: null,
  suggested: [],
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
      return { ...state, rateLimitedUntil: Date.now() + action.seconds * 1000 };
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

export function useGuide({ getPageContext, directRoutes }) {
  const [state, dispatch] = useReducer(reducer, initial);
  // Ids are assigned at dispatch time, never inside the reducer (StrictMode may run a reducer
  // twice, which would desynchronise an id generated there).
  const push = (item) => {
    const id = nextId();
    dispatch({ type: "push", item: { id, ...item } });
    return id;
  };
  const createdAtRef = useRef(0);
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const notice = useCallback(
    (tone, text, actions = []) => push({ kind: "notice", tone, text, actions }),
    [],
  );

  // One place that turns API problems into the approved UI states (Figma §14).
  const handleError = useCallback(
    (err) => {
      const code = err?.code;
      if (code === "GUIDE_SESSION_INVALID") {
        api.clearSession();
        // One clear end state instead of a notice per click. A session that is rejected
        // seconds after it was created means the browser never sent its cookie back.
        const young = Date.now() - (createdAtRef.current || 0) < 60_000;
        dispatch({
          type: "ended",
          message: young
            ? "Guide couldn't keep a secure session in this browser. Cookies for this site may be blocked; allow them and start again."
            : "This conversation has ended. Start a new one to keep going.",
        });
        return;
      }
      if (code === "GUIDE_RATE_LIMITED") {
        const secs = err.retryAfter || 30;
        dispatch({ type: "rate_limited", seconds: secs });
        notice("warning", "You have sent several requests in a short period. Please wait a moment and try again.", directRoutes); // WF-16 exact copy
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
        notice("error", "Guide is having trouble right now. You can still use the website directly.", directRoutes);
        return;
      }
      notice("error", err?.message || "Something went wrong. Please try again.");
    },
    [directRoutes, notice],
  );

  const open = useCallback(async () => {
    if (api.getSession() && stateRef.current.status === "ready") return;
    dispatch({ type: "starting" });
    try {
      const data = await api.createSession(getPageContext("launcher"));
      createdAtRef.current = Date.now();
      dispatch({
        type: "started",
        welcome: data.welcome,
        suggested: data.suggested_intents,
        eventContext: data.event_context,
      });
    } catch (err) {
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

  // ── Messages ────────────────────────────────────────────────────────────────────────────
  const send = useCallback(
    async (text, intentHint) => {
      const trimmed = text.trim();
      if (!trimmed || stateRef.current.sending) return;
      if (Date.now() < stateRef.current.rateLimitedUntil) {
        notice("warning", "You have sent several requests in a short period. Please wait a moment and try again.", directRoutes);
        return;
      }
      push({ kind: "user", text: trimmed });
      const placeholderId = push({ kind: "assistant", msg: { text: "" }, streaming: true });
      dispatch({ type: "sending", value: true });
      try {
        const result = await api.sendMessage(
          { clientMessageId: api.uuid(), text: trimmed, intentHint },
          { onDelta: (t) => dispatch({ type: "patch", id: placeholderId, patch: { msg: { text: t } } }) },
        );
        dispatch({
          type: "patch",
          id: placeholderId,
          patch: { msg: result.assistant_message, streaming: false, sessionState: result.session_state },
        });
      } catch (err) {
        dispatch({ type: "remove", id: placeholderId });
        handleError(err);
      } finally {
        dispatch({ type: "sending", value: false });
      }
    },
    [handleError, notice, directRoutes],
  );

  // ── Viewer support flows ────────────────────────────────────────────────────────────────
  const startFlow = useCallback(
    async (flowType) => {
      try {
        const flow = await api.startSupportFlow(flowType);
        push({ kind: "flow", flow });
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
        // Resuming the same flow: replace the earlier card instead of showing two forms.
        const existing = stateRef.current.items.find((it) => it.kind === "lead" && it.flow.flow_id === flow.flow_id);
        if (existing) dispatch({ type: "remove", id: existing.id });
        push({ kind: "lead", flow, outcome: null });
      } catch (err) {
        handleError(err);
      }
    },
    [handleError],
  );

  const handoffTo = useCallback(
    async (target) => {
      try {
        const result = await api.handoff(target);
        push({ kind: "notice", tone: "info", text: result.message, actions: result.actions || [] });
      } catch (err) {
        handleError(err);
      }
    },
    [handleError],
  );

  const end = useCallback(async () => {
    try {
      await api.endSession();
    } catch {
      /* ending is best-effort; the local conversation is cleared regardless */
    }
    dispatch({ type: "ended" });
  }, []);

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
    startFlow,
    stepFlow,
    startLead,
    handoffTo,
    end,
    restart,
    handleError,
    notice,
  };
}
