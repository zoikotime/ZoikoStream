// ZoikoStream Guide — the public website assistant (ZST-UX-FIGMA-PUB-GUIDE-001).
// Lazy-loaded by GuideMount. Owns the launcher (WF-01), the panel shell (WF-02 / WF-18
// mobile), the conversation timeline and the composer. Workflow state comes from the server.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, AlertTriangle, CalendarCheck, Captions, Code2, Compass, Headset, Info, LifeBuoy,
  Lock, LogOut, MonitorPlay, MoreVertical, RotateCcw, SendHorizontal, ShieldCheck, Sparkles, Tag,
  WifiOff, X, XCircle,
} from "lucide-react";
import "./guide.css";
import * as api from "./guideApi";
import { buildPageContext, getGuideEventContextToken } from "./pageContext";
import { viewerEventId, watchViewerEventContext } from "./viewerEventContext";
import { useGuide } from "./useGuide";
import AssistantMessage from "./parts/AssistantMessage";
import GuideAvatar from "./parts/GuideAvatar";
import SupportStep from "./parts/SupportStep";
import LeadForm from "./parts/LeadForm";
import Actions from "./parts/Actions";
import { isSafeHref } from "./parts/actionUtils";

// Quick intents that start a typed flow directly instead of asking the model (Conversation
// Flow §4: quick actions route to the same engines as free text).
const QUICK_FLOWS = {
  "I-VIEWER": ["support", "viewer_other"],
  "I-VIEWER-ACCESS": ["support", "viewer_access"],
  "I-PLAYBACK": ["support", "playback"],
  "I-CAPTIONS": ["support", "captions"],
  "I-REPLAY": ["support", "replay"],
  "I-DEMO": ["lead", "demo"],
  "I-SALES": ["lead", "sales"],
};

// Decorative icon per suggested topic (labels come from the server's intent catalog).
const INTENT_ICON = {
  "I-PRODUCT": Sparkles, "I-SOLUTION": Compass, "I-PRICING": Tag, "I-DEMO": CalendarCheck,
  "I-SALES": Headset, "I-VIEWER": LifeBuoy, "I-VIEWER-ACCESS": LifeBuoy, "I-PLAYBACK": MonitorPlay,
  "I-CAPTIONS": Captions, "I-REPLAY": RotateCcw, "I-STATUS": Activity, "I-PRIVACY": Lock,
  "I-SECURITY": ShieldCheck, "I-DEVELOPER": Code2, "I-HUMAN": Headset,
};

const NOTICE_ICON = { warning: AlertTriangle, error: XCircle, info: Info, success: Info };

function Notice({ item, onAction }) {
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

const PUBLIC_STATUS = {
  live: "Live now", starting_soon: "Starting soon", scheduled: "Scheduled", ended: "Ended",
  replay_available: "Replay available", cancelled: "Cancelled", postponed: "Postponed",
};

function publicStart(iso, timeZone) {
  if (!iso) return null;
  const opts = { weekday: "long", month: "long", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" };
  try {
    return new Intl.DateTimeFormat("en-US", { ...opts, ...(timeZone ? { timeZone } : {}) }).format(new Date(iso));
  } catch {
    return new Intl.DateTimeFormat("en-US", { ...opts, timeZone: "UTC" }).format(new Date(iso));
  }
}

function PublicContext({ context }) {
  const rows = [
    ["Event", context.event_display_name],
    ["Status", PUBLIC_STATUS[context.public_status]],
    ["Public start time", publicStart(context.public_start_at, context.public_timezone)],
  ].filter(([, v]) => v);
  if (!rows.length) return null;
  return (
    <section className="zg-context" aria-labelledby="zg-context-h">
      <p className="zg-eyebrow-label" id="zg-context-h">Public context</p>
      <dl>
        {rows.map(([k, v]) => (
          <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
        ))}
      </dl>
    </section>
  );
}

function StatePanel({ icon: Icon, title, text, children, role = "status" }) {
  return (
    <div className="zg-state" role={role}>
      <div className="zg-state-icon"><Icon aria-hidden="true" /></div>
      <h3>{title}</h3>
      <p>{text}</p>
      {children}
    </div>
  );
}

export default function GuideWidget({ page, pathname }) {
  const navigate = useNavigate();
  const [boot, setBoot] = useState(null); // bootstrap config; null until loaded
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const launcherRef = useRef(null);
  const titleRef = useRef(null);
  const bodyRef = useRef(null);
  const inputRef = useRef(null);
  const lastPageRef = useRef(pathname);

  const directRoutes = useMemo(() => boot?.direct_routes || [], [boot]);
  const getPageContext = useCallback(
    (entryPoint) =>
      buildPageContext(pathname, page || { page_id: "other", page_type: "other" }, {
        entryPoint,
        eventToken: getGuideEventContextToken(),
      }),
    [pathname, page],
  );
  const g = useGuide({ getPageContext, directRoutes });
  const { state } = g;

  // Bootstrap: lightweight, creates no session (API §6). Disabled/failed => render nothing.
  useEffect(() => {
    if (!page || boot) return;
    let alive = true;
    api
      .bootstrap({ pageId: page.page_id, pageType: page.page_type })
      .then((b) => alive && setBoot(b))
      .catch(() => alive && setBoot({ enabled: false }));
    return () => {
      alive = false;
    };
  }, [page, boot]);

  // SPA navigation: refresh public page context for an open conversation.
  useEffect(() => {
    if (lastPageRef.current !== pathname) {
      lastPageRef.current = pathname;
      if (page) g.refreshContext("launcher");
    }
  }, [pathname, page, g]);

  // Viewer page: load the signed facts about the event being watched, and refresh an open
  // conversation's context whenever a fresh token arrives (no session = nothing to refresh;
  // the next session starts with it).
  const viewerEvent = viewerEventId(pathname);
  const { refreshContext } = g;
  useEffect(() => {
    if (!viewerEvent || !boot?.enabled) return undefined;
    return watchViewerEventContext(viewerEvent, () => refreshContext("launcher"));
  }, [viewerEvent, boot?.enabled, refreshContext]);

  const openPanel = useCallback(() => {
    setOpen(true);
    if (state.status === "closed" || state.status === "unavailable") g.open();
  }, [g, state.status]);

  const closePanel = useCallback(() => {
    setOpen(false);
    setMenuOpen(false);
    requestAnimationFrame(() => launcherRef.current?.focus()); // focus returns to invoker
  }, []);

  // Focus enters Guide on open (Figma §13.2).
  useEffect(() => {
    if (open) requestAnimationFrame(() => titleRef.current?.focus());
  }, [open]);

  // Escape closes the panel (progress is server-side, so nothing is lost).
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        if (menuOpen) setMenuOpen(false);
        else closePanel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, menuOpen, closePanel]);

  // Follow new content only while the visitor is at the bottom (Figma §9.2).
  const onScroll = () => {
    const el = bodyRef.current;
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 48);
  };
  useEffect(() => {
    if (atBottom && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [state.items, atBottom]);

  // Completed answers are announced once, politely; streaming tokens are not (Figma §13).
  const announce = useMemo(() => {
    const last = [...state.items].reverse().find((i) => i.kind === "assistant" && !i.streaming);
    return last?.msg?.text || "";
  }, [state.items]);

  const onAction = useCallback(
    (a) => {
      if (a.type === "__local" && a.action_id === "__new_session") return g.restart();
      if (a.type === "start_support_flow") return g.startFlow(a.flow_type || "viewer_other");
      if (a.type === "start_lead_flow") return g.startLead(a.action_id === "talk-to-sales-flow" ? "sales" : "demo");
      if (a.type === "handoff" && a.target === "organizer") return g.handoffTo("organizer");
      if (isSafeHref(a.href)) {
        // In-app navigation keeps the conversation (Guide stays mounted across routes).
        navigate(a.href);
        if (window.innerWidth < 768) closePanel();
      }
      return undefined;
    },
    [g, navigate, closePanel],
  );

  const onQuick = (intent) => {
    const route = QUICK_FLOWS[intent.intent_id];
    if (route?.[0] === "support") return g.startFlow(route[1]);
    if (route?.[0] === "lead") return g.startLead(route[1], intent.intent_id);
    return g.send(intent.label, intent.intent_id);
  };

  const onResume = (resume) => {
    if (resume.kind === "lead_flow") return g.startLead("demo");
    const el = bodyRef.current?.querySelector(`[data-flow-id="${resume.flow_id}"]`);
    el?.scrollIntoView({ block: "center" });
    el?.querySelector("h3")?.focus();
    return undefined;
  };

  const submit = (e) => {
    e.preventDefault();
    if (!draft.trim() || state.sending) return;
    g.send(draft);
    setDraft("");
    setAtBottom(true);
  };

  // Composer autogrow: 40px -> 144px, then scroll inside (Figma §3.4).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(144, Math.max(40, el.scrollHeight))}px`;
  }, [draft]);

  if (!boot?.enabled) return null;
  if (!page && !open) return null; // ineligible page: no launcher

  const maxChars = boot.limits?.max_message_chars || 4000;
  const isViewer = page?.page_type === "viewer";
  const header = state.eventContext?.event_display_name;
  const canType = state.status === "ready";

  return (
    <div className="zg">
      {!open && (
        <button
          ref={launcherRef}
          type="button"
          className={`zg-launcher${isViewer ? " zg-launcher--viewer" : ""}`}
          onClick={openPanel}
          aria-haspopup="dialog"
          aria-label="Open ZoikoStream Guide"
          title="Open ZoikoStream Guide"
        >
          <GuideAvatar />
        </button>
      )}

      {open && (
        <section className="zg-panel" role="dialog" aria-modal="false" aria-labelledby="zg-title">
          <header className="zg-header">
            <GuideAvatar size="lg" online />
            <div className="zg-titles" ref={titleRef} tabIndex={-1}>
              <h2 id="zg-title">Guide</h2>
              <p>{isViewer ? (header ? `Viewer help · ${header}` : "Viewer help") : "Public AI Assistant"}</p>
            </div>
            <button
              type="button"
              className="zg-iconbtn"
              aria-label="Conversation options"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((m) => !m)}
            >
              <MoreVertical aria-hidden="true" />
            </button>
            <button type="button" className="zg-iconbtn" aria-label="Close Guide" onClick={closePanel}>
              <X aria-hidden="true" />
            </button>
            {menuOpen && (
              <div className="zg-menu" role="menu">
                <button
                  role="menuitem"
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    onAction({ type: "navigate", href: boot.privacy?.privacy_notice_href || "/privacy", label: "Privacy" });
                  }}
                >
                  <Lock aria-hidden="true" /> Privacy notice
                </button>
                {state.status === "ready" && (
                  <button
                    role="menuitem"
                    type="button"
                    onClick={() => {
                      setMenuOpen(false);
                      g.end();
                    }}
                  >
                    <LogOut aria-hidden="true" /> End conversation
                  </button>
                )}
              </div>
            )}
          </header>

          <div className="zg-body" ref={bodyRef} onScroll={onScroll}>
            <div className="zg-stack">
              {state.status === "starting" && (
                <div className="zg-row">
                  <GuideAvatar />
                  <div className="zg-bubble">
                    <span className="zg-typing" aria-label="Starting Guide">
                      <span />
                      <span />
                      <span />
                    </span>
                  </div>
                </div>
              )}

              {state.status === "unavailable" && (
                // WF-15: Guide failure is independent of the site; direct routes remain.
                <StatePanel
                  icon={WifiOff}
                  role="alert"
                  title="Guide is temporarily unavailable."
                  text="You can still use ZoikoStream normally."
                >
                  <Actions actions={directRoutes} onAction={onAction} max={4} />
                  <div className="zg-actions" style={{ justifyContent: "center" }}>
                    <button type="button" className="zg-btn zg-btn--ghost" onClick={() => g.open()}>Try again</button>
                  </div>
                </StatePanel>
              )}

              {state.status === "ended" && (
                <StatePanel
                  icon={Info}
                  title="Conversation ended"
                  text={state.sessionError || "Nothing from this conversation is used to personalize future visits."}
                >
                  <div className="zg-actions">
                    <button type="button" className="zg-btn zg-btn--primary" onClick={() => g.restart()}>
                      Start new conversation
                    </button>
                  </div>
                </StatePanel>
              )}

              {state.status === "ready" && (
                <div className="zg-welcome">
                  <GuideAvatar size="xl" />
                  <h3>{isViewer ? "Help with this event" : "Hi there! I'm Guide."}</h3>
                  <p>
                    {isViewer
                      ? "I can walk you through access, playback, captions and replay, one step at a time."
                      : "Ask me about ZoikoStream, pricing and demos, or get help watching an event."}
                  </p>
                  {isViewer && state.eventContext && <PublicContext context={state.eventContext} />}
                  {state.suggested.length > 0 && (
                    <>
                      <p className="zg-eyebrow-label" id="zg-topics">Popular topics</p>
                      <div className="zg-quick" role="group" aria-labelledby="zg-topics">
                        {state.suggested.map((i) => {
                          const Icon = INTENT_ICON[i.intent_id] || Sparkles;
                          return (
                            <button key={i.intent_id} type="button" className="zg-quick-card" onClick={() => onQuick(i)}>
                              <span className="zg-quick-icon"><Icon aria-hidden="true" /></span>
                              <span>{i.label}</span>
                            </button>
                          );
                        })}
                      </div>
                    </>
                  )}
                  {isViewer && (
                    <p className="zg-fineprint">Private organizer details are not available in public chat.</p>
                  )}
                  <p className="zg-fineprint">Answers are AI-generated from ZoikoStream&apos;s public information.</p>
                </div>
              )}

              {state.status === "ready" && state.items.map((item) => {
                if (item.kind === "user") {
                  return (
                    <div key={item.id} className="zg-row zg-row--user">
                      <span className="zg-sr">You said:</span>
                      <div className="zg-user">{item.text}</div>
                    </div>
                  );
                }
                if (item.kind === "assistant") {
                  return (
                    <AssistantMessage key={item.id} item={item} onAction={onAction} onResume={onResume} onSuggest={onQuick} />
                  );
                }
                if (item.kind === "flow") {
                  return <SupportStep key={item.id} item={item} onStep={g.stepFlow} onAction={onAction} />;
                }
                if (item.kind === "lead") return <LeadForm key={item.id} item={item} onAction={onAction} />;
                if (item.kind === "notice") return <Notice key={item.id} item={item} onAction={onAction} />;
                return null;
              })}
            </div>
            {!atBottom && (
              <button
                type="button"
                className="zg-jump"
                onClick={() => {
                  setAtBottom(true);
                  bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
                }}
              >
                Jump to latest
              </button>
            )}
          </div>

          <div className="zg-sr" aria-live="polite" aria-atomic="true">{announce}</div>

          <div className="zg-composer">
            <form onSubmit={submit}>
              <label htmlFor="zg-input" className="zg-sr">Ask Guide a question</label>
              <textarea
                id="zg-input"
                ref={inputRef}
                rows={1}
                value={draft}
                maxLength={maxChars}
                placeholder="Ask anything about ZoikoStream…"
                disabled={!canType}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
                }}
                aria-describedby="zg-composer-hint"
              />
              <button
                type="submit"
                className="zg-send"
                aria-label={state.sending ? "Guide is replying" : "Send message"}
                disabled={!canType || state.sending || !draft.trim()}
              >
                <SendHorizontal aria-hidden="true" />
              </button>
            </form>
            <div className="zg-composer-meta" id="zg-composer-hint">
              <span>Don&apos;t share passwords, PINs, or payment details.</span>
              {draft.length > maxChars * 0.8 && <span>{draft.length}/{maxChars}</span>}
            </div>
          </div>
        </section>
      )}
    </div>
  );
}
