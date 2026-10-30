import { useEffect } from "react";
import { useLocation, useNavigate, useOutletContext } from "react-router-dom";
import {
  FiActivity, FiBarChart2, FiClipboard, FiFilm, FiHome, FiRadio, FiShield,
} from "react-icons/fi";

// The Event Workspace's seven sections (ZST-UX-NAV-001 §6, ZST-ENG-DASH-001 §3.3), in order.
// `slug` is the route suffix under /organization/events/:id — the Overview is the event's own
// URL, so every link that already pointed at an event still lands on it.
//
// `children` are the sub-items the frames list under the open section (EVT-02..07). Each
// points at a real part of the section page — an anchor (`hash`) or a server-side filter
// (`query`). `gated` marks one the frames show as not yet available; it renders as a label,
// never as a link to nothing.
export const SECTIONS = [
  { id: "event.overview", slug: "", label: "Event Overview", icon: FiHome },
  {
    id: "event.prep", slug: "preparation", label: "Preparation", icon: FiClipboard,
    children: [
      { key: "hub", label: "Preparation hub" },
      { key: "details", label: "Details & Schedule", hash: "details" },
      { key: "sources", label: "Sources & Ingest", hash: "sources" },
      { key: "readiness", label: "Readiness Checks", hash: "readiness" },
      { key: "team", label: "Event team", hash: "team" },
    ],
  },
  {
    id: "event.access", slug: "access", label: "Audience Access", icon: FiShield,
    children: [
      { key: "overview", label: "Access overview" },
      { key: "preview", label: "Viewer experience preview", hash: "viewer-preview" },
      { key: "diagnostics", label: "Access diagnostics", hash: "diagnostics" },
      { key: "invitations", label: "Invitations", hash: "registrations" },
      { key: "links", label: "Links & revocation", hash: "access-links" },
    ],
  },
  {
    id: "event.live", slug: "live", label: "Live Control", icon: FiRadio,
    children: [
      { key: "monitor", label: "Live monitor", hash: "monitor" },
      { key: "sessions", label: "Stream health & sessions", hash: "sessions" },
      { key: "incidents", label: "Alerts & incidents", hash: "incidents" },
      { key: "recovery", label: "Recovery procedures", hash: "recovery" },
    ],
  },
  {
    id: "event.replay", slug: "replay", label: "Replay & Assets", icon: FiFilm,
    children: [
      { key: "overview", label: "Replay overview" },
      { key: "recordings", label: "Recordings & processing", hash: "recordings" },
      { key: "reports", label: "Reports", hash: "reports" },
      { key: "retention", label: "Retention & export", hash: "retention" },
    ],
  },
  {
    id: "event.insights", slug: "insights", label: "Insights", icon: FiBarChart2,
    children: [
      { key: "overview", label: "Insights overview", hash: "overview" },
      { key: "viewers", label: "Viewer activity", hash: "viewers" },
      { key: "quality", label: "Stream quality", hash: "quality" },
      { key: "outcomes", label: "Event outcomes", hash: "outcomes" },
      { key: "feedback", label: "Viewer feedback", hash: "feedback" },
    ],
  },
  {
    id: "event.activity", slug: "activity", label: "Activity & Audit", icon: FiActivity,
    children: [
      { key: "timeline", label: "Activity timeline" },
      { key: "decisions", label: "Audit decisions", query: "outcome=refused" },
      { key: "evidence", label: "Evidence records", query: "category=evidence" },
      { key: "exports", label: "Export requests", gated: true },
    ],
  },
];

export const sectionPath = (eventId, slug = "") =>
  `/organization/events/${eventId}${slug ? `/${slug}` : ""}`;

// The old single-page event screen kept its tab in ?tab=. Links in old emails, bookmarks and
// the Recordings library's failure notice still carry one, so each maps to the section that
// now holds that content.
export const LEGACY_TABS = {
  Overview: "",
  Hosts: "preparation",
  Speakers: "preparation",
  Settings: "preparation",
  Billing: "preparation",
  Registration: "access",
  Feedback: "insights",
  Analytics: "insights",
  Recording: "replay",
  Reports: "replay",
};

// What a section receives from EventWorkspaceLayout: the event (always loaded — the layout
// shows the loading / not-found / error state instead of the section until it is), its
// readiness verdict, and the reloads that re-read them after a change.
export const useEventWorkspace = () => useOutletContext();

export const HASH_ALIASES = {
  "live-monitor": "monitor",
  "stream-health": "sessions",
};

/**
 * Resolves a section target DOM element by ID or supported alias.
 */
export function resolveTargetElement(id) {
  if (!id) return null;
  const canonicalId = HASH_ALIASES[id] || id;
  return (
    document.getElementById(id) ||
    document.getElementById(canonicalId) ||
    (canonicalId === "monitor" && document.querySelector("#monitor, [data-section='live-monitor']")) ||
    (canonicalId === "sessions" && document.querySelector("#sessions, [data-section='stream-health']")) ||
    null
  );
}

/**
 * Scrolls smoothly to a target element, ensuring sticky header clearance.
 */
export function scrollToSection(elementOrId) {
  const el = typeof elementOrId === "string" ? resolveTargetElement(elementOrId) : elementOrId;
  if (!el) return false;

  try {
    el.scrollIntoView?.({ block: "start", behavior: "smooth" });
  } catch {
    // Unsupported in older test environments
  }

  let container = el.parentElement;
  while (container && container !== document.body) {
    const { overflow, overflowY } = window.getComputedStyle(container);
    if (/(auto|scroll|overlay)/.test(`${overflow} ${overflowY}`)) break;
    container = container.parentElement;
  }

  // Organization pages scroll inside the shell's main element, not the document viewport.
  if (!container || container === document.body) {
    container = el.closest("main") || document.scrollingElement;
  }
  if (!container) return false;

  const targetTop = el.getBoundingClientRect().top;
  const containerTop = container === document.scrollingElement
    ? 0
    : container.getBoundingClientRect().top;
  const marginTop = Number.parseFloat(window.getComputedStyle(el).scrollMarginTop) || 0;
  const top = Math.max(0, container.scrollTop + targetTop - containerTop - marginTop);

  if (typeof container.scrollTo === "function") {
    try {
      container.scrollTo({ top, behavior: "smooth" });
    } catch {
      container.scrollTop = top;
    }
  } else {
    container.scrollTop = top;
  }

  return true;
}

export const scrollToHashTarget = scrollToSection;

// A sub-item link (#details) moves to that part of the page. React Router changes the URL but
// does not scroll. A target may render after its data, so observe the page until it appears.
// Read-only: scrolling never triggers an event action.
export function useHashScroll({ supportedHashes, fallbackHash, ready = true } = {}) {
  const { pathname, search, hash } = useLocation();
  const navigate = useNavigate();

  // No hash (a section or parent link): back to the top of the page. Keyed on the hash alone,
  // so a query-string change does not move the page.
  useEffect(() => {
    if (!hash) {
      const main = document.querySelector("main.zk-guide-safe") || document.querySelector("main");
      if (main && typeof main.scrollTo === "function") {
        try {
          main.scrollTo({ top: 0, behavior: "smooth" });
        } catch {
          // Unsupported in older test environments
        }
      }
      if (typeof window !== "undefined" && typeof window.scrollTo === "function") {
        try {
          window.scrollTo({ top: 0, behavior: "smooth" });
        } catch {
          // Unsupported in older test environments
        }
      }
    }
  }, [hash]);

  useEffect(() => {
    if (!hash) return undefined;

    let id;
    try {
      id = decodeURIComponent(hash.slice(1));
    } catch {
      id = "";
    }

    const canonicalId = HASH_ALIASES[id] || id;

    const normalizeToFallback = () => {
      if (!fallbackHash || hash === `#${fallbackHash}`) return;
      navigate({ pathname, search, hash: `#${fallbackHash}` }, { replace: true });
    };

    const isSupported =
      !supportedHashes ||
      supportedHashes.includes(id) ||
      supportedHashes.includes(canonicalId);

    if (!id || !isSupported) {
      normalizeToFallback();
      return undefined;
    }

    // When the component is still loading asynchronous data, defer scrolling until ready
    if (!ready) {
      return undefined;
    }

    let timeoutId;
    let observer;

    const attemptScroll = () => {
      const target = resolveTargetElement(id);
      if (target) {
        scrollToSection(target);
        return true;
      }
      return false;
    };

    if (!attemptScroll()) {
      observer = new MutationObserver(() => {
        if (attemptScroll()) {
          observer.disconnect();
          clearTimeout(timeoutId);
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });

      timeoutId = setTimeout(() => {
        observer?.disconnect();
        normalizeToFallback();
      }, 10_000);
    }

    return () => {
      observer?.disconnect();
      clearTimeout(timeoutId);
    };
  }, [fallbackHash, hash, navigate, pathname, search, supportedHashes, ready]);
}
