// The only Guide piece in the main bundle: decides whether the current page is eligible and,
// if so, lazy-loads the widget once the page has rendered (Architecture §4: Guide must never
// delay the page, sign-in or the player). Any failure — bundle, bootstrap, API down, kill
// switch — renders nothing and the website carries on (PR-041, G4).
import { Component, Suspense, lazy, useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { guidePageFor } from "./pageContext";
import { isConsolePath } from "../operator-guide/eligibility";
import { askGuide, parseGuideParam } from "./askGuide";

const GuideWidget = lazy(() => import("./GuideWidget"));

class Isolate extends Component {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    /* Guide failure is contained here by design; the host page is unaffected. */
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

function useAfterIdle() {
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const go = () => setReady(true);
    if (typeof window.requestIdleCallback === "function") {
      const id = window.requestIdleCallback(go, { timeout: 2500 });
      return () => window.cancelIdleCallback?.(id);
    }
    const t = setTimeout(go, 1200);
    return () => clearTimeout(t);
  }, []);
  return ready;
}

// `?guide=open` / `?guide=<quick-action-id>` (Engineering Wireframe §2.1): read once, handed to
// the widget as an open request, and removed from the address bar so a reload or a shared link
// does not reopen Guide. Only a known, non-sensitive id is honoured; nothing is ever sent.
// Returns true while a valid request is present, so Guide loads now rather than after idle.
function useGuideDeepLink(eligible) {
  const { pathname, search, hash } = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(search);
  const raw = params.get("guide");
  const parsed = params.has("guide") ? parseGuideParam(raw) : null;
  useEffect(() => {
    const p = new URLSearchParams(search);
    if (!p.has("guide")) return;
    const value = p.get("guide");
    const request = parseGuideParam(value);
    p.delete("guide");
    const rest = p.toString();
    navigate({ pathname, search: rest ? `?${rest}` : "", hash }, { replace: true });
    if (request && eligible) {
      askGuide({ quickAction: request.intentId ? value.trim().toLowerCase() : null, entryPoint: "deep_link" });
    }
  }, [search, pathname, hash, navigate, eligible]);
  return !!parsed && eligible;
}

export default function GuideMount() {
  const { pathname } = useLocation();
  const { user } = useAuth() || {};
  const idle = useAfterIdle();
  const page = guidePageFor(pathname);
  // "/" is the operator dashboard for a signed-in user (LandingOrDashboard): the public Guide
  // never appears inside an authenticated console.
  // /guide is Guide itself as a full page: the popup steps aside there (one conversation per
  // screen), exactly as it does inside a console.
  const inConsole = isConsolePath(pathname) || (!!user && pathname === "/") || pathname === "/guide";
  const eligible = page && !inConsole;
  // A deep link is an explicit request to open Guide: load it now rather than after idle.
  const deepLinked = useGuideDeepLink(!!eligible);
  const ready = idle || deepLinked;
  // Once loaded, keep the widget mounted across navigation so an open conversation survives a
  // click on one of its own links; it hides itself on ineligible pages.
  const [loaded, setLoaded] = useState(false);
  if (eligible && ready && !loaded) setLoaded(true); // sticky: derived during render
  if (!loaded) return null;
  return (
    <Isolate>
      <Suspense fallback={null}>
        {/* On a console page the widget closes and renders nothing: the Operator Guide owns
            that corner, and a public panel left open must not sit on top of it. */}
        <GuideWidget page={eligible ? page : null} pathname={pathname} suppressed={inConsole} variant="overlay" />
      </Suspense>
    </Isolate>
  );
}
