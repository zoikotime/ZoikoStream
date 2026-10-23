// Support & Status — the three trust links, end to end, in one place.
//
// Reported: Trust & Evidence (the footer's Trust Center link in that section), Security
// Overview and Data Residency all opened Settings, on the change-password form. Until cbf1893
// (2026-10-07) all three pointed at /organization/settings?tab=security; since then each has
// its own page (TrustEvidence.jsx, pages/Trust.jsx). TrustEvidence.test.jsx and
// SupportStatusTrustCenter.test.jsx pin each link on its own; this walks all three the way a
// user does — click, the page that renders, Back, Forward — and checks that Settings is never
// on the way. The same was verified in Chromium on the web and mobile builds.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Trust from "../Trust";
import SupportStatus from "./SupportStatus";
import { DataResidency, SecurityOverview } from "./TrustEvidence";

const SUPPORT = "/organization/support";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) => Promise.resolve({ data: url === "/privacy/subprocessors" ? [] : {} }));
});

// Every location the router has shown, so a hop through Settings cannot go unseen.
let seen;
function Chrome() {
  const location = useLocation();
  const navigate = useNavigate();
  seen.push(location.pathname + location.search);
  return (
    <>
      <output data-testid="location">{location.pathname + location.search}</output>
      <button type="button" onClick={() => navigate(-1)}>Browser back</button>
      <button type="button" onClick={() => navigate(1)}>Browser forward</button>
    </>
  );
}

const open = () => {
  seen = [];
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[SUPPORT]}>
        <Chrome />
        <Routes>
          <Route path={SUPPORT} element={<SupportStatus />} />
          <Route path="/organization/support/security" element={<SecurityOverview />} />
          <Route path="/organization/support/data-residency" element={<DataResidency />} />
          <Route path="/trust" element={<Trust />} />
          <Route path="/organization/settings" element={<h1>Account Security</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
};
const location = () => screen.getByTestId("location").textContent;

describe.each([
  ["Security overview", { name: /^Security overview:/ }, "/organization/support/security", "Security Overview"],
  ["Data residency", { name: /^Data residency:/ }, "/organization/support/data-residency", "Data Residency"],
  ["Trust Center (Trust & evidence)", { name: "Trust Center" }, "/trust", /Security, transparently/],
])("%s", (_, link, destination, heading) => {
  it("opens its own page, never Settings, and Back and Forward move between them", async () => {
    const user = userEvent.setup();
    open();
    await screen.findByRole("heading", { name: /trust & evidence/i });

    const anchor = screen.getByRole("link", link);
    expect(anchor).toHaveAttribute("href", destination);
    await user.click(anchor);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
    expect(location()).toBe(destination);

    await user.click(screen.getByRole("button", { name: "Browser back" }));
    expect(await screen.findByRole("heading", { name: /trust & evidence/i })).toBeInTheDocument();
    expect(location()).toBe(SUPPORT);

    await user.click(screen.getByRole("button", { name: "Browser forward" }));
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
    expect(location()).toBe(destination);

    expect(seen.some((path) => path.startsWith("/organization/settings"))).toBe(false);
    expect(screen.queryByRole("heading", { name: "Account Security" })).toBeNull();
  });
});
