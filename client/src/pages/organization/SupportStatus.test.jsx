// Support & Status — what it is allowed to claim.
//
// ── THE PROBLEM ────────────────────────────────────────────────────────────────────────
// A status page's whole value is that you can believe it. This one printed "100.00%" under
// a column headed "Uptime (24 h)" for eight services, including ones nothing in this
// deployment has ever probed. The figure is real arithmetic over the incident log —
// services/ops.availability() is 100 − (recorded incident minutes ÷ window) and its own
// docstring calls it "a measurement of 'nothing was recorded', not an assumption of
// perfection" — but printed under "Uptime" it asserts a measurement nobody takes.
//
// It also said "No maintenance scheduled" from a field that is hardcoded None, and buried
// Contact in a 12px footer link below three screens of telemetry.
//
// So the assertions here are mostly about what must NOT appear.
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import SupportStatus from "./SupportStatus";

const stage = (code, label, over = {}) => ({
  stage: code, label, status: "ok", in_use: true,
  availability: 100, detail: null, open_incidents: 0, ...over,
});

// A payload shaped like /organization/overview: a probed stage, a configured-only stage,
// and one nothing watches.
const OVERVIEW = {
  generated_at: "2026-09-22T11:30:00Z",
  organization: { region: "US East" },
  service_health: { status: "ok", label: "Healthy", cause: null },
  security_support: { maintenance_window: null },
  lifecycle: [
    stage("platform", "Platform"),
    stage("understand", "Understand"),
    stage("secure", "Secure"),
    stage("produce", "Produce", { status: "not_configured", in_use: false, availability: 100 }),
    stage("deliver", "Deliver", { status: "not_configured", in_use: false, availability: 100 }),
  ],
};

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/support"]}>
        <Routes>
          <Route path="/organization/support" element={<SupportStatus />} />
          <Route path="/contact" element={<h1>Contact page</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );

const heading = () => screen.findByRole("heading", { level: 1 });
const contactButton = () => screen.getByRole("link", { name: /contact support/i });
const serviceTable = () => screen.getByRole("table");

// StatTile renders <p>label</p><p>value</p>. Read the VALUE element: the concatenated
// tile text ("Active incidents0") leaves no word boundary for a regex to anchor on.
const tileValue = (label) =>
  screen.getByText(label).parentElement.querySelectorAll("p")[1].textContent;

const serve = (payload) => vi.mocked(api.get).mockResolvedValue({ data: payload });

beforeEach(() => {
  vi.clearAllMocks();
  serve(OVERVIEW);
});

// ── 1 & 2. Contact Support ─────────────────────────────────────────────────────────────

describe("Contact Support", () => {
  it("is near the top, not buried in the footer", async () => {
    show();
    await heading();

    const button = contactButton();
    expect(button).toBeInTheDocument();
    // Above the service table — i.e. in the summary, not three screens down.
    expect(button.compareDocumentPosition(serviceTable()) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
  });

  it("navigates to the EXISTING contact page rather than a second contact system", async () => {
    const u = userEvent.setup();
    show();
    await heading();

    await u.click(contactButton());

    expect(await screen.findByRole("heading", { name: /contact page/i })).toBeInTheDocument();
  });

  it("keeps the footer link as secondary navigation", async () => {
    show();
    await heading();

    const links = screen.getAllByRole("link", { name: /^contact/i });
    expect(links.length).toBeGreaterThanOrEqual(2);   // the button and the footer link
  });
});

// ── 4. missing telemetry must not become 100% ──────────────────────────────────────────

describe("the incident-free column", () => {
  it("never calls itself uptime", async () => {
    show();
    await heading();

    expect(within(serviceTable()).queryByText(/uptime/i)).not.toBeInTheDocument();
    expect(within(serviceTable()).getByText(/incident-free/i)).toBeInTheDocument();
  });

  it("shows 'Not measured' for a service nothing probes, not 100.00%", async () => {
    // Produce and Deliver are not_configured — services/ops calls them informational,
    // nothing watches them, and the 100 in the payload is arithmetic over an empty set.
    show();
    await heading();

    const rows = within(serviceTable()).getAllByRole("row");
    const produce = rows.find((r) => r.textContent.includes("Transcoding"));
    expect(produce.textContent).toMatch(/not measured/i);
    expect(produce.textContent).not.toMatch(/100\.00%/);
  });

  it("still reports the figure for a service that IS watched", async () => {
    show();
    await heading();

    const rows = within(serviceTable()).getAllByRole("row");
    const platform = rows.find((r) => r.textContent.includes("Management API"));
    expect(platform.textContent).toMatch(/100\.00%/);
  });

  it("shows an em dash when no figure came back at all", async () => {
    serve({ ...OVERVIEW, lifecycle: [stage("platform", "Platform", { availability: null })] });
    show();
    await heading();

    const row = within(serviceTable()).getAllByRole("row")[1];
    expect(row.textContent).toMatch(/—/);
    expect(row.textContent).not.toMatch(/100/);
  });
});

// ── active incidents must not be a fabricated zero ─────────────────────────────────────

describe("the Active incidents tile", () => {
  it("shows 0 when the feed genuinely reported none", async () => {
    show();
    await heading();
    expect(tileValue("Active incidents")).toBe("0");
  });

  it("counts real open incidents", async () => {
    serve({
      ...OVERVIEW,
      lifecycle: [stage("platform", "Platform", { status: "warn", open_incidents: 2, detail: "Elevated errors" })],
    });
    show();
    await heading();

    expect(tileValue("Active incidents")).toBe("2");
  });

  it("shows an em dash, NOT 0, when the feed returned no service list", async () => {
    // "we could not ask" and "we asked and the answer is none" are different facts.
    serve({ ...OVERVIEW, lifecycle: [] });
    show();
    await heading();

    expect(tileValue("Active incidents")).toBe("—");
  });
});

// ── monitored services ─────────────────────────────────────────────────────────────────

describe("the Monitored services tile", () => {
  it("does not count stages nothing watches as monitored", async () => {
    // 5 stages, 2 of them not_configured.
    show();
    await heading();

    expect(tileValue("Monitored services")).toBe("3 of 5");
  });

  it("shows a plain count when everything really is monitored", async () => {
    serve({ ...OVERVIEW, lifecycle: [stage("platform", "Platform"), stage("secure", "Secure")] });
    show();
    await heading();

    expect(tileValue("Monitored services")).toBe("2");
  });
});

// ── 5/6/7. honest states ───────────────────────────────────────────────────────────────

describe("honest states", () => {
  it("keeps Not configured as Not configured", async () => {
    show();
    await heading();
    expect(within(serviceTable()).getAllByText(/not configured/i).length).toBeGreaterThan(0);
  });

  it("renders a real active incident from the payload", async () => {
    serve({
      ...OVERVIEW,
      lifecycle: [stage("deliver", "Deliver", { status: "down", open_incidents: 1, detail: "CDN origin timeouts" })],
    });
    show();
    await heading();

    expect(await screen.findByText(/cdn origin timeouts/i)).toBeInTheDocument();
    expect(screen.getByText(/major/i)).toBeInTheDocument();
  });

  it("does not claim maintenance is unscheduled when nothing tracks maintenance", async () => {
    show();
    await heading();

    // The backend field is hardcoded None, so absence cannot be confirmed.
    expect(screen.queryByText(/no maintenance scheduled/i)).not.toBeInTheDocument();
    expect(screen.getByText(/no maintenance calendar is connected/i)).toBeInTheDocument();
  });

  it("shows a real maintenance window when one is reported", async () => {
    serve({ ...OVERVIEW, security_support: { maintenance_window: "Sun 02:00–04:00 UTC" } });
    show();
    await heading();

    expect(await screen.findByText(/sun 02:00–04:00 utc/i)).toBeInTheDocument();
    expect(screen.getByText(/upcoming/i)).toBeInTheDocument();
  });
});

// ── regions ────────────────────────────────────────────────────────────────────────────

describe("regions", () => {
  it("presents the region as configuration, not measured delivery", async () => {
    show();
    await heading();

    expect(screen.getByText(/configured region · us east/i)).toBeInTheDocument();
    expect(screen.getByText(/no regional delivery telemetry yet/i)).toBeInTheDocument();
  });

  it("does not repeat the org's region on every service row", async () => {
    // It used to print "US East" eight times, implying per-service regional telemetry.
    show();
    await heading();

    expect(within(serviceTable()).queryByText("US East")).not.toBeInTheDocument();
    expect(within(serviceTable()).queryByText(/region/i)).not.toBeInTheDocument();
  });

  it("says so honestly when no region is configured", async () => {
    serve({ ...OVERVIEW, organization: {} });
    show();
    await heading();

    expect(screen.getByText(/no region configured/i)).toBeInTheDocument();
  });
});

// ── 8/9. refresh and failure ───────────────────────────────────────────────────────────

describe("refreshing", () => {
  it("offers a manual refresh that re-queries the feed", async () => {
    const u = userEvent.setup();
    show();
    await heading();
    const before = vi.mocked(api.get).mock.calls.length;

    await u.click(screen.getByRole("button", { name: /refresh/i }));

    await waitFor(() => expect(vi.mocked(api.get).mock.calls.length).toBeGreaterThan(before));
  });

  it("stamps the time from the server payload, not a clock read during render", async () => {
    show();
    await heading();

    expect(screen.getByText(/11:30 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/updated every 60 seconds/)).toBeInTheDocument();
  });

  it("shows an honest unavailable state when the feed fails", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("boom"));
    show();

    expect(await screen.findByText(/couldn’t load platform status/i)).toBeInTheDocument();
    // Critically: it does not claim the platform is down, and invents no figures.
    expect(screen.getByText(/does not itself mean the platform is down/i)).toBeInTheDocument();
    expect(screen.queryByText(/100\.00%/)).not.toBeInTheDocument();
  });
});

// ── 10. no dead UI ─────────────────────────────────────────────────────────────────────

describe("Trust & evidence", () => {
  it("links the two cards that have somewhere to go", async () => {
    show();
    await heading();

    expect(screen.getByRole("link", { name: /security overview/i })).toHaveAttribute("href", expect.stringContaining("/organization/settings"));
    expect(screen.getByRole("link", { name: /data residency/i })).toBeInTheDocument();
  });

  it("marks the two that do not, instead of leaving them looking clickable", async () => {
    show();
    await heading();

    expect(screen.queryByRole("link", { name: /compliance documents/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /report a vulnerability/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/on request/i)).toHaveLength(2);
  });
});
