// Support & Status › Trust & evidence — "Compliance documents" and "Report a vulnerability".
//
// Reported: clicking either did nothing. They were deliberately inert cards marked "On
// request", but both flows exist: the public Trust Center (pages/Trust.jsx) has a document
// request form (#evidence, POST /trust/evidence/requests) and the protected vulnerability
// report form (#report, POST /trust/security/reports). The cards now open those forms, and
// the Trust Center goes to the requested section once it has loaded — it renders both only
// after GET /trust answers, so the browser's own fragment scroll could never find them.
// The vulnerability card also no longer promises a security.txt the deployment does not serve.
//
// Arriving at a form submits nothing; whatever happens next is the Trust Center's own flow,
// with its own validation and responses, unchanged here.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));

import api from "../../api";
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import Trust from "../Trust";
import SupportStatus from "./SupportStatus";

const SUPPORT = "/organization/support";
const OVERVIEW = {
  generated_at: "2026-10-22T09:00:00Z",
  organization: { name: "Northwind", region: "US East" },
  service_health: { status: "ok", label: "Healthy", cause: null },
  security_support: { maintenance_window: null },
  lifecycle: [{ stage: "platform", label: "Platform", status: "ok", in_use: true, availability: 100, detail: null, open_incidents: 0 }],
  sessions: { items: [] }, entitlements: {}, developer_ops: {},
};

let scrolled;
beforeEach(() => {
  vi.clearAllMocks();
  scrolled = [];
  // jsdom has no layout; record what the page asked to bring into view.
  Element.prototype.scrollIntoView = vi.fn(function scrollIntoView() { scrolled.push(this.id); });
  vi.mocked(api.get).mockImplementation((url) =>
    Promise.resolve({ data: url === "/organization/overview" ? OVERVIEW : url === "/trust" ? { advisories: [], documents: [] } : {} }));
});
afterEach(() => {
  delete Element.prototype.scrollIntoView;
});

function Where() {
  const { pathname, hash } = useLocation();
  return <output data-testid="location">{pathname + hash}</output>;
}
const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[SUPPORT]}>
        <Where />
        <Routes>
          <Route path={SUPPORT} element={<SupportStatus />} />
          <Route path="/trust" element={<Trust />} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
const supportReady = () => screen.findByRole("heading", { name: /trust & evidence/i });
const location = () => screen.getByTestId("location").textContent;

describe.each([
  ["Compliance documents", /^Compliance documents:/, "/trust#evidence", "evidence", "Request trust documentation"],
  ["Report a vulnerability", /^Report a vulnerability:/, "/trust#report", "report", "Report a security issue"],
])("%s", (_, name, destination, sectionId, sectionHeading) => {
  it("opens the Trust Center at its form, and puts the reader there", async () => {
    const user = userEvent.setup();
    open();
    await supportReady();
    const card = screen.getByRole("link", { name });
    expect(card).toHaveAttribute("href", destination);

    await user.click(card);

    expect(await screen.findByRole("heading", { name: sectionHeading })).toBeInTheDocument();
    expect(location()).toBe(destination);
    const section = document.getElementById(sectionId);
    await waitFor(() => expect(document.activeElement).toBe(section));
    expect(scrolled).toEqual([sectionId]);
  });

  it("works from the keyboard", async () => {
    const user = userEvent.setup();
    open();
    await supportReady();
    const card = screen.getByRole("link", { name });
    card.focus();
    expect(card).toHaveFocus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: sectionHeading })).toBeInTheDocument();
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById(sectionId)));
  });

  it("submits nothing and claims nothing by arriving", async () => {
    const user = userEvent.setup();
    open();
    await supportReady();
    await user.click(screen.getByRole("link", { name }));
    await screen.findByRole("heading", { name: sectionHeading });
    expect(api.post).not.toHaveBeenCalled();
    expect(notify.success).not.toHaveBeenCalled();
  });
});

describe("the Trust & evidence cards together", () => {
  it("are all real links now, none inert, and the others still go where they went", async () => {
    open();
    await supportReady();
    expect(screen.queryByText(/on request/i)).toBeNull();
    expect(screen.getByRole("link", { name: /^Security overview:/ })).toHaveAttribute("href", "/organization/support/security");
    expect(screen.getByRole("link", { name: /^Data residency:/ })).toHaveAttribute("href", "/organization/support/data-residency");
    expect(screen.getByRole("link", { name: "Trust Center" })).toHaveAttribute("href", "/trust");
  });

  it("promise no security.txt, which this deployment does not serve", async () => {
    open();
    await supportReady();
    expect(screen.queryByText(/security\.txt/i)).toBeNull();
    expect(screen.getByRole("link", { name: /^Report a vulnerability:/ })).toHaveAccessibleName(
      "Report a vulnerability: Coordinated disclosure through a protected channel.");
  });
});

describe("the Trust Center opened without a section", () => {
  it("stays at the top and takes no focus", async () => {
    render(
      <MemoryRouter initialEntries={["/trust"]}>
        <Routes><Route path="/trust" element={<Trust />} /></Routes>
      </MemoryRouter>
    );
    await screen.findByRole("heading", { name: "Report a security issue" });
    expect(scrolled).toEqual([]);
    expect(document.activeElement).toBe(document.body);
  });
});
