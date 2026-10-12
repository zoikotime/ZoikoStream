// The only Operator Guide piece in the main bundle. Decides eligibility — a signed-in host,
// org admin or super admin on a console page — and lazy-loads Guide after the page is idle,
// so Guide can never delay the console. Any failure renders nothing (the console carries on).
import { Component, Suspense, lazy, useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { consoleRoute } from "./operatorGuideApi";
import { GUIDE_ROLES, eventIdFrom, isConsolePath } from "./eligibility";

const OperatorGuide = lazy(() => import("./OperatorGuide"));

class Isolate extends Component {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch() {
    /* Guide failure is contained here by design; the console is unaffected. */
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

export default function OperatorGuideMount() {
  const { pathname, search } = useLocation();
  const { user } = useAuth() || {};
  const ready = useAfterIdle();
  const eligible = !!user && GUIDE_ROLES.includes(user.role) && isConsolePath(pathname);
  if (!eligible || !ready) return null;
  return (
    <Isolate>
      <Suspense fallback={null}>
        <OperatorGuide route={consoleRoute(pathname, search)} routeEventId={eventIdFrom(pathname, search)} />
      </Suspense>
    </Isolate>
  );
}
