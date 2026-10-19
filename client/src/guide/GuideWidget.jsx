// ZoikoStream Guide — the public website assistant (ZST-UX-FIGMA-PUB-GUIDE-001).
// Lazy-loaded by GuideMount. Owns the launcher (WF-01), the panel shell (WF-02 / WF-18
// mobile), the conversation timeline and the composer. Workflow state comes from the server.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, AlertTriangle, CalendarCheck, Captions, Clock, Code2, Compass, Headset, Info, LifeBuoy,
  Lock, LogOut, MonitorPlay, MoreVertical, RotateCcw, SendHorizontal, ShieldCheck, Sparkles, Tag,
  WifiOff, X, XCircle,
} from "lucide-react";
import "./guide.css";
import * as api from "./guideApi";
import { buildPageContext, getGuideEventContextToken } from "./pageContext";
import { viewerEventId, watchViewerEventContext } from "./viewerEventContext";
import { RATE_LIMIT_COPY, useGuide } from "./useGuide";
import { bucketCount, bucketSeconds, deviceClass, track } from "./guideAnalytics";
import AssistantMessage from "./parts/AssistantMessage";
import GuideAvatar from "./parts/GuideAvatar";
import SupportStep from "./parts/SupportStep";
import LeadForm from "./parts/LeadForm";
import Actions from "./parts/Actions";
import { isSafeHref } from "./parts/actionUtils";
import { isActiveLead, leadTitle } from "./parts/leadUtils";
import { NestedLayers, createLayerStack } from "./parts/nested";

// Canonical welcome (Conversation Flow §28). The server's session `welcome` is authoritative;
// this is only the fallback if a session response ever omits it.
const CANONICAL_WELCOME =
  "Welcome to ZoikoStream Guide. I can help with product questions, pricing and demos, or viewer support.";

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

// Mobile (< 768 px) is the full-screen shell (Figma §4.2, WF-18).
const isMobile = () =>
  typeof window.matchMedia === "function"
    ? window.matchMedia("(max-width: 767px)").matches
    : window.innerWidth < 768;

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

// A message that did not get an answer: the text stays, with Retry (Figma §14, CF §24).
const FAILED_COPY = {
  GUIDE_TIMEOUT: "Guide took too long to answer. Your message is kept, so you can retry.",
  GUIDE_STREAM_INTERRUPTED: "The answer was interrupted. Your message is kept, so you can retry.",
  GUIDE_NETWORK_ERROR: "Guide couldn't be reached. Your message is kept, so you can retry.",
  GUIDE_STATE_CONFLICT: "This message is still being answered. Retry in a moment.",
  GUIDE_RATE_LIMITED: "Your message wasn't sent yet. You can retry when the wait is over.",
};

function FailedSend({ failed, disabled, onRetry, routes, onAction }) {
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
function RateLimitBanner({ rateLimit, routes, onAction }) {
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
function IdleBanner({ warning, onStay }) {
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

// Welcome copy from the server session: first sentence as the heading, the rest below.
function splitWelcome(text) {
  const t = (text || CANONICAL_WELCOME).trim();
  const m = t.match(/^(.+?[.!?])\s+([\s\S]+)$/);
  return m ? [m[1], m[2]] : [t, null];
}

export default function GuideWidget({ page, pathname }) {
  const navigate = useNavigate();
  const [boot, setBoot] = useState(null); // bootstrap config; null until loaded
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const [chosenIntent, setChosenIntent] = useState(null); // GC-005 selected quick reply
  const [pendingIntent, setPendingIntent] = useState(null);
  const [layers] = useState(createLayerStack); // nested layers: Back/Escape close these first
  const launcherRef = useRef(null);
  const titleRef = useRef(null);
  const bodyRef = useRef(null);
  const inputRef = useRef(null);
  const lastPageRef = useRef(pathname);
  const historyArmedRef = useRef(false); // a Guide history entry is on top (mobile Back)
  const openedAtRef = useRef(0);
  const launcherTrackedRef = useRef(false);

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
  const itemCountRef = useRef(0);
  useEffect(() => {
    itemCountRef.current = state.items.filter((i) => i.kind === "user").length;
  }, [state.items]);

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

  useEffect(() => {
    if (!boot?.enabled || !page || launcherTrackedRef.current) return;
    launcherTrackedRef.current = true;
    track("launcher_viewed", { page_id: page.page_id, page_type: page.page_type, locale: "en-US", device_class: deviceClass() });
  }, [boot, page]);

  // SPA navigation: refresh public page context for an open conversation.
  useEffect(() => {
    if (lastPageRef.current !== pathname) {
      lastPageRef.current = pathname;
      // The Guide history entry is no longer on top once the page itself navigated.
      if (historyArmedRef.current && !window.history.state?.zgGuide) historyArmedRef.current = false;
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
    openedAtRef.current = Date.now();
    track("opened", { entry_point: "launcher", page_id: page?.page_id, page_type: page?.page_type, device_class: deviceClass() });
    // Mobile full-screen shell: Back closes Guide instead of leaving the page (WF-18 A5).
    if (isMobile() && !historyArmedRef.current) {
      try {
        window.history.pushState({ ...(window.history.state || {}), zgGuide: true }, "");
        historyArmedRef.current = true;
      } catch {
        /* history unavailable: Back simply behaves as the browser's default */
      }
    }
    if (state.status === "closed" || state.status === "unavailable") g.open();
  }, [g, state.status, page]);

  const closePanel = useCallback(({ fromHistory = false } = {}) => {
    setOpen(false);
    setMenuOpen(false);
    // Closed with X / Escape / a button: remove our own history entry so none is left behind.
    if (historyArmedRef.current && !fromHistory && window.history.state?.zgGuide) {
      historyArmedRef.current = false;
      window.history.back();
    }
    historyArmedRef.current = false;
    track("closed", {
      duration_bucket: bucketSeconds(Date.now() - openedAtRef.current),
      message_count_bucket: bucketCount(itemCountRef.current),
    });
    requestAnimationFrame(() => launcherRef.current?.focus()); // focus returns to invoker
  }, []);

  // The header menu is a nested layer too.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const entry = { close: () => setMenuOpen(false) };
    layers.push(entry);
    return () => layers.remove(entry);
  }, [menuOpen, layers]);

  // Browser Back on mobile: nested detail first, then Guide; never the page (WF-18 A5).
  useEffect(() => {
    if (!open) return undefined;
    const onPop = () => {
      if (!historyArmedRef.current) return;
      if (layers.closeTop()) {
        try {
          window.history.pushState({ ...(window.history.state || {}), zgGuide: true }, "");
        } catch {
          historyArmedRef.current = false;
        }
        return;
      }
      historyArmedRef.current = false;
      closePanel({ fromHistory: true });
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [open, layers, closePanel]);

  // Focus enters Guide on open (Figma §13.2).
  useEffect(() => {
    if (open) requestAnimationFrame(() => titleRef.current?.focus());
  }, [open]);

  // Escape closes the top Guide layer. Nothing typed is lost: drafts and form values live in
  // the conversation state, which survives closing the panel (Figma §10.1).
  useEffect(() => {
    if (!open) return;
    const onKey = (e) => {
      if (e.key === "Escape") {
        if (!layers.closeTop()) closePanel();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, layers, closePanel]);

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
    const last = [...state.items].reverse().find((i) => i.kind === "assistant" && !i.streaming && !i.incomplete);
    return last?.msg?.text || "";
  }, [state.items]);

  const onAction = useCallback(
    (a) => {
      if (a.type === "__local" && a.action_id === "__new_session") return g.restart();
      if (a.action_id && a.action_id !== "source" && a.action_id !== "privacy-notice") {
        track("cta_clicked", { cta_id: a.action_id, cta_type: a.type });
      }
      if (a.type === "start_support_flow") return g.startFlow(a.flow_type || "viewer_other");
      if (a.type === "start_lead_flow") return g.startLead(a.action_id === "talk-to-sales-flow" ? "sales" : "demo");
      if (a.type === "handoff" && a.target === "organizer") return g.handoffTo("organizer");
      if (isSafeHref(a.href)) {
        // In-app navigation keeps the conversation (Guide stays mounted across routes).
        if (historyArmedRef.current && window.history.state?.zgGuide) {
          // Replace the Guide entry so Back from the new page returns to the original page.
          historyArmedRef.current = false;
          navigate(a.href, { replace: true });
          closePanel();
        } else {
          navigate(a.href);
          if (isMobile()) closePanel();
        }
      }
      return undefined;
    },
    [g, navigate, closePanel],
  );

  const onQuick = async (intent) => {
    track("intent_selected", { intent_id: intent.intent_id, page_id: page?.page_id });
    setChosenIntent(intent.intent_id);
    setPendingIntent(intent.intent_id);
    try {
      const route = QUICK_FLOWS[intent.intent_id];
      if (route?.[0] === "support") await g.startFlow(route[1]);
      else if (route?.[0] === "lead") await g.startLead(route[1], intent.intent_id);
      else await g.send(intent.label, intent.intent_id);
    } finally {
      setPendingIntent(null);
    }
  };

  const onResume = (resume) => {
    if (resume.kind === "lead_flow") return g.startLead("demo");
    const el = bodyRef.current?.querySelector(`[data-flow-id="${resume.flow_id}"]`);
    el?.scrollIntoView({ block: "center" });
    el?.querySelector("h3")?.focus();
    return undefined;
  };

  // The hook clears rateLimit when the server's Retry-After window has passed.
  const rlActive = !!state.rateLimit?.known;

  const submit = (e) => {
    e.preventDefault();
    const text = draft;
    if (!text.trim() || state.sending || rlActive) return;
    setDraft("");
    setAtBottom(true);
    // Text the server could not take (too long, session ended) comes back to the composer.
    g.send(text).then((r) => {
      if (r === "recover") setDraft((d) => d || text.trim());
    });
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
  const canType = state.status === "ready" && !rlActive;

  // Header descriptor per Figma §8: degraded > lead flow > viewer context > default.
  const lastAnswer = [...state.items].reverse().find((i) => i.kind === "assistant" && !i.streaming);
  const degraded = state.status === "unavailable" || lastAnswer?.msg?.answer_class === "degraded";
  const activeLead = [...state.items].reverse().find(isActiveLead);
  let descriptor = "Public AI Assistant";
  if (degraded) descriptor = "Help temporarily limited";
  else if (activeLead) descriptor = leadTitle(activeLead.flow);
  else if (isViewer) descriptor = header ? `Viewer help · ${header}` : "Viewer help";

  const [welcomeHeading, welcomeRest] = splitWelcome(state.welcome);
  const quickDisabled = !!pendingIntent || state.sending || rlActive;

  return (
    <NestedLayers.Provider value={layers}>
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
              <p>{descriptor}</p>
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
            <button type="button" className="zg-iconbtn" aria-label="Close Guide" onClick={() => closePanel()}>
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
                  {/* WF-17: [Start new conversation] [Close Guide] */}
                  <div className="zg-actions">
                    <button type="button" className="zg-btn zg-btn--primary" onClick={() => g.restart()}>
                      Start new conversation
                    </button>
                    <button type="button" className="zg-btn zg-btn--ghost" onClick={() => closePanel()}>
                      Close Guide
                    </button>
                  </div>
                </StatePanel>
              )}

              {state.status === "ready" && (
                <div className="zg-welcome">
                  <GuideAvatar size="xl" />
                  {isViewer ? (
                    <>
                      <h3>Help with this event</h3>
                      <p>I can walk you through access, playback, captions and replay, one step at a time.</p>
                    </>
                  ) : (
                    // Canonical welcome sentence from the server session (CF §28, Figma §12).
                    <>
                      <h3>{welcomeHeading}</h3>
                      {welcomeRest && <p>{welcomeRest}</p>}
                    </>
                  )}
                  {isViewer && state.eventContext && <PublicContext context={state.eventContext} />}
                  {state.suggested.length > 0 && (
                    <>
                      <p className="zg-eyebrow-label" id="zg-topics">Popular topics</p>
                      <div className="zg-quick" role="group" aria-labelledby="zg-topics">
                        {state.suggested.map((i) => {
                          const Icon = INTENT_ICON[i.intent_id] || Sparkles;
                          const selected = chosenIntent === i.intent_id;
                          return (
                            <button
                              key={i.intent_id}
                              type="button"
                              className={`zg-quick-card${selected ? " is-selected" : ""}`}
                              aria-pressed={selected}
                              disabled={quickDisabled}
                              onClick={() => onQuick(i)}
                            >
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
                    <div key={item.id}>
                      <div className="zg-row zg-row--user">
                        <span className="zg-sr">You said:</span>
                        <div className="zg-user">{item.text}</div>
                      </div>
                      {item.failed && (
                        <FailedSend
                          failed={item.failed}
                          disabled={state.sending || rlActive}
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
                      onPatch={g.patchItem}
                    />
                  );
                }
                if (item.kind === "flow") {
                  return <SupportStep key={item.id} item={item} onStep={g.stepFlow} onAction={onAction} />;
                }
                if (item.kind === "lead") return <LeadForm key={item.id} item={item} onAction={onAction} onPatch={g.patchItem} />;
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
            {state.status === "ready" && state.rateLimit && (
              <RateLimitBanner rateLimit={state.rateLimit} routes={directRoutes} onAction={onAction} />
            )}
            {state.status === "ready" && state.idleWarning && !state.rateLimit && (
              <IdleBanner warning={state.idleWarning} onStay={() => g.stayActive()} />
            )}
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
    </NestedLayers.Provider>
  );
}
