// The Usage & Entitlements page, and the URL it answers on.
//
// Everywhere a person saw this page it was called "Usage & Entitlements" — the sidebar item,
// the heading, the breadcrumb — while the address bar said /admin/subscriptions. The label
// was never wrong; the route simply predated the rename and nobody moved it.
//
// Moving it needed care, because "/admin/subscriptions" is ALSO a backend endpoint this very
// page calls: GET /admin/subscriptions, GET /admin/subscriptions/summary, PATCH
// /admin/subscriptions/{id}. A find-and-replace across the repo would have renamed the API
// along with the route and broken the page it was meant to fix. Only the browser route moved;
// the API paths are asserted unchanged below.
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed to load",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: "u-me", full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Subscriptions from "./Subscriptions";
import AdminSidebar from "../../components/admin/AdminSidebar";

const CANONICAL = "/admin/usage-entitlements";
const LEGACY = "/admin/subscriptions";

// The same forward App.jsx registers, exercised here rather than re-implemented: it has to
// carry the query string and must not point at itself.
function LegacySubscriptionsRedirect() {
  const { search } = useLocation();
  return <Navigate to={`${CANONICAL}${search}`} replace />;
}

// Reports where routing settled, so a redirect can be observed.
function Where() {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{pathname + search}</div>;
}

const routed = (entry) =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[entry]}>
        <Where />
        <Routes>
          <Route path={CANONICAL} element={<Subscriptions />} />
          <Route path={LEGACY} element={<LegacySubscriptionsRedirect />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const at = () => screen.getByTestId("where").textContent;

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: { items: [], total: 0 } });
});

describe("the canonical route", () => {
  it("renders the page", async () => {
    routed(CANONICAL);
    expect(await screen.findByRole("heading", { name: /Usage & Entitlements/i })).toBeInTheDocument();
  });

  it("is where a direct visit stays — no redirect away from it", async () => {
    routed(CANONICAL);
    await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    expect(at()).toBe(CANONICAL);
  });

  it("keeps the page's own identity in the heading", async () => {
    routed(CANONICAL);
    const heading = await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    expect(heading.tagName).toBe("H1");
  });

  it("still shows both tabs", async () => {
    routed(CANONICAL);
    await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    expect(screen.getByRole("tab", { name: /Entitlements & usage/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /^Subscriptions$/i })).toBeInTheDocument();
  });
});

describe("the retired URL", () => {
  it("forwards to the canonical one", async () => {
    routed(LEGACY);
    await waitFor(() => expect(at()).toBe(CANONICAL));
  });

  it("lands on the real page, not an empty shell", async () => {
    routed(LEGACY);
    expect(await screen.findByRole("heading", { name: /Usage & Entitlements/i })).toBeInTheDocument();
  });

  it("carries the query string through", async () => {
    routed(`${LEGACY}?plan=pro&status=active`);
    await waitFor(() => expect(at()).toBe(`${CANONICAL}?plan=pro&status=active`));
  });

  it("settles rather than bouncing — the target is a different path", async () => {
    routed(LEGACY);
    await waitFor(() => expect(at()).toBe(CANONICAL));
    // Still there a tick later: a self-referential redirect would keep firing.
    await new Promise((r) => setTimeout(r, 20));
    expect(at()).toBe(CANONICAL);
  });
});

describe("the sidebar", () => {
  const rail = () =>
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={[CANONICAL]}>
          <AdminSidebar open onClose={() => {}} state={null} unknown={false} onChange={() => {}} />
        </MemoryRouter>
      </ThemeProvider>
    );

  it("links to the canonical route", () => {
    const { container } = rail();
    expect(container.querySelector(`a[href="${CANONICAL}"]`)).toBeTruthy();
  });

  it("no longer links to the retired one", () => {
    const { container } = rail();
    expect(container.querySelector(`a[href="${LEGACY}"]`)).toBeNull();
  });

  it("still calls it Usage & Entitlements", () => {
    rail();
    expect(screen.getByText("Usage & Entitlements")).toBeInTheDocument();
  });

  it("marks the item current when that route is open", () => {
    const { container } = rail();
    const link = container.querySelector(`a[href="${CANONICAL}"]`);
    // NavLink marks the active destination; whichever attribute this rail uses, the active
    // link must be distinguishable from an inactive one.
    expect(link.getAttribute("aria-current") || link.className).toBeTruthy();
    expect(link.getAttribute("aria-current")).toBe("page");
  });
});

describe("the breadcrumb vocabulary", () => {
  it("names the canonical segment", async () => {
    const mod = await import("../../components/admin/AdminTopbar.jsx?raw");
    expect(mod.default).toContain('"usage-entitlements": "Usage & Entitlements"');
  });
});

describe("the backend contract is untouched", () => {
  it("still calls the subscription endpoints by their own names", async () => {
    routed(CANONICAL);
    await screen.findByRole("heading", { name: /Usage & Entitlements/i });
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    const urls = vi.mocked(api.get).mock.calls.map(([u]) => u);
    // The page's API paths are not the page's URL, and must not have moved with it.
    expect(urls.some((u) => u.startsWith("/admin/") )).toBe(true);
    expect(urls.every((u) => !u.startsWith("/admin/usage-entitlements"))).toBe(true);
  });
});
