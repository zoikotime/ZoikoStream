// ZoikoStream Guide — full-page experience at /guide. The same assistant as the popup
// (GuideWidget): same session API, same server-owned flows, same conversation components; only
// the shell differs. Layout follows the approved Guide page design: brand header with Guide
// identity, hero, six help topics, "Why viewers choose", security and help banners, and a
// composer pinned to the bottom of the window.
//
// No session is created on page load (API §6): the first topic click or message opens one.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowUpRight, Captions, ChevronRight, Headphones, History, Info, LogOut, Monitor,
  Maximize2, Minimize2, Minus, MonitorSmartphone, Paperclip, Play, Radio, SendHorizontal, ShieldCheck, Users,
  WifiOff, X, Zap,
} from "lucide-react";
import "./guide.css";
import "./guideTopics.css";
import "./guidePage.css";
import { GUIDE_TOPICS as TOPICS, guideTopicById } from "./guideTopics";
import * as api from "./guideApi";
import { buildPageContext } from "./pageContext";
import { useGuide } from "./useGuide";
import { deviceClass, getGuideAnalyticsConsent, track } from "./guideAnalytics";
import Actions from "./parts/Actions";
import { isSafeHref } from "./parts/actionUtils";
import { NestedLayers, createLayerStack } from "./parts/nested";
import { markPrivacyNoticeSeen } from "./parts/privacyNotice";
import { ConversationItems, IdleBanner, RateLimitBanner, StatePanel } from "./parts/Conversation";
import { OFFLINE_TEXT, OFFLINE_TITLE, QUICK_FLOWS, useOnline } from "./parts/conversationKit";

const PAGE = { page_id: "guide", page_type: "home" };
const PATH = "/guide";

const WHY = [
  { tone: "violet", Icon: Users, title: "Live Events for Everyone",
    text: "Watch concerts, conferences, classes, and more — no matter where you are." },
  { tone: "blue", Icon: Zap, title: "Reliable & Easy Access",
    text: "A smooth, secure viewing experience on all your devices." },
  { tone: "rose", Icon: ShieldCheck, title: "Inclusive by Design",
    text: "Captions and accessibility features help more people participate." },
  { tone: "violet", Icon: History, title: "Watch On Your Schedule",
    text: "Replays and on-demand access let you catch up anytime." },
];

const HERO_CHIPS = [
  { Icon: Radio, label: "Live Events" },
  { Icon: Captions, label: "Captions" },
  { Icon: History, label: "Replay" },
  { Icon: Monitor, label: "All Devices" },
];

const prefersReducedMotion = () =>
  typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Stylised concert scene for the hero devices: stage lights, beams and a crowd with raised
// hands. Pure SVG, decorative.
function ConcertScene({ id }) {
  const crowd = useMemo(() => {
    const heads = [];
    const arms = [];
    for (let row = 0; row < 2; row += 1) {
      for (let i = 0; i < 30; i += 1) {
        const x = -4 + i * 14 + row * 7 + ((i * 37) % 6);
        const y = 206 + row * 14 + ((i * 53) % 9);
        const r = 7 + row * 1.5;
        heads.push(<circle key={`h${row}-${i}`} cx={x} cy={y} r={r} />);
        heads.push(<rect key={`b${row}-${i}`} x={x - r - 2} y={y + r - 2} width={r * 2 + 4} height={40} rx={r} />);
        if ((i + row) % 3 !== 1) {
          const lean = ((i * 29 + row * 7) % 13) - 6;
          const top = y - 30 - ((i + row) % 4) * 7;
          const hx = x + lean;
          arms.push(
            <g key={`a${row}-${i}`}>
              <path d={`M${x - 4} ${y + 5} L${hx} ${top}`} strokeWidth={6} strokeLinecap="round" fill="none" />
              <circle cx={hx} cy={top - 2} r={4.6} />
            </g>,
          );
        }
      }
    }
    return { heads, arms };
  }, []);
  return (
    <svg className="zgp-scene" viewBox="0 0 400 260" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
      <defs>
        <linearGradient id={`${id}-sky`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#120a33" />
          <stop offset="0.55" stopColor="#3a1270" />
          <stop offset="1" stopColor="#1a0838" />
        </linearGradient>
        <radialGradient id={`${id}-stage`} cx="0.5" cy="0.72" r="0.55">
          <stop offset="0" stopColor="#ff7ad9" stopOpacity="0.95" />
          <stop offset="0.35" stopColor="#b44dff" stopOpacity="0.55" />
          <stop offset="1" stopColor="#3a1270" stopOpacity="0" />
        </radialGradient>
        <linearGradient id={`${id}-beam-c`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#7df3ff" stopOpacity="0.9" />
          <stop offset="1" stopColor="#7df3ff" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}-beam-p`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ff6bd6" stopOpacity="0.85" />
          <stop offset="1" stopColor="#ff6bd6" stopOpacity="0" />
        </linearGradient>
        <linearGradient id={`${id}-beam-v`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#a78bff" stopOpacity="0.85" />
          <stop offset="1" stopColor="#a78bff" stopOpacity="0" />
        </linearGradient>
      </defs>
      <rect width="400" height="260" fill={`url(#${id}-sky)`} />
      <rect width="400" height="260" fill={`url(#${id}-stage)`} />
      <g style={{ mixBlendMode: "screen" }}>
        <polygon points="70,0 82,0 175,200 120,200" fill={`url(#${id}-beam-c)`} opacity="0.55" />
        <polygon points="150,0 160,0 215,200 150,200" fill={`url(#${id}-beam-p)`} opacity="0.5" />
        <polygon points="240,0 250,0 260,200 195,200" fill={`url(#${id}-beam-v)`} opacity="0.55" />
        <polygon points="318,0 330,0 290,200 236,200" fill={`url(#${id}-beam-c)`} opacity="0.5" />
        <polygon points="200,0 208,0 330,200 280,200" fill={`url(#${id}-beam-p)`} opacity="0.35" />
        <polygon points="110,0 118,0 70,200 20,200" fill={`url(#${id}-beam-v)`} opacity="0.35" />
      </g>
      <g fill="#ffffff">
        {[76, 155, 245, 324, 204, 114].map((x, i) => (
          <circle key={x} cx={x} cy={4} r={i % 2 ? 3.5 : 4.5} opacity="0.95" />
        ))}
      </g>
      <g fill="#ffe3fb" opacity="0.8">
        {[[40, 60], [96, 120], [182, 90], [260, 70], [340, 110], [300, 40], [130, 50], [220, 140]].map(([x, y]) => (
          <circle key={`${x}-${y}`} cx={x} cy={y} r={1.6} />
        ))}
      </g>
      <g fill="#07030f" stroke="#07030f">
        {crowd.arms}
        {crowd.heads}
        <rect x="0" y="222" width="400" height="40" stroke="none" />
      </g>
    </svg>
  );
}

function HeroArt() {
  return (
    <div className="zgp-art" aria-hidden="true">
      <div className="zgp-arc zgp-arc--1" />
      <div className="zgp-arc zgp-arc--2" />
      <div className="zgp-tablet">
        <div className="zgp-tablet-screen">
          <ConcertScene id="zgp-scene-tab" />
          <span className="zgp-live"><span className="zgp-live-dot" />LIVE</span>
        </div>
      </div>
      <div className="zgp-phone">
        <div className="zgp-phone-screen">
          <ConcertScene id="zgp-scene-phone" />
        </div>
      </div>
      <div className="zgp-apptile">
        <img src="/zoiko-mark.png" alt="" />
      </div>
      <ul className="zgp-chips">
        {HERO_CHIPS.map(({ Icon, label }) => (
          <li key={label}>
            <Icon aria-hidden="true" />
            {label}
          </li>
        ))}
      </ul>
    </div>
  );
}

// `embedded`: the host site already shows its own header (the marketing website), so the
// page drops its logo and site links and keeps only the Guide identity bar. `links` overrides
// the site paths; `openHref` overrides how a Guide action link opens (the website opens them
// on the platform host).
// `request`: a quick action handed over by the launcher (inline CTA / ?guide= deep link). A
// typed flow starts; a question topic only opens Guide — nothing is ever sent as text.
// `mode`: "page" (the /guide route), "popup" (the launcher's compact window) or "max" (the
// launcher's window maximized to the full design). `onMinimize`, `onToggleMax` and `onClose`
// add the window controls to the header.
export default function GuidePage({
  embedded = false, links: linkOverrides, openHref = null, request = null,
  mode = "page", onMinimize = null, onToggleMax = null, onClose = null,
}) {
  const compact = mode === "popup";
  const links = linkOverrides || {};
  const navigate = useNavigate();
  const [boot, setBoot] = useState(null);
  const [draft, setDraft] = useState("");
  const [busyTopic, setBusyTopic] = useState(null);
  const [showUnavailable, setShowUnavailable] = useState(false);
  const [, setLayerCount] = useState(0);
  const [layers] = useState(() => createLayerStack(setLayerCount));
  const online = useOnline();
  const inputRef = useRef(null);
  const convRef = useRef(null);
  const pendingRef = useRef(null); // the first action, run once the session is ready
  const openedRef = useRef(false);

  const directRoutes = useMemo(() => boot?.direct_routes || [], [boot]);
  const getPageContext = useCallback((entryPoint) => buildPageContext(PATH, PAGE, { entryPoint }), []);
  const g = useGuide({ getPageContext, directRoutes });
  const { state } = g;

  // The security banner on this page carries the privacy guidance (WF-12), so the first-use
  // notice is already shown.
  useEffect(() => {
    markPrivacyNoticeSeen();
  }, []);

  // Bootstrap: lightweight, creates no session (API §6).
  useEffect(() => {
    let alive = true;
    api
      .bootstrap({ pageId: PAGE.page_id, pageType: PAGE.page_type })
      .then((b) => alive && setBoot(b))
      .catch(() => alive && setBoot({ enabled: false }));
    return () => {
      alive = false;
    };
  }, []);

  // This page owns its own conversation; leaving it releases the session handle so the popup
  // on other pages starts cleanly instead of writing into this page's conversation.
  useEffect(() => () => api.clearSession(), []);

  const available = !!boot?.enabled;
  const rlActive = !!state.rateLimit?.known;
  const ready = state.status === "ready";
  const canType = available && online && !rlActive && (ready || state.status === "closed");

  const runTopic = useCallback(
    async (topic) => {
      setBusyTopic(topic.id);
      try {
        const route = QUICK_FLOWS[topic.intent];
        if (route?.[0] === "support") await g.startFlow(route[1]);
        else if (route?.[0] === "lead") await g.startLead(route[1], topic.intent);
        else await g.send(topic.ask || topic.title, topic.intent);
      } finally {
        setBusyTopic(null);
      }
    },
    [g],
  );

  // Every interaction goes through here: the first one opens the session, then runs.
  const perform = useCallback(
    (action) => {
      if (!available) {
        setShowUnavailable(true);
        return;
      }
      if (!online) return;
      if (ready) {
        action();
        return;
      }
      pendingRef.current = action;
      if (!openedRef.current) {
        openedRef.current = true;
        track("opened", {
          entry_point: "inline", page_id: PAGE.page_id, page_type: PAGE.page_type,
          device_class: deviceClass(), consent_state: getGuideAnalyticsConsent(),
        });
      }
      if (state.status !== "starting") g.open("inline");
    },
    [available, online, ready, state.status, g],
  );

  useEffect(() => {
    if (!ready || !pendingRef.current) return;
    const action = pendingRef.current;
    pendingRef.current = null;
    action();
  }, [ready]);

  const onTopic = (topic) => {
    track("intent_selected", { intent_id: topic.intent, page_id: PAGE.page_id });
    perform(() => runTopic(topic));
  };

  // /guide?topic=<id> (the homepage topic cards): a click the visitor already made elsewhere,
  // so it runs once Guide is known to be available. The parameter is removed from the address
  // bar so a reload or a shared link does not run it again.
  const [params, setParams] = useSearchParams();
  const deepTopic = guideTopicById(params.get("topic"));
  const deepTopicRef = useRef(deepTopic);
  useEffect(() => {
    if (!params.has("topic")) return;
    const next = new URLSearchParams(params);
    next.delete("topic");
    setParams(next, { replace: true });
  }, [params, setParams]);
  const handledRequestRef = useRef(null);
  useEffect(() => {
    if (!boot || !request || handledRequestRef.current === request) return;
    handledRequestRef.current = request;
    if (QUICK_FLOWS[request.intentId]) {
      const topic = { id: request.intentId, intent: request.intentId, title: request.intentId };
      queueMicrotask(() => perform(() => runTopic(topic)));
    }
  });
  useEffect(() => {
    if (!boot || !deepTopicRef.current) return;
    const topic = deepTopicRef.current;
    deepTopicRef.current = null;
    onTopic(topic);
  });

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
        if (openHref) openHref(a.href);
        else navigate(a.href);
      }
      return undefined;
    },
    [g, navigate, openHref],
  );

  const onQuick = useCallback(
    (intent) => perform(() => runTopic({ id: intent.intent_id, intent: intent.intent_id, title: intent.label })),
    [perform, runTopic],
  );

  const onResume = (resume) => {
    if (resume.kind === "lead_flow") return g.startLead(resume.flow_kind === "sales" ? "sales" : "demo");
    const el = convRef.current?.querySelector(`[data-flow-id="${resume.flow_id}"]`);
    if (typeof el?.scrollIntoView === "function") el.scrollIntoView({ block: "center" });
    el?.querySelector("h3")?.focus();
    return undefined;
  };

  const submit = (e) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || state.sending || !canType) return;
    setDraft("");
    perform(() =>
      g.send(text).then((r) => {
        if (r === "recover") setDraft((d) => d || text);
      }),
    );
  };

  // Composer autogrow: 52 px -> 144 px, then scroll inside.
  useEffect(() => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(142, Math.max(50, el.scrollHeight))}px`;
  }, [draft]);

  // New conversation content scrolls into view above the pinned composer.
  const itemCount = state.items.length;
  useEffect(() => {
    if (!itemCount || !convRef.current) return;
    const last = convRef.current.querySelector(".zgp-conv-stack > :last-child");
    if (typeof last?.scrollIntoView === "function") {
      last.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
    }
  }, [itemCount, state.items]);

  let status = { tone: "pending", label: "Connecting…" };
  if (!online) status = { tone: "off", label: "Offline" };
  else if (boot && !available) status = { tone: "warn", label: "Unavailable" };
  else if (available) status = { tone: "on", label: "Online" };

  const showConversation =
    state.items.length > 0 || state.status === "starting" || state.status === "ended"
    || state.status === "unavailable" || showUnavailable || (!online && state.status === "ready");
  const supportHref = links.support || directRoutes.find((r) => r.action_id === "contact-support")?.href || "/contact";

  return (
    <NestedLayers.Provider value={layers}>
      <div className={`zgp${embedded ? " zgp--embedded" : ""}${compact ? " zgp--popup" : ""}`}>
        <header className="zgp-top">
          <div className="zgp-wrap zgp-nav">
            {!embedded && !compact && (
              <>
                <Link to="/" className="zgp-logo" aria-label="ZoikoStream home">
                  <img src="/zoiko-logo-white.png" alt="ZoikoStream" />
                </Link>
                <span className="zgp-divider" aria-hidden="true" />
              </>
            )}
            <div className="zgp-ident">
              <span className="zgp-avatar" aria-hidden="true">
                <img src="/zoiko-mark.png" alt="" />
              </span>
              <div>
                <p className="zgp-ident-name">Zoiko Stream Guide</p>
                <p className={`zgp-ident-status zgp-ident-status--${status.tone}`} role="status">
                  <span className="zgp-dot" aria-hidden="true" />
                  {status.label}
                </p>
              </div>
            </div>
            {embedded || compact ? (
              <span className="zgp-nav-spacer" />
            ) : (
              <nav className="zgp-links" aria-label="Site">
                <Link to={links.liveEvents || "/"}>Live Events</Link>
                <Link to={links.help || "/contact"}>Help</Link>
                <Link to={links.about || "/trust"}>About</Link>
              </nav>
            )}
            {!compact && (
              <Link to={supportHref} className="zgp-support">
                <Headphones aria-hidden="true" />
                Contact Support
              </Link>
            )}
            {(onMinimize || onToggleMax || onClose) && (
              <div className="zgp-winctls">
                {onMinimize && (
                  <button type="button" className="zgp-winbtn" aria-label="Minimize Guide" title="Minimize" onClick={onMinimize}>
                    <Minus aria-hidden="true" />
                  </button>
                )}
                {onToggleMax && (
                  <button
                    type="button"
                    className="zgp-winbtn zgp-winbtn--max"
                    aria-label={mode === "max" ? "Restore Guide size" : "Maximize Guide"}
                    title={mode === "max" ? "Restore" : "Maximize"}
                    onClick={onToggleMax}
                  >
                    {mode === "max" ? <Minimize2 aria-hidden="true" /> : <Maximize2 aria-hidden="true" />}
                  </button>
                )}
                {onClose && (
                  <button type="button" className="zgp-winbtn zgp-close" aria-label="Close Guide" title="Close" onClick={onClose}>
                    <X aria-hidden="true" />
                  </button>
                )}
              </div>
            )}
          </div>
        </header>

        <section className="zgp-hero" aria-labelledby="zgp-title">
          <div className="zgp-wrap zgp-hero-grid">
            <div className="zgp-hero-copy">
              <p className="zgp-eyebrow">Welcome to Zoiko Stream</p>
              <h1 id="zgp-title">
                <span className="zgp-h1-line">Your Guide to</span>{" "}
                <br />
                <span className="zgp-h1-line"><span className="zgp-grad">Live Events,</span> Anytime.</span>
              </h1>
              <p className="zgp-lede">
                Zoiko Stream is a live-events streaming platform that helps you access, watch, and enjoy events from
                anywhere. Get instant help with joining events, fixing playback issues, captions and accessibility,
                replays, and more — all in one place.
              </p>
              <ul className="zgp-features">
                <li>
                  <Play className="zgp-feat-icon zgp-feat-icon--pink" aria-hidden="true" />
                  <span>Live Events<br />for Everyone</span>
                </li>
                <li>
                  <Users className="zgp-feat-icon zgp-feat-icon--blue" aria-hidden="true" />
                  <span>Watch from<br />Anywhere</span>
                </li>
                <li>
                  <MonitorSmartphone className="zgp-feat-icon zgp-feat-icon--blue" aria-hidden="true" />
                  <span>Works on All<br />Your Devices</span>
                </li>
              </ul>
            </div>
            <HeroArt />
          </div>
        </section>

        <main className="zgp-main">
          <div className="zgp-wrap">
            <section aria-label="What Guide can help with">
              <ul className="zgp-topics">
                {TOPICS.map((t) => (
                  <li key={t.id}>
                    <button
                      type="button"
                      className={`zgp-topic zgp-tone--${t.tone}`}
                      onClick={() => onTopic(t)}
                      disabled={!!busyTopic || state.sending || rlActive}
                      aria-busy={busyTopic === t.id || undefined}
                    >
                      <span className="zgp-topic-icon">
                        <t.Icon aria-hidden="true" />
                      </span>
                      <span className="zgp-topic-text">
                        <span className="zgp-topic-title">{t.title}</span>
                        <span className="zgp-topic-desc">{t.text}</span>
                      </span>
                      <span className="zgp-topic-go" aria-hidden="true">
                        <ChevronRight />
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>

            <section className="zgp-why" aria-labelledby="zgp-why-h">
              <div className="zgp-why-intro">
                <h2 id="zgp-why-h">Why Viewers Choose Zoiko Stream</h2>
                <p>A simple, reliable way to watch, access, and enjoy live events from anywhere.</p>
              </div>
              <ul className="zgp-why-list">
                {WHY.map(({ tone, Icon, title, text }) => (
                  <li key={title} className={`zgp-tone--${tone}`}>
                    <Icon className="zgp-why-icon" aria-hidden="true" />
                    <h3>{title}</h3>
                    <p>{text}</p>
                  </li>
                ))}
              </ul>
            </section>

            <section className="zgp-security" aria-label="Your security matters">
              <ShieldCheck className="zgp-security-icon" aria-hidden="true" />
              <strong>Your security matters</strong>
              <span className="zgp-security-sep" aria-hidden="true" />
              <p>For your safety, please do not share passwords, PINs, secret event links, or other private account information in this chat.</p>
            </section>

            <section className="zgp-help" aria-labelledby="zgp-help-h">
              <span className="zgp-help-icon" aria-hidden="true">
                <Headphones />
              </span>
              <h2 id="zgp-help-h">Need more help?</h2>
              <p>If you still need assistance, our support team is here for you.</p>
              <Link to={supportHref} className="zgp-help-btn">
                <Headphones aria-hidden="true" />
                Contact Support
                <ArrowUpRight aria-hidden="true" />
              </Link>
            </section>

            {showConversation && (
              <section className="zg zgp-conv" aria-labelledby="zgp-conv-h" ref={convRef}>
                <div className="zgp-conv-head">
                  <h2 id="zgp-conv-h">Your conversation with Guide</h2>
                  {ready && (
                    <button type="button" className="zgp-conv-end" onClick={() => g.end()}>
                      <LogOut aria-hidden="true" />
                      End conversation
                    </button>
                  )}
                </div>
                <div className="zgp-conv-stack">
                  {state.status === "starting" && (
                    <p className="zg-progress" role="status">
                      <span className="zg-progress-dot" aria-hidden="true" />
                      Starting Guide…
                    </p>
                  )}
                  {(state.status === "unavailable" || showUnavailable) && online && (
                    <StatePanel
                      icon={WifiOff}
                      role="alert"
                      title="Guide is temporarily unavailable."
                      text="You can still use ZoikoStream normally."
                      focusHeading
                    >
                      <Actions actions={directRoutes} onAction={onAction} max={4} />
                      {available && (
                        <div className="zg-actions" style={{ justifyContent: "center" }}>
                          <button type="button" className="zg-btn zg-btn--ghost" onClick={() => g.open("inline")}>
                            Try again
                          </button>
                        </div>
                      )}
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
                  {ready && (
                    <ConversationItems
                      g={g}
                      directRoutes={directRoutes}
                      online={online}
                      rlActive={rlActive}
                      onAction={onAction}
                      onResume={onResume}
                      onQuick={onQuick}
                    />
                  )}
                </div>
              </section>
            )}
          </div>
        </main>

        <div className="zgp-dock">
          <div className="zgp-wrap">
            <div className="zg zgp-dock-banners">
              {!online && (
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
              {ready && state.rateLimit && (
                <RateLimitBanner rateLimit={state.rateLimit} routes={directRoutes} onAction={onAction} />
              )}
              {ready && state.idleWarning && !state.rateLimit && (
                <IdleBanner warning={state.idleWarning} onStay={() => g.stayActive()} />
              )}
            </div>
            <form className="zgp-composer" onSubmit={submit}>
              <span className="zgp-avatar zgp-avatar--lg" aria-hidden="true">
                <img src="/zoiko-mark.png" alt="" />
              </span>
              <div className="zgp-input">
                <label htmlFor="zgp-input" className="zg-sr">Ask Zoiko Stream Guide a question</label>
                <textarea
                  id="zgp-input"
                  ref={inputRef}
                  rows={1}
                  value={draft}
                  maxLength={boot?.limits?.max_message_chars || 4000}
                  placeholder="Ask Zoiko Stream Guide anything..."
                  disabled={!canType}
                  onChange={(e) => setDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
                  }}
                  aria-describedby="zgp-disclaimer"
                />
                {/* Guide accepts text only (Security §8: no uploads); the control is shown
                    disabled so it never implies a file can be shared here. */}
                <button
                  type="button"
                  className="zgp-attach"
                  disabled
                  aria-label="Attachments aren't supported in Guide"
                  title="Attachments aren't supported in Guide"
                >
                  <Paperclip aria-hidden="true" />
                </button>
              </div>
              <button
                type="submit"
                className="zgp-send"
                aria-label={state.sending ? "Guide is replying" : "Send message"}
                disabled={!canType || state.sending}
              >
                <SendHorizontal aria-hidden="true" />
              </button>
            </form>
            <p className="zgp-disclaimer" id="zgp-disclaimer">
              <Info aria-hidden="true" />
              This chatbot provides public information and viewer help for Zoiko Stream. For account-specific issues,
              please contact our support team.
            </p>
          </div>
        </div>

        <div className="zg-sr" aria-live="polite" aria-atomic="true">{state.announce.text}</div>
      </div>
    </NestedLayers.Provider>
  );
}
