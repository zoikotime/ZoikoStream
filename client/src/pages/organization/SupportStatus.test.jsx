// Support & Status — what it is allowed to claim.
//
// ── THE PROBLEM ────────────────────────────────────────────────────────────────────────
// A status page's whole value is that you can believe it. This one listed all eight internal
// lifecycle stages, so a customer read "Transcoding & packaging — Not configured — Not
// measured" for a stage with no service behind it at all, beside an "Incident-free (24 h)"
// column of 100.00% that was arithmetic over an empty incident log. It also presented the
// account's free-text region as a "delivery region" that routes nothing.
//
// The page now reads `service_status` (services/org.customer_status): only the services the
// platform can actually check, each with a status from real evidence, and a count of recorded
// incidents. The assertions are mostly about what must NOT appear.
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
import { formatMaintenanceWindow } from "../../data/supportStatus";

const svc = (id, name, over = {}) => ({
  id, name, summary: `${name} summary`, status: "ok",
  incidents_24h: 0, open_incidents: 0, incident: null, ...over,
});
const SERVICES = [
  svc("live_streaming", "Live streaming"),
  svc("recording", "Recording & replay"),
  svc("platform_api", "Management API"),
];

// Shaped like /organization/overview. `lifecycle` and `service_health` still carry the internal
// stage view for other pages — including a not-configured "Produce" stage — and this page must
// never surface either.
const OVERVIEW = {
  generated_at: "2026-09-22T11:30:00Z",
  organization: { region: "US East" },
  service_health: { status: "not_configured", label: "Not configured", cause: null },
  security_support: { maintenance_window: null },
  lifecycle: [{ stage: "produce", label: "Produce", status: "not_configured", in_use: false,
                availability: 100, detail: null, open_incidents: 0 }],
  service_status: { verdict: "ok", services: SERVICES },
  // Nothing published, and the schedule is not authoritative (no operator tool publishes yet).
  maintenance: { authoritative: false, upcoming: [] },
};
const withServices = (services, verdict = "ok") => ({ ...OVERVIEW, service_status: { verdict, services } });

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
const bodyRows = () => within(serviceTable()).getAllByRole("row").slice(1);
const rowFor = (name) => bodyRows().find((r) => r.textContent.includes(name));

// StatTile renders <p>label</p><p>value</p>. Read the VALUE element: the concatenated
// tile text ("Active incidents0") leaves no word boundary for a regex to anchor on.
const tileValue = (label) =>
  screen.getByText(label).parentElement.querySelectorAll("p")[1].textContent;

const serve = (payload) => vi.mocked(api.get).mockResolvedValue({ data: payload });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.removeItem("theme");
  serve(OVERVIEW);
});

// ── Contact Support ────────────────────────────────────────────────────────────────────

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

// ── the service table ──────────────────────────────────────────────────────────────────

describe("the service table", () => {
  it("lists the customer services the server can check — never the internal stages", async () => {
    show();
    await heading();
    expect(bodyRows().map((r) => r.querySelector("td span").textContent))
      .toEqual(["Live streaming", "Recording & replay", "Management API"]);
    expect(screen.queryByText(/transcoding|packaging|produce|origin & cdn/i)).not.toBeInTheDocument();
  });

  it("never shows an internal setup state or a synthesized percentage", async () => {
    show();
    await heading();
    const page = document.body.textContent;
    for (const phrase of [/not configured/i, /not measured/i, /not monitored/i, /incident-free/i, /uptime/i, /100\.00%/]) {
      expect(page).not.toMatch(phrase);
    }
  });

  it.each([["ok", "Operational"], ["warn", "Degraded"], ["down", "Major outage"]])(
    "shows a measured %s service as %s", async (status, label) => {
      serve(withServices([svc("live_streaming", "Live streaming", { status })], status));
      show();
      await heading();
      expect(within(rowFor("Live streaming")).getByText(label)).toBeInTheDocument();
    });

  it("counts recorded incidents instead of printing a percentage", async () => {
    serve(withServices([
      svc("live_streaming", "Live streaming"),
      svc("recording", "Recording & replay", { incidents_24h: 1 }),
      svc("platform_api", "Management API", { incidents_24h: 3 }),
    ]));
    show();
    await heading();
    expect(within(serviceTable()).getByRole("columnheader", { name: /incidents \(24 h\)/i })).toBeInTheDocument();
    expect(rowFor("Live streaming")).toHaveTextContent("0 incidents");
    expect(rowFor("Recording & replay")).toHaveTextContent("1 incident");
    expect(rowFor("Management API")).toHaveTextContent("3 incidents");
  });

  it("leaves out what the server could not check, and does not then claim everything is fine", async () => {
    serve(withServices([svc("live_streaming", "Live streaming"), svc("platform_api", "Management API")], "partial"));
    show();
    await heading();
    expect(bodyRows()).toHaveLength(2);
    expect(rowFor("Recording")).toBeUndefined();
    expect(screen.queryByText(/all systems operational/i)).not.toBeInTheDocument();
    expect(screen.getByText(/some services not reporting/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/can't confirm the status of every service/i);
  });

  it("drops a row whose status is not one of the three it can state", async () => {
    serve(withServices([svc("live_streaming", "Live streaming"), svc("x", "Mystery", { status: "not_configured" })]));
    show();
    await heading();
    expect(rowFor("Mystery")).toBeUndefined();
  });
});

// ── the headline verdict ───────────────────────────────────────────────────────────────

describe("the headline", () => {
  it("is all-green only when the server says every service was measured healthy", async () => {
    show();
    await heading();
    expect(screen.getByText(/all systems operational/i)).toBeInTheDocument();
  });

  it.each([["warn", /degraded performance/i], ["down", /service disruption/i]])(
    "reports a %s verdict as such", async (verdict, pill) => {
      serve(withServices([svc("live_streaming", "Live streaming", { status: verdict })], verdict));
      show();
      await heading();
      expect(screen.getByText(pill)).toBeInTheDocument();
    });

  it.each([
    ["nothing could be checked", withServices([], "unavailable")],
    ["the API sent no service status at all", { ...OVERVIEW, service_status: undefined }],
  ])("says status is unavailable — never green — when %s", async (_case, payload) => {
    serve(payload);
    show();
    await heading();
    expect(screen.getByText(/status temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/can't show live service status/i);
    expect(within(serviceTable()).getByText(/live service status is temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.queryByText(/operational/i)).not.toBeInTheDocument();
  });
});

// ── tiles ──────────────────────────────────────────────────────────────────────────────

describe("the tiles", () => {
  it("count the services shown and their open incidents", async () => {
    serve(withServices([
      svc("live_streaming", "Live streaming", { status: "warn", open_incidents: 2,
                                               incident: { title: "Elevated errors", severity: "minor" } }),
      svc("platform_api", "Management API"),
    ], "warn"));
    show();
    await heading();
    expect(tileValue("Monitored services")).toBe("2");
    expect(tileValue("Active incidents")).toBe("2");
  });

  it("shows 0 incidents only when the feed answered, and a dash when it did not", async () => {
    show();
    await heading();
    expect(tileValue("Active incidents")).toBe("0");
  });

  it("shows dashes, not zeros, when the feed carried no service status", async () => {
    serve({ ...OVERVIEW, service_status: undefined });
    show();
    await heading();
    expect(tileValue("Active incidents")).toBe("—");
    expect(tileValue("Monitored services")).toBe("—");
  });

  it("has no uptime tile, because nothing measures uptime", async () => {
    show();
    await heading();
    expect(screen.queryByText(/90-day uptime/i)).not.toBeInTheDocument();
  });
});

// ── incidents and maintenance ──────────────────────────────────────────────────────────

describe("incidents and maintenance", () => {
  it("renders a real active incident against the customer service it affects", async () => {
    serve(withServices([
      svc("live_streaming", "Live streaming", { status: "down", open_incidents: 1,
                                               incident: { title: "Media server unreachable", severity: "major" } }),
    ], "down"));
    show();
    await heading();

    expect(await screen.findByText(/media server unreachable/i)).toBeInTheDocument();
    expect(screen.getByText(/^major$/i)).toBeInTheDocument();
    expect(screen.getByText(/affects live streaming · 1 open incident/i)).toBeInTheDocument();
  });

});

// ── scheduled maintenance ──────────────────────────────────────────────────────────────

const WINDOW = {
  reference: "MNT-2026-0001", title: "Streaming node replacement", kind: "scheduled", status: "scheduled",
  starts_at: "2026-10-18T12:00:00+00:00", ends_at: "2026-10-18T14:00:00+00:00",
  impact: "Live sessions may briefly reconnect.", components: ["Live streaming", "Recording"], regions: ["Europe"],
};
const withMaintenance = (maintenance) => ({ ...OVERVIEW, maintenance });
const maintenancePanel = () => screen.getByText("Scheduled maintenance").closest("section, div[class*='rounded']");

describe("scheduled maintenance", () => {
  it("never shows implementation wording — calendar, scheduler, deployment", async () => {
    show();
    await heading();
    const page = document.body.textContent;
    for (const phrase of [/calendar/i, /scheduler/i, /this deployment/i, /not connected/i, /integrated/i]) {
      expect(page).not.toMatch(phrase);
    }
  });

  it("says updates will appear here — not 'no maintenance' — when the schedule is not authoritative", async () => {
    show();
    await heading();
    expect(screen.getByText("Maintenance updates will appear here when available.")).toBeInTheDocument();
    expect(screen.queryByText(/no scheduled maintenance/i)).not.toBeInTheDocument();
  });

  it("says there is none, exactly, when an empty schedule is authoritative", async () => {
    serve(withMaintenance({ authoritative: true, upcoming: [] }));
    show();
    await heading();
    expect(screen.getByText("No scheduled maintenance at this time.")).toBeInTheDocument();
    // Reads like its neighbour.
    expect(screen.getByText("No active incidents affecting your services.")).toBeInTheDocument();
  });

  it("shows a published window: title, state, local time, length, what it affects, impact", async () => {
    serve(withMaintenance({ authoritative: false, upcoming: [WINDOW] }));
    show();
    await heading();
    const panel = maintenancePanel();
    expect(within(panel).getByText("Streaming node replacement")).toBeInTheDocument();
    expect(within(panel).getByText("Scheduled")).toBeInTheDocument();
    // In the viewer's own locale and zone (exact formats are pinned in supportStatusMaintenance.test.js).
    const time = formatMaintenanceWindow(WINDOW.starts_at, WINDOW.ends_at);
    expect(panel).toHaveTextContent(`${time.when} · 2 h`);
    expect(within(panel).getByText(/affects live streaming, recording · europe/i)).toBeInTheDocument();
    expect(within(panel).getByText("Live sessions may briefly reconnect.")).toBeInTheDocument();
    expect(panel.textContent).not.toMatch(/MNT-/);                 // no internal reference
    expect(screen.queryByText(/maintenance updates will appear here/i)).not.toBeInTheDocument();
  });

  it("lists several windows in the order given, each with its own state", async () => {
    serve(withMaintenance({ authoritative: true, upcoming: [
      { ...WINDOW, reference: "MNT-a", title: "Database upgrade", status: "in_progress" },
      { ...WINDOW, reference: "MNT-b", title: "Edge rollout", starts_at: "2026-10-20T12:00:00+00:00",
        ends_at: "2026-10-20T13:00:00+00:00" },
      { ...WINDOW, reference: "MNT-c", title: "Urgent patch", kind: "emergency" },
    ] }));
    show();
    await heading();
    const items = within(maintenancePanel()).getAllByRole("listitem");
    expect(items.map((li) => li.querySelector("h3").textContent)).toEqual(["Database upgrade", "Edge rollout", "Urgent patch"]);
    expect(items.map((li) => li.querySelector("span").textContent)).toEqual(["In progress", "Scheduled", "Emergency"]);
    expect(screen.queryByText(/no scheduled maintenance/i)).not.toBeInTheDocument();
  });

  it("says the information is temporarily unavailable when the feed carried none", async () => {
    serve({ ...OVERVIEW, maintenance: undefined });
    show();
    await heading();
    expect(screen.getByText("Scheduled maintenance information is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.queryByText(/no scheduled maintenance/i)).not.toBeInTheDocument();
  });

  it("claims nothing about maintenance when the status feed fails", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("boom"));
    show();
    expect(await screen.findByText(/couldn’t load platform status/i)).toBeInTheDocument();
    expect(screen.queryByText(/no scheduled maintenance/i)).not.toBeInTheDocument();
  });

  it("renders the same in dark mode", async () => {
    localStorage.setItem("theme", "dark");
    serve(withMaintenance({ authoritative: false, upcoming: [WINDOW] }));
    show();
    await heading();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(within(maintenancePanel()).getByText("Streaming node replacement")).toBeInTheDocument();
  });
});

// ── region ─────────────────────────────────────────────────────────────────────────────

describe("the account region", () => {
  it("is not presented as delivery infrastructure — it routes nothing", async () => {
    show();
    await heading();
    expect(screen.queryByText(/configured region/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/delivery region/i)).not.toBeInTheDocument();
    expect(screen.queryByText("US East")).not.toBeInTheDocument();
  });
});

// ── refresh, failure and theme ─────────────────────────────────────────────────────────

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

  it("shows an honest unavailable state when the feed fails — no green, no figures", async () => {
    vi.mocked(api.get).mockRejectedValue(new Error("boom"));
    show();

    expect(await screen.findByText(/couldn’t load platform status/i)).toBeInTheDocument();
    expect(screen.getByText(/does not itself mean the platform is down/i)).toBeInTheDocument();
    expect(screen.queryByText(/operational/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/100\.00%/)).not.toBeInTheDocument();
  });

  it("renders the same table in dark mode", async () => {
    localStorage.setItem("theme", "dark");
    show();
    await heading();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(bodyRows()).toHaveLength(3);
    expect(within(rowFor("Live streaming")).getByText("Operational")).toBeInTheDocument();
  });
});

// ── no dead UI ─────────────────────────────────────────────────────────────────────────

describe("Trust & evidence", () => {
  it("links the two cards that have somewhere to go — each to its own page, not Settings", async () => {
    // They used to open Settings → Security (the change-password form). Navigation, Back and
    // the detail pages themselves are covered in TrustEvidence.test.jsx.
    show();
    await heading();

    expect(screen.getByRole("link", { name: /security overview/i })).toHaveAttribute("href", "/organization/support/security");
    expect(screen.getByRole("link", { name: /data residency/i })).toHaveAttribute("href", "/organization/support/data-residency");
  });

  // These two rendered inert ("On request") until they were pointed at the Trust Center's
  // existing request and report forms (SupportStatusTrustRequests.test.jsx).
  it("links the other two to the Trust Center's forms, leaving no card inert", async () => {
    show();
    await heading();

    expect(screen.getByRole("link", { name: /compliance documents/i })).toHaveAttribute("href", "/trust#compliance");
    expect(screen.getByRole("link", { name: /report a vulnerability/i })).toHaveAttribute("href", "/trust#report");
    expect(screen.queryByText(/on request/i)).toBeNull();
  });
});

// ── recording evidence, incidents that name no service, isolated failures ──────────────

describe("what Recording & replay may claim", () => {
  it("shows an idle row as neither Operational nor a fault, and qualifies the headline", async () => {
    serve(withServices([SERVICES[0], { ...SERVICES[1], status: "idle" }, SERVICES[2]]));
    show();
    await heading();
    const row = rowFor("Recording & replay");
    expect(within(row).getByText("No recent recordings")).toBeInTheDocument();
    expect(within(row).queryByText("Operational")).toBeNull();
    expect(within(row).getByText(/Recording is confirmed the next time you record/)).toBeInTheDocument();
    expect(screen.getByText("All checked services operational")).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Everything we can check is operating normally.");
    expect(document.body.textContent).not.toMatch(/not measured|not configured/i);
  });
});

describe("an incident that names no single service", () => {
  const withNotice = (notice, verdict) => ({
    ...OVERVIEW, service_status: { verdict, services: SERVICES, platform_notice: notice },
  });

  it("is the active incident, says it affects the platform, and is counted", async () => {
    serve(withNotice({ title: "Service disruption under investigation", severity: "major", scope: "platform", count: 1 }, "down"));
    show();
    await heading();
    expect(screen.getByText("Service disruption under investigation")).toBeInTheDocument();
    expect(screen.getByText(/Affects the ZoikoStream platform · 1 open incident/)).toBeInTheDocument();
    expect(screen.getByText("Major")).toBeInTheDocument();
    expect(tileValue("Active incidents")).toBe("1");
    expect(screen.queryByText("All systems operational")).toBeNull();
    expect(screen.queryByText(/No active incidents/)).toBeNull();
  });

  it("names the organization when the incident is this organization's own", async () => {
    serve(withNotice({ title: "Degraded performance under investigation", severity: "minor", scope: "organization", count: 1 }, "warn"));
    show();
    await heading();
    expect(screen.getByText(/Affects your organization · 1 open incident/)).toBeInTheDocument();
    expect(screen.getByText("Minor")).toBeInTheDocument();
  });

  it("gives way to a more severe incident on a service", async () => {
    const services = [svc("live_streaming", "Live streaming", {
      status: "down", open_incidents: 1, incident: { title: "Viewers cannot join", severity: "major" } }),
    SERVICES[1], SERVICES[2]];
    serve({ ...OVERVIEW, service_status: { verdict: "down", services,
      platform_notice: { title: "Degraded performance under investigation", severity: "minor", scope: "platform", count: 1 } } });
    show();
    await heading();
    expect(screen.getByText("Viewers cannot join")).toBeInTheDocument();
    expect(tileValue("Active incidents")).toBe("2");
  });
});

describe("when the server isolated a failing status subsystem", () => {
  it("shows service status as unavailable — never green — while the rest of the page works", async () => {
    serve({ ...OVERVIEW, service_status: null });
    show();
    await heading();
    expect(screen.getByText("Status temporarily unavailable")).toBeInTheDocument();
    expect(screen.getByText("Live service status is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.queryByText("All systems operational")).toBeNull();
    expect(screen.queryByText("Operational")).toBeNull();
    expect(screen.getByText("Maintenance updates will appear here when available.")).toBeInTheDocument();
  });

  it("shows maintenance as unavailable when only that part failed", async () => {
    serve({ ...OVERVIEW, maintenance: null });
    show();
    await heading();
    expect(screen.getByText("Scheduled maintenance information is temporarily unavailable.")).toBeInTheDocument();
    expect(screen.getByText("All systems operational")).toBeInTheDocument();
  });
});

describe("a refresh that fails after a good answer", () => {
  it("marks the last known state as stale, then clears the mark when a refresh succeeds", async () => {
    const user = userEvent.setup();
    show();
    await heading();
    expect(screen.getByText("All systems operational")).toBeInTheDocument();
    expect(screen.queryByTestId("status-stale")).toBeNull();

    vi.mocked(api.get).mockRejectedValue(new Error("network down"));
    await user.click(screen.getByRole("button", { name: /refresh/i }));
    const banner = await screen.findByTestId("status-stale");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveTextContent(/Status information is temporarily unavailable\. Showing the last known state/);
    // The old green answer is kept for reference but no longer headlined as current.
    expect(screen.queryByText("All systems operational")).toBeNull();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("We can't refresh service status right now.");
    expect(screen.getByText("Status temporarily unavailable")).toBeInTheDocument();
    expect(rowFor("Live streaming")).toBeTruthy();
    expect(screen.getByText(/retrying every 60 seconds/)).toBeInTheDocument();

    serve(OVERVIEW);
    await user.click(screen.getByRole("button", { name: /refresh/i }));
    await waitFor(() => expect(screen.queryByTestId("status-stale")).toBeNull());
    expect(screen.getByText("All systems operational")).toBeInTheDocument();
    expect(screen.getByText(/updated every 60 seconds/)).toBeInTheDocument();
  });
});
