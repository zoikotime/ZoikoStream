// Operator Guide state: context, conversation, proposals. The server is authoritative for
// everything — this hook only mirrors what it returns and never infers permissions.
import { useCallback, useEffect, useRef, useState } from "react";
import { guideErrorMessage, newIdempotencyKey, opGuide } from "./operatorGuideApi";

const ATTENTION_POLL_MS = 60_000;

export default function useOperatorGuide({ route, routeEventId }) {
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

  // Bootstrap whenever the console route or chosen event changes.
  useEffect(() => {
    let alive = true;
    opGuide.bootstrap(route, eventId)
      .then((data) => {
        if (!alive) return;
        setBoot(data);
        setContext(data.context);
        setAttention(data.attention || { count: 0, items: [] });
        setBootError(null);
      })
      .catch((e) => alive && setBootError({ status: e?.response?.status, message: guideErrorMessage(e) }));
    return () => {
      alive = false;
    };
  }, [route, eventId]);

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

  // `eventOverride` lets a just-chosen event apply to this request before state settles.
  const post = useCallback(async (body, userText, eventOverride) => {
    setPending(true);
    setError(null);
    if (userText) {
      setMessages((m) => [...m, { id: `local-${Date.now()}`, role: "user", text: userText }]);
    }
    try {
      const res = await opGuide.send({
        route, event_id: eventOverride || eventId || undefined,
        conversation_id: conversationId || undefined, ...body,
      });
      setConversationId(res.conversation_id);
      if (res.context) setContext(res.context);
      setMessages((m) => [...m, res.message]);
      return res;
    } catch (e) {
      setError(guideErrorMessage(e));
      return null;
    } finally {
      setPending(false);
    }
  }, [route, eventId, conversationId]);

  const send = useCallback((text) => {
    const clean = (text || "").trim();
    if (!clean || pending) return null;
    lastSent.current = clean;
    return post({ text: clean }, clean);
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

  const selectEvent = useCallback((id) => setPinnedEventId(id || null), [setPinnedEventId]);

  return {
    boot, bootError, context, attention, messages, pending, error, conversationId,
    send, choose, runStructured, confirm, cancel, newConversation, loadConversation, selectEvent,
    setError,
  };
}
