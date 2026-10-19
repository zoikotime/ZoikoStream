// Operator Guide state: context, conversation, proposals. The server is authoritative for
// everything — this hook only mirrors what it returns and never infers permissions.
import { useCallback, useEffect, useRef, useState } from "react";
import { guideErrorMessage, newIdempotencyKey, opGuide } from "./operatorGuideApi";

const ATTENTION_POLL_MS = 60_000;
// Live Mode (§6.9): while Guide is open on a live event, context is re-read often enough that
// the Live banner appears and disappears with the broadcast, not minutes later.
const LIVE_REFRESH_MS = 15_000;
const LIVE = ["live", "degraded"];

const isContextNotice = (m) => m.intent === "context.change";

// The thread notice for a context change (§4.2), placed before the question it applies to.
function withNotice(messages, notice) {
  const rest = messages.filter((m) => !(isContextNotice(m) && m.id.startsWith("local-")));
  if (!notice) return rest;
  let at = rest.length;
  while (at > 0 && rest[at - 1].role !== "user") at -= 1;
  at = Math.max(at - 1, 0);
  return [...rest.slice(0, at), notice, ...rest.slice(at)];
}

export default function useOperatorGuide({ route, routeEventId, open = false }) {
  const [boot, setBoot] = useState(null);           // bootstrap payload
  const [bootError, setBootError] = useState(null);
  const [context, setContext] = useState(null);     // org / workspace / event / environment
  // A user-selected context event, remembered per console route: navigating to another page
  // falls back to that page's own event without an effect having to reset state.
  const [pin, setPin] = useState({ route: null, id: null });
  const pinnedEventId = pin.route === route ? pin.id : null;
  const setPinnedEventId = useCallback((id) => setPin({ route, id }), [route]);
  const [conversationId, setConversationId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const [attention, setAttention] = useState({ count: 0, items: [] });
  const lastSent = useRef(null);

  const eventId = pinnedEventId || routeEventId || null;

  const applyBoot = useCallback((data) => {
    setBoot(data);
    setContext(data.context);
    setAttention(data.attention || { count: 0, items: [] });
    setBootError(null);
  }, []);

  // Bootstrap whenever the console route or chosen event changes.
  useEffect(() => {
    let alive = true;
    opGuide.bootstrap(route, eventId)
      .then((data) => alive && applyBoot(data))
      .catch((e) => alive && setBootError({ status: e?.response?.status, message: guideErrorMessage(e) }));
    return () => {
      alive = false;
    };
  }, [route, eventId, applyBoot]);

  // Attention badge: a slow poll while the tab is visible; never an animation.
  useEffect(() => {
    if (bootError) return undefined;
    const tick = () => {
      if (document.visibilityState !== "visible") return;
      opGuide.attention().then(setAttention).catch(() => {});
    };
    const id = setInterval(tick, ATTENTION_POLL_MS);
    return () => clearInterval(id);
  }, [bootError]);

  // Re-read context when Guide opens, then every LIVE_REFRESH_MS while it is open on a live
  // event and the tab is visible. A failed refresh keeps the last good context.
  const live = LIVE.includes(context?.event?.status);
  useEffect(() => {
    if (!open || bootError) return undefined;
    let alive = true;
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      opGuide.bootstrap(route, eventId).then((data) => alive && applyBoot(data)).catch(() => {});
    };
    refresh();
    const id = live ? setInterval(refresh, LIVE_REFRESH_MS) : null;
    return () => {
      alive = false;
      if (id) clearInterval(id);
    };
  }, [open, live, route, eventId, applyBoot, bootError]);

  // `eventOverride` lets a just-chosen event apply to this request before state settles.
  const post = useCallback(async (body, userText, eventOverride) => {
    setPending(true);
    setError(null);
    if (userText) {
      setMessages((m) => [...m, { id: `local-${Date.now()}`, role: "user", text: userText }]);
    }
    const sentEvent = eventOverride || eventId || null;
    try {
      const res = await opGuide.send({
        route, event_id: sentEvent || undefined,
        conversation_id: conversationId || undefined, ...body,
      });
      setConversationId(res.conversation_id);
      if (res.context) {
        setContext(res.context);
        // An event named in the message (or reused by a follow-up) stays the context for the
        // conversation instead of silently reverting on the next turn (§4.2).
        const now = res.context.event?.id;
        if (now && now !== sentEvent) setPinnedEventId(now);
      }
      setMessages((m) => [...withNotice(m, res.context_notice), res.message]);
      return res;
    } catch (e) {
      setError(guideErrorMessage(e));
      return null;
    } finally {
      setPending(false);
    }
  }, [route, eventId, conversationId, setPinnedEventId]);

  const send = useCallback((text, eventOverride) => {
    const clean = (text || "").trim();
    if (!clean || pending) return null;
    lastSent.current = clean;
    return post({ text: clean }, clean, eventOverride);
  }, [post, pending]);

  // A Guide-rendered choice: pick an event (then re-ask), or run a structured action.
  const choose = useCallback((option, block) => {
    if (block.kind === "event" && option.value?.event_id) {
      setPinnedEventId(option.value.event_id);
      if (block.resend && lastSent.current) {
        post({ text: lastSent.current }, lastSent.current, option.value.event_id);
      }
      return;
    }
    if (option.value?.action_id) {
      post({ action: { action_id: option.value.action_id, params: option.value.params || {} } }, option.label);
    }
  }, [post, setPinnedEventId]);

  const runStructured = useCallback((apply) => post(
    { action: { action_id: apply.action_id, params: apply.params || {} } }, apply.label,
  ), [post]);

  // Proposals: the key is generated once per confirm click and reused on retry of the SAME
  // click, so a network retry can never execute twice.
  const updateProposal = useCallback((card) => {
    setMessages((m) => m.map((msg) => {
      const blocks = msg.payload?.blocks;
      if (!blocks?.some((b) => b.type === "proposal" && b.id === card.id)) return msg;
      return { ...msg, payload: { ...msg.payload, blocks: blocks.map((b) => (b.type === "proposal" && b.id === card.id ? card : b)) } };
    }));
  }, []);

  const confirm = useCallback(async (proposal) => {
    const key = newIdempotencyKey();
    try {
      const card = await opGuide.confirm(proposal.id, key);
      updateProposal(card);
      return card;
    } catch (e) {
      const message = guideErrorMessage(e);
      updateProposal({ ...proposal, status: "error", error: message });
      return null;
    }
  }, [updateProposal]);

  const cancel = useCallback(async (proposal) => {
    try {
      updateProposal(await opGuide.cancel(proposal.id));
    } catch (e) {
      updateProposal({ ...proposal, error: guideErrorMessage(e) });
    }
  }, [updateProposal]);

  const newConversation = useCallback(() => {
    setConversationId(null);
    setMessages([]);
    setError(null);
  }, []);

  const loadConversation = useCallback(async (id) => {
    try {
      const data = await opGuide.conversation(id);
      setConversationId(data.id);
      setMessages(data.messages);
      setError(null);
    } catch (e) {
      setError(guideErrorMessage(e));
    }
  }, []);

  // Choosing an event mid-conversation is shown in the thread at once (§4.2); the server's
  // own notice replaces this local one with the next answer.
  const selectEvent = useCallback((id, title) => {
    setPinnedEventId(id || null);
    if (title === undefined) return;
    setMessages((m) => (m.length === 0 ? m : [...m.filter((x) => !(isContextNotice(x) && x.id.startsWith("local-"))), {
      id: `local-ctx-${Date.now()}`, role: "guide", intent: "context.change",
      text: id ? `Context: now ${title}` : "Context: no event selected — answers now cover your organization.",
    }]));
  }, [setPinnedEventId]);

  return {
    boot, bootError, context, attention, messages, pending, error, conversationId,
    send, choose, runStructured, confirm, cancel, newConversation, loadConversation, selectEvent,
    setError,
  };
}
