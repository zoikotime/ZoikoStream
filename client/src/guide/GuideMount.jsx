// The only Guide piece in the main bundle: decides whether the current page is eligible and,
// if so, lazy-loads the widget once the page has rendered (Architecture §4: Guide must never
// delay the page, sign-in or the player). Any failure — bundle, bootstrap, API down, kill
// switch — renders nothing and the website carries on (PR-041, G4).
import { Component, Suspense, lazy, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { guidePageFor } from "./pageContext";

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

export default function GuideMount() {
  const { pathname } = useLocation();
  const { user } = useAuth() || {};
  const ready = useAfterIdle();
  const page = guidePageFor(pathname);
  // "/" is the operator dashboard for a signed-in user (LandingOrDashboard): the public Guide
  // never appears inside an authenticated console.
  const eligible = page && !(user && pathname === "/");
  // Once loaded, keep the widget mounted across navigation so an open conversation survives a
  // click on one of its own links; it hides itself on ineligible pages.
  const [loaded, setLoaded] = useState(false);
  if (eligible && ready && !loaded) setLoaded(true); // sticky: derived during render
  if (!loaded) return null;
  return (
    <Isolate>
      <Suspense fallback={null}>
        <GuideWidget page={eligible ? page : null} pathname={pathname} />
      </Suspense>
    </Isolate>
  );
}
