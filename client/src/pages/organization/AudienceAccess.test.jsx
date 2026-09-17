// Audience Access renders counted rows, or says it could not count them.
//
// Every figure on this page was either real-but-unset or not wired at all. "Registrations"
// and every event's "Registered" cell printed an em dash for an organization that really did
// have registrations, because `registered_count` existed nowhere in the backend — the page
// read a field nothing produced. The other three KPIs were genuinely real and genuinely zero
// for that org, which is a different thing and is pinned here so nobody "fixes" them.
//
// The distinction these tests exist to protect: 0 is a measurement, — is the absence of one.
// An API failure must never become a zero.
import { configure, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

configure({ asyncUtilTimeout: 8000 });
vi.setConfig({ testTimeout: 30000 });

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));

const downloadCsv = vi.fn();
vi.mock("../../utils/export", () => ({ downloadCsv: (...a) => downloadCsv(...a) }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import AudienceAccess from "./AudienceAccess";

const EVENTS = [
  { id: "e1", title: "Full House", visibility: "public", registration_required: true,
    registration_limit: 10, registered_count: 10, start_time: "2026-09-16T12:11:00Z" },
  { id: "e2", title: "Nearly There", visibility: "private", registration_required: true,
    registration_limit: 10, registered_count: 9, start_time: "2026-09-15T12:11:00Z" },
  { id: "e3", title: "Open Door", visibility: "public", registration_required: false,
    registration_limit: null, registered_count: 0, start_time: "2026-09-14T12:11:00Z" },
];

const SUMMARY = {
  range: "30d", events: 3, registration_required: 2,
  registrations: 19, at_capacity: 1, events_with_capacity: 2,
};

const ATTENDANCE = {
  range: "30d", unique_attendees: 4, returning: 1, avg_watch_minutes: 2.5,
  show_rate: null, unique_attendees_estimated: true, avg_watch_minutes_estimated: true,
  show_rate_basis: "private_invited_events_only",
};

let failSummary = false;
let eventsTotal = 3;

const serve = () => {
  vi.mocked(api.get).mockImplementation((url, cfg) => {
    if (url === "/organization/audience-summary") {
      return failSummary
        ? Promise.reject(new Error("boom"))
        : Promise.resolve({ data: { ...SUMMARY, range: cfg?.params?.range } });
    }
    if (url === "/organization/audience-attendance") return Promise.resolve({ data: ATTENDANCE });
    if (url === "/events") {
      const q = cfg?.params?.q;
      const items = q
        ? EVENTS.filter((e) => e.title.toLowerCase().includes(q.toLowerCase()))
        : EVENTS;
      return Promise.resolve({ data: { items, total: q ? items.length : eventsTotal } });
    }
    return Promise.resolve({ data: {} });
  });
};

// The page navigates on row click, so it needs a Router in scope.
const renderPage = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <AudienceAccess />
      </MemoryRouter>
    </ThemeProvider>
  );

const listParams = () => {
  const calls = vi.mocked(api.get).mock.calls.filter(([u]) => u === "/events");
  return calls[calls.length - 1]?.[1]?.params;
};
const summaryParams = () => {
  const calls = vi.mocked(api.get).mock.calls.filter(([u]) => u === "/organization/audience-summary");
  return calls[calls.length - 1]?.[1]?.params;
};
const rowFor = (title) => screen.getByText(title).closest("tr");
const card = (label) => screen.getByText(label).closest("div");

beforeEach(() => {
  vi.clearAllMocks();
  downloadCsv.mockClear();
  failSummary = false;
  eventsTotal = 3;
  serve();
});

// ── the KPI row ────────────────────────────────────────────────────────────────────────

describe("KPI row", () => {
  it("reads the dataset-wide summary rather than summing the page", async () => {
    renderPage();
    await screen.findByText("Full House");
    // The page holds 3 rows; the cards must come from the summary endpoint regardless.
    expect(summaryParams()).toBeTruthy();
    // Asserted as "a figure, not an em dash" rather than as the literal 19: StatCard renders
    // numbers through <Counter>, which tweens from 0 and only starts once
    // IntersectionObserver reports it in view — jsdom never fires that, so the digits on
    // screen are an artefact of the environment. The arithmetic itself is pinned in
    // server/test_org_audience_metrics.py against real rows.
    expect(within(card("Registrations")).queryByText("—")).not.toBeInTheDocument();
    expect(vi.mocked(api.get).mock.calls.some(([u]) => u === "/organization/audience-summary")).toBe(true);
  });

  it("shows a real registration total, not an em dash", async () => {
    renderPage();
    await screen.findByText("Full House");
    expect(within(card("Registrations")).queryByText("—")).not.toBeInTheDocument();
  });

  it("shows counted zeros as 0", async () => {
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/organization/audience-summary"
        ? Promise.resolve({ data: { ...SUMMARY, registrations: 0, at_capacity: 0,
                                    registration_required: 0, events: 0, events_with_capacity: 0 } })
        : url === "/events"
          ? Promise.resolve({ data: { items: [], total: 0 } })
          : Promise.resolve({ data: ATTENDANCE }));
    renderPage();

    // A real zero is a figure. It must not be rendered as "unavailable".
    await waitFor(() => expect(within(card("Registrations")).getByText("0")).toBeInTheDocument());
    expect(within(card("Registrations")).queryByText("—")).not.toBeInTheDocument();
  });

  it("shows an em dash — never a zero — when the summary call fails", async () => {
    failSummary = true;
    renderPage();
    await screen.findByText("Full House");

    expect(await screen.findByText(/couldn't load the audience totals/i)).toBeInTheDocument();
    expect(within(card("Registrations")).getByText("—")).toBeInTheDocument();
    expect(within(card("Registrations")).queryByText("0")).not.toBeInTheDocument();
  });

  it("keeps the table working when only the summary failed", async () => {
    failSummary = true;
    renderPage();
    expect(await screen.findByText("Full House")).toBeInTheDocument();
  });

  it("explains an at-capacity zero when nothing has a capacity", async () => {
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/organization/audience-summary"
        ? Promise.resolve({ data: { ...SUMMARY, at_capacity: 0, events_with_capacity: 0 } })
        : url === "/events"
          ? Promise.resolve({ data: { items: EVENTS, total: 3 } })
          : Promise.resolve({ data: ATTENDANCE }));
    renderPage();
    // 0 of 0 capped events reads differently from 0 of 12.
    expect(await screen.findByText(/no event has a capacity set/i)).toBeInTheDocument();
  });
});

// ── the table ──────────────────────────────────────────────────────────────────────────

describe("per-event figures", () => {
  it("shows the real registered count for each event", async () => {
    renderPage();
    await screen.findByText("Full House");

    // The Registered cell is split across elements ("10" + " / 10"), so getByText cannot see
    // it as one string — assert on the row's text instead.
    expect(rowFor("Full House").textContent).toContain("10");
    expect(within(rowFor("Nearly There")).getByText(/^9$/)).toBeInTheDocument();
    expect(within(rowFor("Open Door")).getByText(/^0$/)).toBeInTheDocument();
  });

  it("renders a counted zero as 0, not an em dash", async () => {
    renderPage();
    await screen.findByText("Open Door");
    const row = rowFor("Open Door");
    expect(within(row).getByText("0")).toBeInTheDocument();
  });

  it("renders an em dash only when the response omits the count", async () => {
    // An older API build that does not send registered_count at all.
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/events"
        ? Promise.resolve({ data: { items: [{ ...EVENTS[0], registered_count: undefined }], total: 1 } })
        : url === "/organization/audience-summary"
          ? Promise.resolve({ data: SUMMARY })
          : Promise.resolve({ data: ATTENDANCE }));
    renderPage();
    await screen.findByText("Full House");
    expect(within(rowFor("Full House")).getByText("—")).toBeInTheDocument();
  });

  it("names the real capacity, and says Uncapped only when none is configured", async () => {
    renderPage();
    await screen.findByText("Full House");

    expect(within(rowFor("Full House")).getByText(/of 10/)).toBeInTheDocument();
    // registration_limit is null on this one — genuinely uncapped.
    expect(within(rowFor("Open Door")).getByText(/uncapped/i)).toBeInTheDocument();
    expect(within(rowFor("Nearly There")).queryByText(/uncapped/i)).not.toBeInTheDocument();
  });

  it("takes the audience type from the event's own visibility", async () => {
    renderPage();
    await screen.findByText("Full House");
    expect(within(rowFor("Full House")).getByText(/public/i)).toBeInTheDocument();
    // Not hardcoded to Public — this one really is private.
    expect(within(rowFor("Nearly There")).getByText(/private/i)).toBeInTheDocument();
  });

  it("shows the real registration state per event", async () => {
    renderPage();
    await screen.findByText("Full House");
    expect(within(rowFor("Full House")).getByText(/required/i)).toBeInTheDocument();
    // Exact match: the event is literally called "Open Door", so a loose /open/i matches the
    // title cell too.
    expect(within(rowFor("Open Door")).getByText("Open")).toBeInTheDocument();
  });
});

// ── range, search, pagination ──────────────────────────────────────────────────────────

describe("the date range is real", () => {
  it("sends the range to the summary and a matching window to the events list", async () => {
    renderPage();
    await screen.findByText("Full House");
    expect(summaryParams().range).toBe("30d");
    expect(listParams().date_from).toBeTruthy();
  });

  it.each(["7 days", "90 days"])("refetches everything when %s is chosen", async (label) => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Full House");

    await u.click(screen.getByRole("button", { name: label }));

    const key = label === "7 days" ? "7d" : "90d";
    await waitFor(() => expect(summaryParams().range).toBe(key));
    // The table window moves with it — the buttons are not decoration over a fixed list.
    await waitFor(() => expect(listParams().date_from).toBeTruthy());
  });

  it("moves the window: a longer range asks for an earlier date_from", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Full House");
    const thirty = new Date(listParams().date_from).getTime();

    await u.click(screen.getByRole("button", { name: "90 days" }));
    await waitFor(() => expect(summaryParams().range).toBe("90d"));
    expect(new Date(listParams().date_from).getTime()).toBeLessThan(thirty);
  });
});

describe("search is server-side", () => {
  it("sends q to the API rather than filtering the fetched page", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Full House");

    await u.type(screen.getByLabelText(/search events/i), "Nearly");
    await waitFor(() => expect(listParams().q).toBe("Nearly"));
  });

  it("returns to page 1 when a search narrows the set", async () => {
    const u = userEvent.setup();
    eventsTotal = 120;
    renderPage();
    await screen.findByText("Full House");

    await u.click(screen.getByRole("button", { name: "2" }));
    await waitFor(() => expect(listParams().page).toBe(2));

    await u.type(screen.getByLabelText(/search events/i), "Nearly");
    await waitFor(() => expect(listParams().q).toBe("Nearly"));
    expect(listParams().page).toBe(1);
  });
});

describe("pagination is server-side", () => {
  it("asks for a page and a page size inside the API cap", async () => {
    renderPage();
    await screen.findByText("Full House");
    expect(listParams().page).toBe(1);
    expect(listParams().page_size).toBeLessThanOrEqual(100);
  });

  it("reports the dataset total and reaches later pages", async () => {
    const u = userEvent.setup();
    eventsTotal = 120;
    renderPage();
    await screen.findByText("Full House");

    expect(await screen.findByText(/of 120/)).toBeInTheDocument();
    await u.click(screen.getByRole("button", { name: "2" }));
    await waitFor(() => expect(listParams().page).toBe(2));
  });
});

// ── export, scoping, and the removed link ──────────────────────────────────────────────

describe("export", () => {
  it("exports the counted values, not placeholders", async () => {
    const u = userEvent.setup();
    renderPage();
    await screen.findByText("Full House");

    await u.click(screen.getByRole("button", { name: /export/i }));
    expect(downloadCsv).toHaveBeenCalled();

    const [, rows, cols] = downloadCsv.mock.calls[0];
    const registered = cols.find(([h]) => h === "Registered")[1];
    const capacity = cols.find(([h]) => h === "Capacity")[1];
    const full = rows.find((r) => r.title === "Full House");
    const open = rows.find((r) => r.title === "Open Door");

    expect(registered(full)).toBe(10);
    expect(registered(open)).toBe(0);          // a counted zero, exported as 0
    expect(capacity(full)).toBe(10);
    expect(capacity(open)).toBe("");           // genuinely uncapped — not invented
  });
});

describe("organization scoping", () => {
  it("never sends an org_id — the server derives it from the session", async () => {
    renderPage();
    await screen.findByText("Full House");
    for (const [, cfg] of vi.mocked(api.get).mock.calls) {
      expect(cfg?.params ?? {}).not.toHaveProperty("org_id");
    }
  });
});

describe("the Playback & Access entry points are gone", () => {
  it("no longer offers a Playback gates action", async () => {
    renderPage();
    await screen.findByText("Full House");
    expect(screen.queryByText(/playback gates/i)).not.toBeInTheDocument();
  });

  it("links nowhere into /organization/playback", async () => {
    const { container } = renderPage();
    await screen.findByText("Full House");
    const hrefs = [...container.querySelectorAll("a")].map((a) => a.getAttribute("href"));
    expect(hrefs.some((h) => h && h.includes("/organization/playback"))).toBe(false);
  });
});

describe("error handling", () => {
  it("shows a real error state when the events list fails", async () => {
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/events"
        ? Promise.reject(new Error("500"))
        : Promise.resolve({ data: url.includes("summary") ? SUMMARY : ATTENDANCE }));
    renderPage();
    expect(await screen.findByText(/couldn't load events/i)).toBeInTheDocument();
  });
});
