// ZoikoStream Guide for the authenticated consoles (ZST-UX-GUIDE-001).
//
// A launcher (Ctrl/Cmd + . , attention badge, no animation) opens a non-modal side panel:
// 440 px drawer, an expanded 960–1200 px workspace, full screen on phones. The page behind
// stays usable — Guide never blocks the console it is helping with.
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Radio, Send } from "lucide-react";
import useOperatorGuide from "./useOperatorGuide";
import Header from "./parts/Header";
import Home from "./parts/Home";
import Message from "./parts/Message";
import HistoryView from "./parts/HistoryView";
import SettingsView from "./parts/SettingsView";
import Thinking from "./parts/Thinking";
import { safeHref } from "./parts/links";
import { OPEN_EVENT, takePendingAsk } from "./askGuide";
import "./operator-guide.css";

const MAX_CHARS = 2000;
export { OPEN_EVENT };

function isShortcut(e) {
  return (e.ctrlKey || e.metaKey) && !e.altKey && e.key === ".";
}

export default function OperatorGuide({ route, routeEventId }) {
  const titleId = useId();
  const inputId = useId();
  const navigate = useNavigate();
  // An "Ask Guide" click that happened before Guide loaded opens it straight away.
  const [initialAsk] = useState(takePendingAsk);
  const [open, setOpen] = useState(!!initialAsk);
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState("chat");
  const [draft, setDraft] = useState("");
  const g = useOperatorGuide({ route, routeEventId, open });
  const launcherRef = useRef(null);
  const inputRef = useRef(null);
  const threadEnd = useRef(null);
  const pendingAsk = useRef(initialAsk);

  const close = useCallback(() => {
    setOpen(false);
    launcherRef.current?.focus();
  }, []);

  // Keyboard: Ctrl/Cmd + . toggles from anywhere in the console.
  useEffect(() => {
    const onKey = (e) => {
      if (isShortcut(e)) {
        e.preventDefault();
        setOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Inline "Ask Guide" entry points (askGuide.js) open Guide with a question and, where the
  // page has one, its event attached: {question, eventId, route?}.
  useEffect(() => {
    const onOpen = (e) => {
      takePendingAsk(); // handled here; nothing is left for a later mount
      setOpen(true);
      setView("chat");
      if (e.detail?.question) pendingAsk.current = e.detail;
    };
    window.addEventListener(OPEN_EVENT, onOpen);
    return () => window.removeEventListener(OPEN_EVENT, onOpen);
  }, []);

  // Focus moves into Guide once, when it opens — never on later renders.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const { boot, send, selectEvent } = g;
  useEffect(() => {
    const request = pendingAsk.current;
    if (open && boot && request) {
      pendingAsk.current = null;
      if (request.eventId) selectEvent(request.eventId);
      send(request.question, request.eventId || undefined);
    }
  }, [open, boot, send, selectEvent]);

  useEffect(() => {
    threadEnd.current?.scrollIntoView?.({ block: "end" });
  }, [g.messages.length, g.pending]);

  const ask = useCallback((text, eventId) => {
    setView("chat");
    if (eventId) g.selectEvent(eventId);
    g.send(text, eventId);
  }, [g]);

  const go = useCallback((href) => {
    const target = safeHref(href);
    if (!target) return;
    navigate(target);
    if (window.matchMedia?.("(max-width: 767px)").matches) setOpen(false);
  }, [navigate]);

  const submit = (e) => {
    e.preventDefault();
    if (!draft.trim() || g.pending) return;
    g.send(draft);
    setDraft("");
  };

  const onComposerKey = (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
  };

  const handlers = {
    ask, navigate: go, choose: g.choose, apply: g.runStructured, confirm: g.confirm, cancel: g.cancel,
  };
  const live = ["live", "degraded"].includes(g.context?.event?.status);
  const lastGuide = [...g.messages].reverse().find((m) => m.role === "guide" && m.intent !== "context.change");
  const unavailable = g.bootError && g.bootError.status !== 403 ? g.bootError.message : null;
  const isAdmin = !!g.boot?.user?.is_org_admin;
  const count = g.attention?.count || 0;

  if (g.bootError?.status === 403) return null; // not a Guide role — render nothing at all

  return (
    <div className="og">
      <button
        ref={launcherRef}
        type="button"
        className="og-launcher"
        aria-expanded={open}
        aria-controls={open ? titleId : undefined}
        aria-keyshortcuts="Control+. Meta+."
        aria-label={`${open ? "Close" : "Open"} Guide${count ? `, ${count} item${count === 1 ? "" : "s"} need attention` : ""}`}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <img className="og-launcher-icon" src="/zoiko-icon.png" alt="" width="22" height="22" />
        <span className="og-launcher-text">Guide</span>
        {count > 0 && <span className="og-launcher-badge" aria-hidden="true">{count > 9 ? "9+" : count}</span>}
      </button>

      {open && (
        <aside
          className={`og-panel${expanded ? " og-expanded" : ""}${live ? " og-live" : ""}`}
          role="dialog"
          aria-modal="false"
          aria-labelledby={titleId}
          onKeyDown={(e) => e.key === "Escape" && close()}
        >
          <Header
            titleId={titleId}
            context={g.context}
            view={view}
            setView={setView}
            expanded={expanded}
            setExpanded={setExpanded}
            onClose={close}
            onNew={() => { g.newConversation(); setView("chat"); }}
            onSelectEvent={g.selectEvent}
            canSettings={isAdmin || !!unavailable}
          />

          {live && view === "chat" && (
            <p className="og-live-banner" role="status">
              <Radio size={14} aria-hidden="true" />
              Live Mode — brief answers only. Go Live, End and recovery stay in the host console.
            </p>
          )}

          <div className="og-body">
            {view === "settings" && <SettingsView onChanged={() => g.newConversation()} />}
            {view === "history" && (
              <HistoryView
                activeId={g.conversationId}
                onOpen={(id) => { g.loadConversation(id); setView("chat"); }}
                onDeleted={(id) => id === g.conversationId && g.newConversation()}
              />
            )}
            {view === "chat" && unavailable && <p className="og-unavailable" role="alert">{unavailable}</p>}
            {view === "chat" && !unavailable && (
              <div className="og-thread" aria-label="Conversation">
                {g.messages.length === 0
                  ? <Home boot={g.boot} context={g.context} attention={g.attention} onAsk={ask} onNavigate={go} onOpenConversation={g.loadConversation} />
                  : g.messages.map((m) => (
                    <Message key={m.id} message={m} handlers={handlers} pending={g.pending} isLast={m === lastGuide} />
                  ))}
                {g.pending && <Thinking live={live} />}
                {g.error && <p className="og-error" role="alert">{g.error}</p>}
                <div ref={threadEnd} />
              </div>
            )}
          </div>

          {view === "chat" && !unavailable && (
            <form className="og-composer" onSubmit={submit}>
              <label htmlFor={inputId} className="og-sr">Ask Guide</label>
              <textarea
                id={inputId}
                ref={inputRef}
                rows={1}
                value={draft}
                maxLength={MAX_CHARS}
                placeholder={g.context?.event ? `Ask about ${g.context.event.title}…` : "Ask about your events, audience or settings…"}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={onComposerKey}
              />
              <button type="submit" className="og-send" aria-label="Send" disabled={!draft.trim() || g.pending}>
                <Send size={18} aria-hidden="true" />
              </button>
            </form>
          )}
          {/* Screen readers hear each new answer once, without re-reading the thread. */}
          <p className="og-sr" aria-live="polite">{lastGuide?.payload?.answer || ""}</p>
        </aside>
      )}
    </div>
  );
}
