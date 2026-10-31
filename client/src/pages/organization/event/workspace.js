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
  // Live Control aliases
  "live-monitor": "monitor",
  "stream-health": "sessions",
  // Audience Access aliases
  "invitations": "registrations",
  "links": "access-links",
  // Preparation aliases
  "details-schedule": "details",
  "schedule": "details",
  "sources-ingest": "sources",
  "readiness-checks": "readiness",
  "event-team": "team",
  // Replay aliases
  "assets": "recordings",
  "preservation": "retention",
  // Insights aliases
  "viewer-activity": "viewers",
  "stream-quality": "quality",
  "outcomes-viewing": "outcomes",
  // Activity aliases
  "timeline": "timeline",
};

// The canonical section for a hash. Own keys only: a hash such as #constructor must not pick up
// an Object.prototype member.
export const canonicalHash = (id) =>
  (Object.prototype.hasOwnProperty.call(HASH_ALIASES, id) ? HASH_ALIASES[id] : id);

/**
 * Resolves a section target DOM element by ID or supported alias.
 *
 * The id comes from the URL, so it is compared, never built into a CSS selector: #a%22b%5D
 * decodes to `a"b]`, and as `[data-section="a"b]"]` querySelector threw inside the hash
 * effect and took the page down.
 */
export function resolveTargetElement(id) {
  if (!id || typeof id !== "string") return null;
  const canonicalId = canonicalHash(id);
  const byId = document.getElementById(id) || document.getElementById(canonicalId);
  if (byId) return byId;
  const sections = [...document.querySelectorAll("[data-section]")];
  return (
    sections.find((el) => el.getAttribute("data-section") === id) ||
    sections.find((el) => canonicalHash(el.getAttribute("data-section")) === canonicalId) ||
    null
  );
}

// Back to the top: the shell's <main> when it is the scroller (a locked-viewport shell), the
// window otherwise. Both are set to 0; the one that does not scroll is a no-op.
function scrollPageToTop() {
  const main = document.querySelector("main.zk-guide-safe") || document.querySelector("main");
  if (main && typeof main.scrollTo === "function") {
    try {
      main.scrollTo({ top: 0, behavior: "smooth" });
    } catch {
      main.scrollTop = 0;
    }
  } else if (main) {
    main.scrollTop = 0;
  }

  if (typeof window.scrollTo === "function") {
    try {
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch {
      const docEl = document.scrollingElement || document.documentElement;
      if (docEl) docEl.scrollTop = 0;
    }
  }
}

/**
 * Scrolls smoothly to a target element, ensuring sticky header clearance.
 * Handles nested scroll containers (<main>) and the document/window viewport.
 */
export function scrollToSection(elementOrId) {
  if (elementOrId === "top") {
    scrollPageToTop();
    return true;
  }

  const el = typeof elementOrId === "string" ? resolveTargetElement(elementOrId) : elementOrId;
  if (!el) return false;

  // The nearest ancestor that scrolls by style; failing that, the shell's <main>.
  let container = el.parentElement;
  while (container && container !== document.body && container !== document.documentElement) {
    const { overflow, overflowY } = window.getComputedStyle(container);
    if (/(auto|scroll|overlay)/.test(`${overflow} ${overflowY}`)) break;
    container = container.parentElement;
  }
  if (!container || container === document.body || container === document.documentElement) {
    container = el.closest("main");
  }

  // Exactly one scroller moves. The organization shell's <main> is overflow-auto but grows with
  // its content (AppShell without fullHeight), so there the window scrolls; a locked-viewport
  // shell scrolls <main>. Scrolling both started two smooth scrolls toward different offsets.
  const docEl = document.scrollingElement || document.documentElement;
  const containerScrolls = Boolean(container) && container.scrollHeight > container.clientHeight;
  const windowScrolls = Boolean(docEl) && docEl.scrollHeight > window.innerHeight;
  const useWindow = !container || (!containerScrolls && windowScrolls);

  // A scroll-margin on the target wins. Without one, clear a sticky header that covers the
  // scrolled content: the shell's top bar when the window scrolls (it sits outside <main>),
  // or one inside the scrolling container.
  const marginTop = Number.parseFloat(window.getComputedStyle(el).scrollMarginTop) || 0;
  const header = useWindow
    ? document.querySelector("header.sticky")
    : container.querySelector("header.sticky");
  const offset = marginTop > 0 ? marginTop : (header ? header.getBoundingClientRect().height : 0) + 16;

  if (useWindow) {
    const current = window.pageYOffset || (docEl ? docEl.scrollTop : 0) || 0;
    const top = Math.max(0, current + el.getBoundingClientRect().top - offset);
    if (typeof window.scrollTo === "function") {
      try {
        window.scrollTo({ top, behavior: "smooth" });
      } catch {
        if (docEl) docEl.scrollTop = top;
      }
    } else if (docEl) {
      docEl.scrollTop = top;
    }
    return true;
  }

  const top = Math.max(
    0,
    container.scrollTop + el.getBoundingClientRect().top - container.getBoundingClientRect().top - offset,
  );
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

// A workspace link's click. useHashScroll (below) scrolls when the URL's hash or query changes,
// on the page that renders the destination and once its target exists. So a click scrolls by
// itself only where no such change will follow: a repeated click on the current item, or a
// top-of-page link when the hash is already empty (a query-only change does not run the
// no-hash effect). Anything else, a link to another section included, is left to the
// destination: scrolling at click time would move the page being left, which can hold an
// element with the same id (several sections have #overview).
export function scrollForSamePageLink(location, to) {
  if (location.pathname !== to.pathname) return false;
  const toHash = to.hash || "";
  if (toHash !== (location.hash || "")) return false;
  if (!toHash) return scrollToSection("top");
  if ((to.search || "") !== (location.search || "")) return false;
  let id;
  try {
    id = decodeURIComponent(toHash.slice(1));
  } catch {
    return false;
  }
  return scrollToSection(id);
}

// A sub-item link (#details) moves to that part of the page. React Router changes the URL but
// does not scroll. A target may render after its data, so observe the page until it appears.
// Read-only: scrolling never triggers an event action.
export function useHashScroll({ supportedHashes, fallbackHash, ready = true } = {}) {
  const { pathname, search, hash } = useLocation();
  const navigate = useNavigate();

  // No hash (a section or parent link): back to the top of the page. Keyed on the hash alone,
  // so a query-string change does not move the page.
  useEffect(() => {
    if (!hash) scrollPageToTop();
  }, [hash]);

  useEffect(() => {
    if (!hash) return undefined;

    let id;
    try {
      id = decodeURIComponent(hash.slice(1));
    } catch {
      id = "";
    }

    const canonicalId = canonicalHash(id);

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

    // Every hash change scrolls here, Back/Forward included. There is deliberately no "already
    // scrolled" memory: a module-level one outlived the navigation it was set for, and a later
    // Back to that hash (after a page without one) was skipped. Clicks that do not change the
    // URL scroll themselves (scrollForSamePageLink), so nothing scrolls twice.

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
