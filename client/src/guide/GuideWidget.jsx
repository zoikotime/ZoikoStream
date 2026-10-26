// ZoikoStream Guide — the public website assistant (ZST-UX-FIGMA-PUB-GUIDE-001).
// Lazy-loaded by GuideMount. Owns the launcher (WF-01), the panel shell (WF-02, tablet sheet,
// WF-18 mobile), the conversation timeline and the composer. Workflow state comes from the
// server. Brand surface shared with the full Guide page (/guide): navy header with the brand
// mark, cyan -> violet -> pink gradient on the launcher and send control.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Activity, CalendarCheck, Captions, ChevronLeft, Code2, Compass, Headset,
  Info, LifeBuoy, Lock, LogOut, Maximize2, MonitorPlay, MoreVertical, RotateCcw,
  SendHorizontal, ShieldCheck, Sparkles, Tag, WifiOff, X,
} from "lucide-react";
import "./guide.css";
import * as api from "./guideApi";
import { buildPageContext, getGuideEventContextToken } from "./pageContext";
import { viewerEventId, watchViewerEventContext } from "./viewerEventContext";
import { useGuide } from "./useGuide";
import { bucketCount, bucketSeconds, deviceClass, getGuideAnalyticsConsent, track } from "./guideAnalytics";
import { GUIDE_OPEN_EVENT, setGuideAvailable, takePendingGuideOpen } from "./askGuide";
import Actions from "./parts/Actions";
import { ConversationItems, IdleBanner, RateLimitBanner, StatePanel } from "./parts/Conversation";
import { OFFLINE_TEXT, OFFLINE_TITLE, QUICK_FLOWS, useOnline } from "./parts/conversationKit";
import { isSafeHref } from "./parts/actionUtils";
import { useGuideLauncher } from "./safeArea";
import { isActiveLead, leadTitle } from "./parts/leadUtils";
import { NestedLayers, createLayerStack } from "./parts/nested";
import { markPrivacyNoticeSeen, privacyNoticeSeen } from "./parts/privacyNotice";
import GuidePage from "./GuidePage";

// Canonical welcome (Conversation Flow §28). The server's session `welcome` is authoritative;
// this is only the fallback if a session response ever omits it.
const CANONICAL_WELCOME =
  "Welcome to ZoikoStream Guide. I can help with product questions, pricing and demos, or viewer support.";

// WF-02 "Popular: How secure is ZoikoStream?" — routed with its catalog intent.
const POPULAR_QUESTION = { text: "How secure is ZoikoStream?", intent_id: "I-SECURITY" };

// Decorative icon per suggested topic (labels come from the server's intent catalog).
const INTENT_ICON = {
  "I-PRODUCT": Sparkles, "I-SOLUTION": Compass, "I-PRICING": Tag, "I-DEMO": CalendarCheck,
  "I-SALES": Headset, "I-VIEWER": LifeBuoy, "I-VIEWER-ACCESS": LifeBuoy, "I-PLAYBACK": MonitorPlay,
  "I-CAPTIONS": Captions, "I-REPLAY": RotateCcw, "I-STATUS": Activity, "I-PRIVACY": Lock,
  "I-SECURITY": ShieldCheck, "I-DEVELOPER": Code2, "I-HUMAN": Headset,
};

// Mobile (< 768 px) is the full-screen shell (Figma §4.2, WF-18).
const isMobile = () =>
  typeof window.matchMedia === "function"
    ? window.matchMedia("(max-width: 767px)").matches
    : window.innerWidth < 768;


const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function useIsMobile() {
  const [mobile, setMobile] = useState(isMobile);
  useEffect(() => {
    const on = () => setMobile(isMobile());
    window.addEventListener("resize", on);
    return () => window.removeEventListener("resize", on);
  }, []);
  return mobile;
}

// Mobile keyboard (Figma §4.2 "Composer sticky above soft keyboard", §9.2): the shell follows
// the visual viewport, so the composer stays fully visible above the on-screen keyboard.
function useVisualViewport(active) {
  const [box, setBox] = useState(null);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!active || !vv) return undefined;
    const sync = () => setBox({ height: Math.round(vv.height), top: Math.round(vv.offsetTop) });
    const first = requestAnimationFrame(sync);
    vv.addEventListener("resize", sync);
    vv.addEventListener("scroll", sync);
    return () => {
      cancelAnimationFrame(first);
      vv.removeEventListener("resize", sync);
      vv.removeEventListener("scroll", sync);
    };
  }, [active]);
  return active ? box : null;
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

// WF-12 first-use notice (exact wireframe copy).
function PrivacyFirstUse({ onPrivacy, onContinue }) {
  return (
    <section className="zg-privacy" aria-labelledby="zg-privacy-h">
      <h3 id="zg-privacy-h">Privacy in Guide</h3>
      <p>Use Guide without sharing account credentials. We may process your messages to answer your request and operate the service.</p>
      <p>Do not enter passwords, PINs, payment card details, or private event links.</p>
      <div className="zg-actions">
        <button type="button" className="zg-btn zg-btn--secondary" onClick={onPrivacy}>Privacy notice</button>
        <button type="button" className="zg-btn zg-btn--ghost" onClick={onContinue}>Continue</button>
      </div>
    </section>
  );
}

// Welcome copy from the server session: first sentence as the heading, the rest below.
function splitWelcome(text) {
  const t = (text || CANONICAL_WELCOME).trim();
  const m = t.match(/^(.+?[.!?])\s+([\s\S]+)$/);
  return m ? [m[1], m[2]] : [t, null];
}

// variant "overlay": the launcher opens the full Guide page design over the current page
// (GuidePage, with a close control) instead of the compact panel. The overlay owns the
// conversation and stays mounted once opened, so closing and reopening keeps it. `pageLinks`
// and `pageOpenHref` are passed through to GuidePage (the website's own paths/link opening).
export default function GuideWidget({
  page, pathname, suppressed = false, variant = "panel", pageLinks, pageOpenHref = null,
}) {
  const overlay = variant === "overlay";
  const navigate = useNavigate();
  const [boot, setBoot] = useState(null); // bootstrap config; null until loaded
  const [open, setOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  // Entering a console page closes the panel (adjusted during render, not in an effect): the
  // public Guide never shares the screen with the Operator Guide. The conversation itself is
  // server-side, so the launcher on the next public page picks it up again.
  const [wasSuppressed, setWasSuppressed] = useState(suppressed);
  if (suppressed !== wasSuppressed) {
    setWasSuppressed(suppressed);
    if (suppressed) {
      setOpen(false);
      setMenuOpen(false);
    }
  }
  const overlayRef = useRef(null);
  const [overlayMounted, setOverlayMounted] = useState(false);
  const [overlayRequest, setOverlayRequest] = useState(null); // CTA / deep-link quick action
  const [maximized, setMaximized] = useState(false); // popup window vs full design
  if (overlay && open && !overlayMounted) setOverlayMounted(true); // sticky: keeps the conversation
  const [draft, setDraft] = useState("");
  const [atBottom, setAtBottom] = useState(true);
  const [chosenIntent, setChosenIntent] = useState(null); // GC-005 selected quick reply
  const [pendingIntent, setPendingIntent] = useState(null);
  const [privacySeen, setPrivacySeen] = useState(privacyNoticeSeen);
  const [layerCount, setLayerCount] = useState(0);
  // Nested layers: Back/Escape close these first. The count drives the header Back control.
  const [layers] = useState(() => createLayerStack(setLayerCount));
  const online = useOnline();
  const mobile = useIsMobile();
  const viewportBox = useVisualViewport(open && mobile);
  const launcherRef = useRef(null);
  const panelRef = useRef(null);
  const titleRef = useRef(null);
  const bodyRef = useRef(null);
  const inputRef = useRef(null);
  const lastPageRef = useRef(pathname);
  const historyArmedRef = useRef(false); // a Guide history entry is on top (mobile Back)
  const openedAtRef = useRef(0);
  const launcherTrackedRef = useRef(false);
  const deepIntentRef = useRef(null); // quick action requested by an inline CTA / deep link

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

  const available = !!boot?.enabled && !suppressed && !!page;
  // Inline "Ask Guide" CTAs render only where Guide can actually open.
  useEffect(() => {
    setGuideAvailable(available);
    return () => setGuideAvailable(false);
  }, [available]);

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

  const openPanel = useCallback((entryPoint = "launcher") => {
    const entry = typeof entryPoint === "string" ? entryPoint : "launcher";
    setOpen(true);
    openedAtRef.current = Date.now();
    track("opened", {
      entry_point: entry, page_id: page?.page_id, page_type: page?.page_type,
      device_class: deviceClass(), consent_state: getGuideAnalyticsConsent(),
    });
    // Mobile full-screen shell: Back closes Guide instead of leaving the page (WF-18 A5).
    if (isMobile() && !historyArmedRef.current) {
      try {
        window.history.pushState({ ...(window.history.state || {}), zgGuide: true }, "");
        historyArmedRef.current = true;
      } catch {
        /* history unavailable: Back simply behaves as the browser's default */
      }
    }
    // Offline: no session attempt now; it starts when the connection returns.
    if (!overlay && (state.status === "closed" || state.status === "unavailable") && navigator.onLine !== false) g.open(entry);
  }, [g, state.status, page, overlay]);

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

  // Inline "Ask Guide" CTA / ?guide= deep link (WF §2.1): open Guide on the requested quick
  // action. A typed flow (viewer help, demo, sales) starts; nothing is ever sent as text.
  const handleOpenRequest = useCallback(
    (req) => {
      if (!req) return;
      deepIntentRef.current = req.intentId || null;
      // Overlay: GuidePage owns the conversation, so the request is handed to it.
      if (overlay && req.intentId) setOverlayRequest({ intentId: req.intentId, at: Date.now() });
      if (!open) openPanel(req.entryPoint || "inline_cta");
    },
    [open, openPanel, overlay],
  );
  useEffect(() => {
    if (!available) return undefined;
    const onOpen = () => handleOpenRequest(takePendingGuideOpen());
    const first = requestAnimationFrame(onOpen); // a request made before Guide loaded
    window.addEventListener(GUIDE_OPEN_EVENT, onOpen);
    return () => {
      cancelAnimationFrame(first);
      window.removeEventListener(GUIDE_OPEN_EVENT, onOpen);
    };
  }, [available, handleOpenRequest]);

  // Back online: an unstarted or failed session starts again by itself (Figma §14 "Offline").
  useEffect(() => {
    if (!overlay && online && open && (state.status === "closed" || state.status === "unavailable") && boot?.enabled) g.open();
  }, [online, open, state.status, boot?.enabled, g, overlay]);

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

  // Focus enters Guide on open (Figma §13.2): the Guide heading, or the error heading when
  // Guide opens straight into an unavailable/offline state.
  useEffect(() => {
    if (!open) return undefined;
    const id = requestAnimationFrame(() => {
      if (overlay) {
        overlayRef.current?.querySelector(".zgp-close")?.focus();
        return;
      }
      const err = bodyRef.current?.querySelector("[data-zg-initial-focus]");
      (err || titleRef.current)?.focus();
    });
    return () => cancelAnimationFrame(id);
  }, [open, overlay]);

  // Overlay: the page behind does not scroll while Guide covers it (maximized, or the popup
  // filling a phone screen).
  useEffect(() => {
    if (!overlay || !open || (!maximized && !mobile)) return undefined;
    const root = document.documentElement;
    const previous = root.style.overflow;
    root.style.overflow = "hidden";
    return () => {
      root.style.overflow = previous;
    };
  }, [overlay, open, maximized, mobile]);

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

  // Mobile full-screen shell is modal: Tab and Shift+Tab cycle inside Guide (WF-18, §13).
  const onPanelKeyDown = (e) => {
    if (!mobile || e.key !== "Tab" || !panelRef.current) return;
    const nodes = [...panelRef.current.querySelectorAll(FOCUSABLE)].filter((n) => !n.closest("[hidden]"));
    if (!nodes.length) return;
    const first = nodes[0];
    const last = nodes[nodes.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !panelRef.current.contains(active) || active === titleRef.current)) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  };
  // Focus that escapes the modal shell (e.g. a click on the page behind) is brought back.
  useEffect(() => {
    if (!open || !mobile) return undefined;
    const onFocusIn = (e) => {
      const panel = panelRef.current;
      if (panel && !panel.contains(e.target)) titleRef.current?.focus();
    };
    document.addEventListener("focusin", onFocusIn);
    return () => document.removeEventListener("focusin", onFocusIn);
  }, [open, mobile]);

  // Follow new content only while the visitor is at the bottom (Figma §9.2).
  const onScroll = () => {
    const el = bodyRef.current;
    if (el) setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 48);
  };
  useEffect(() => {
    if (atBottom && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [state.items, atBottom]);

  const onAction = useCallback(
    (a) => {
      if (a.type === "__local" && a.action_id === "__new_session") return g.restart();
      if (a.action_id && a.action_id !== "source" && a.action_id !== "privacy-notice") {
        track("cta_clicked", { cta_id: a.action_id, cta_type: a.type, destination_id: a.action_id });
      }
      if (a.type === "start_support_flow") return g.startFlow(a.flow_type || "viewer_other");
      if (a.type === "start_lead_flow") return g.startLead(a.action_id === "talk-to-sales-flow" ? "sales" : "demo");
      if (a.type === "handoff" && a.target === "organizer") {
        track("escalation_selected", { route_type: "organizer" });
        return g.handoffTo("organizer");
      }
      if (a.type === "handoff" || a.type === "sign_in") track("escalation_selected", { route_type: a.target || a.type });
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

  const acknowledgePrivacy = () => {
    markPrivacyNoticeSeen();
    setPrivacySeen(true);
  };

  const onQuick = useCallback(
    async (intent) => {
      track("intent_selected", { intent_id: intent.intent_id, page_id: page?.page_id });
      markPrivacyNoticeSeen();
      setPrivacySeen(true);
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
    },
    [g, page],
  );

  // Once the session is ready, act on a quick action requested by a CTA or deep link: a typed
  // flow starts; a question intent is only highlighted for the visitor to choose.
  useEffect(() => {
    if (state.status !== "ready" || !deepIntentRef.current) return;
    const intentId = deepIntentRef.current;
    deepIntentRef.current = null;
    if (QUICK_FLOWS[intentId]) {
      const known = state.suggested.find((i) => i.intent_id === intentId);
      onQuick(known || { intent_id: intentId, label: intentId });
    } else {
      requestAnimationFrame(() => bodyRef.current?.querySelector(`[data-intent="${intentId}"]`)?.focus());
    }
  }, [state.status, state.suggested, onQuick]);

  const onResume = (resume) => {
    if (resume.kind === "lead_flow") return g.startLead(resume.flow_kind === "sales" ? "sales" : "demo");
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
    if (!text.trim() || state.sending || rlActive || !online) return;
    setDraft("");
    setAtBottom(true);
    acknowledgePrivacy();
    // Text the server could not take (too long, session ended) comes back to the composer.
    g.send(text).then((r) => {
      if (r === "recover") setDraft((d) => d || text.trim());
    });
  };

  // Composer autogrow: 52 px -> 144 px control, then scroll inside (Figma §3.4).
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(142, Math.max(50, el.scrollHeight))}px`;
  }, [draft]);

  // Second in the shared bottom-right stack (guide/safeArea.js): if a console launcher were
  // ever mounted too, this one sits above it rather than on the same spot.
  const showLauncher = available && !open;
  useGuideLauncher(launcherRef, { order: 1, enabled: showLauncher });

  if (!boot?.enabled || suppressed) return null;
  if (!page && !open) return null; // ineligible page: no launcher

  const maxChars = boot.limits?.max_message_chars || 4000;
  const isViewer = page?.page_type === "viewer";
  const header = state.eventContext?.event_display_name;
  const canType = state.status === "ready" && !rlActive && online;

  // Header descriptor per Figma §8: degraded > lead flow > viewer context > default.
  const lastAnswer = [...state.items].reverse().find((i) => i.kind === "assistant" && !i.streaming);
  const degraded = state.status === "unavailable" || lastAnswer?.msg?.answer_class === "degraded";
  const activeLeadIndex = state.items.findLastIndex(isActiveLead);
  const activeLead = activeLeadIndex >= 0 ? state.items[activeLeadIndex] : null;
  const activeFlowIndex = state.items.findLastIndex((i) => i.kind === "flow" && i.flow.status === "in_progress");
  const activeFlow = activeFlowIndex >= 0 ? state.items[activeFlowIndex] : null;
  let descriptor = "Public AI Assistant";
  if (degraded) descriptor = "Help temporarily limited";
  else if (activeLead) descriptor = leadTitle(activeLead.flow);
  else if (isViewer || activeFlow) descriptor = header ? `Viewer help · ${header}` : "Viewer help";

  // Header Back (Figma §8: viewer context, lead flow and nested mobile states): closes a
  // nested layer first, then steps back inside the most recent flow — a viewer step goes back
  // (or leaves the flow from its first step); a lead form returns from review to editing, or
  // is paused with everything kept. Back never leaves the page.
  const backTarget =
    mobile && layerCount > 0
      ? "layer"
      : activeLead && activeLeadIndex > activeFlowIndex
        ? "lead"
        : activeFlow
          ? "flow"
          : null;
  const headerBack = () => {
    if (backTarget === "layer") {
      layers.closeTop();
    } else if (backTarget === "lead") {
      const phase = activeLead.phase || "collecting";
      g.patchItem(activeLead.id, phase === "collecting" ? { phase: "paused" } : { phase: "collecting", focusPending: true });
    } else if (backTarget === "flow" && !activeFlow.busy) {
      g.stepFlow(activeFlow.id, activeFlow.flow, { command: activeFlow.flow.step?.allow_back ? "back" : "cancel" });
    }
    if (backTarget !== "flow") requestAnimationFrame(() => titleRef.current?.focus());
  };

  const [welcomeHeading, welcomeRest] = splitWelcome(state.welcome);
  const quickDisabled = !!pendingIntent || state.sending || rlActive || !online;
  const helpCenter = directRoutes.find((r) => r.action_id === "help-center");
  const privacyHref = boot.privacy?.privacy_notice_href || "/privacy";
  const openPrivacy = () => onAction({ action_id: "privacy-notice", type: "navigate", href: privacyHref, label: "Privacy" });
  const panelStyle = viewportBox ? { "--zg-vvh": `${viewportBox.height}px`, "--zg-vvtop": `${viewportBox.top}px` } : undefined;

  return (
    <NestedLayers.Provider value={layers}>
    <div className="zg">
      {!open && (
        <button
          ref={launcherRef}
          type="button"
          className={`zg-launcher${isViewer ? " zg-launcher--viewer" : ""}`}
          onClick={() => openPanel("launcher")}
          aria-haspopup="dialog"
          aria-label="Open ZoikoStream Guide"
          title="Open ZoikoStream Guide"
        >
          <span className="zg-launcher-mark" aria-hidden="true">
            <img src="/zoiko-mark.png" alt="" />
          </span>
        </button>
      )}

      {overlay && overlayMounted && (
        <div
          ref={overlayRef}
          className={`zgp-shell zgp-shell--${maximized ? "max" : "popup"}`}
          role="dialog"
          aria-modal={maximized || mobile ? "true" : "false"}
          aria-label="Zoiko Stream Guide"
          hidden={!open}
        >
          <GuidePage
            links={pageLinks}
            openHref={pageOpenHref}
            request={overlayRequest}
            mode={maximized ? "max" : "popup"}
            onMinimize={() => closePanel()}
            onToggleMax={() => setMaximized((m) => !m)}
            onClose={() => {
              setMaximized(false);
              closePanel();
            }}
          />
        </div>
      )}

      {open && !overlay && (
        <section
          ref={panelRef}
          className={`zg-panel${viewportBox ? " zg-panel--vv" : ""}`}
          role="dialog"
          aria-modal={mobile ? "true" : "false"}
          aria-labelledby="zg-title"
          onKeyDown={onPanelKeyDown}
          style={panelStyle}
        >
          <header className="zg-header">
            {backTarget && (
              <button type="button" className="zg-iconbtn" aria-label="Back" onClick={headerBack}>
                <ChevronLeft aria-hidden="true" />
              </button>
            )}
            {!backTarget && (
              <span className="zg-brandmark" aria-hidden="true">
                <img src="/zoiko-mark.png" alt="" />
              </span>
            )}
            <div className="zg-titles" ref={titleRef} tabIndex={-1}>
              <h2 id="zg-title">Guide</h2>
              <p>
                {!degraded && <span className="zg-online" aria-hidden="true" />}
                {descriptor}
              </p>
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
                    openPrivacy();
                  }}
                >
                  <Lock aria-hidden="true" /> Privacy notice
                </button>
                <button
                  role="menuitem"
                  type="button"
                  onClick={() => {
                    setMenuOpen(false);
                    closePanel();
                    navigate("/guide");
                  }}
                >
                  <Maximize2 aria-hidden="true" /> Open full page
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
                <p className="zg-progress" role="status">
                  <span className="zg-progress-dot" aria-hidden="true" />
                  Starting Guide…
                </p>
              )}

              {!online && state.status !== "ready" && (
                <StatePanel icon={WifiOff} role="alert" title={OFFLINE_TITLE} text={OFFLINE_TEXT} focusHeading>
                  <Actions actions={directRoutes} onAction={onAction} max={4} />
                </StatePanel>
              )}

              {online && state.status === "unavailable" && (
                // WF-15: Guide failure is independent of the site; direct routes remain.
                <StatePanel
                  icon={WifiOff}
                  role="alert"
                  title="Guide is temporarily unavailable."
                  text="You can still use ZoikoStream normally."
                  focusHeading
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
                  {!privacySeen && <PrivacyFirstUse onPrivacy={openPrivacy} onContinue={acknowledgePrivacy} />}
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
                              data-intent={i.intent_id}
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
                  {!isViewer && (
                    <p className="zg-popular">
                      <span>Popular:</span>{" "}
                      <button
                        type="button"
                        className="zg-link"
                        disabled={quickDisabled}
                        onClick={() => {
                          acknowledgePrivacy();
                          track("intent_selected", { intent_id: POPULAR_QUESTION.intent_id, page_id: page?.page_id });
                          g.send(POPULAR_QUESTION.text, POPULAR_QUESTION.intent_id);
                        }}
                      >
                        {POPULAR_QUESTION.text}
                      </button>
                    </p>
                  )}
                  {isViewer && (
                    <p className="zg-fineprint">Private organizer details are not available in public chat.</p>
                  )}
                  <p className="zg-fineprint">Answers are AI-generated from ZoikoStream&apos;s public information.</p>
                  {/* WF-02 / Figma §7.1: "Privacy | Help Center" */}
                  <nav className="zg-welcome-links" aria-label="Guide links">
                    <button type="button" className="zg-link" onClick={openPrivacy}>Privacy</button>
                    {helpCenter && isSafeHref(helpCenter.href) && (
                      <button type="button" className="zg-link" onClick={() => onAction(helpCenter)}>Help Center</button>
                    )}
                  </nav>
                </div>
              )}

              {state.status === "ready" && (
                <ConversationItems
                  g={g}
                  directRoutes={directRoutes}
                  online={online}
                  rlActive={rlActive}
                  onAction={onAction}
                  onResume={onResume}
                  onQuick={onQuick}
                  onKeepFlow={() =>
                    requestAnimationFrame(() => bodyRef.current?.querySelector(`[data-flow-id="${activeFlow?.flow.flow_id}"] h3`)?.focus())
                  }
                />
              )}
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

          <div className="zg-sr" aria-live="polite" aria-atomic="true">{state.announce.text}</div>

          <div className="zg-composer">
            {!online && state.status === "ready" && (
              <div className="zg-banner zg-offline" role="alert">
                <div className="zg-notice zg-notice--warning">
                  <WifiOff aria-hidden="true" />
                  <div>
                    <strong>{OFFLINE_TITLE}</strong>
                    <div>{OFFLINE_TEXT}</div>
                  </div>
                </div>
              </div>
            )}
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
                placeholder="Ask Guide..."
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
