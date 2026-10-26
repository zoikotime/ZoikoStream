// Developers (ORG-08): the readiness steps and KPI row read the organization's own key
// inventory and webhook endpoints from GET /organization/developer. A refused or failed read
// says "Unavailable" — never a zero that would read as "nothing set up".
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import DeveloperPlatform from "./DeveloperPlatform";

const DEVELOPER = {
  api_keys: [{ id: "k1", label: "CI", prefix: "zk_", revoked: false }],
  webhooks: [],
  key_summary: { total: 2, active: 1, expiring_30d: 1, expiring_7d: 0, expired: 0, revoked: 1, non_expiring: 0 },
};

const serve = (developer) =>
  vi.mocked(api.get).mockImplementation((url) => {
    if (url === "/organization/developer") {
      return developer instanceof Error ? Promise.reject(developer) : Promise.resolve({ data: developer });
    }
    return Promise.resolve({ data: {} });
  });

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/developers"]}>
        <DeveloperPlatform />
      </MemoryRouter>
    </ThemeProvider>
  );

const readiness = () => within(screen.getByRole("region", { name: "Integration readiness" }));

beforeEach(() => vi.clearAllMocks());

describe("integration readiness", () => {
  it("marks a step done only when the organization really has it", async () => {
    serve(DEVELOPER);
    show();
    expect(await screen.findByText("Active of 2 issued · 1 expiring in 30 days")).toBeInTheDocument();
    expect(readiness().getByText("Confirmed")).toBeInTheDocument();
    const credential = readiness().getByText("Create a credential").closest("li");
    expect(within(credential).getByText("Done")).toBeInTheDocument();
    const webhook = readiness().getByText("Register a signed webhook").closest("li");
    expect(within(webhook).getByText("To do")).toBeInTheDocument();
    expect(screen.getByText("No endpoints registered")).toBeInTheDocument();
    // No sandbox and no request telemetry exist; they say so instead of showing a figure.
    expect(within(screen.getByRole("group", { name: "Sandbox access" })).getByText("Not available")).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "Request telemetry" })).getByText("Not connected")).toBeInTheDocument();
  });

  it("says unavailable, not zero, when the developer settings cannot be read", async () => {
    serve(new Error("403"));
    show();
    expect(await readiness().findByText("Unavailable")).toBeInTheDocument();
    expect(screen.getAllByText("Unavailable right now")).toHaveLength(2);
    expect(readiness().queryByText("To do")).toBeNull();
  });
});

// `key_summary` is the server's own count, and an API without it (an older server, or one
// deployed without the key-lifecycle change) must still give a finished tile. The caption used
// to branch on the summary alone, so its absence read "Loading…" for as long as the page was open.
describe("the API credentials tile without the server's key_summary", () => {
  const PAST = "2026-01-01T00:00:00Z";
  const FUTURE = "2099-01-01T00:00:00Z";
  const tileOf = (caption) => screen.getByText(caption).closest("a");

  it("finishes from the key list, counting only keys that are neither revoked nor expired", async () => {
    serve({
      api_keys: [
        { id: "k1", label: "CI", prefix: "zk_", revoked: false, expires_at: null },
        { id: "k2", label: "Old", prefix: "zk_", revoked: true, expires_at: null },
        { id: "k3", label: "Lapsed", prefix: "zk_", revoked: false, expires_at: PAST },
        { id: "k4", label: "Batch", prefix: "zk_", revoked: false, expires_at: FUTURE },
      ],
      webhooks: [],
    });
    show();
    expect(await screen.findByText("Active of 4 issued")).toBeInTheDocument();
    expect(within(tileOf("Active of 4 issued")).getByText("2")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).toBeNull();
    const credential = readiness().getByText("Create a credential").closest("li");
    expect(within(credential).getByText("Done")).toBeInTheDocument();
  });

  it("reads an empty key list as none issued — a counted zero, not a load in progress", async () => {
    serve({ api_keys: [], webhooks: [] });
    show();
    expect(await screen.findByText("No credentials issued yet")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).toBeNull();
    const credential = readiness().getByText("Create a credential").closest("li");
    expect(within(credential).getByText("To do")).toBeInTheDocument();
  });

  it("says the inventory was not reported when the response carries no key list at all", async () => {
    serve({ webhooks: [] });
    show();
    expect(await screen.findByText("Key inventory not reported")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).toBeNull();
    expect(within(tileOf("Key inventory not reported")).getByText("—")).toBeInTheDocument();
  });

  it("says Loading… only while the request is still outstanding", async () => {
    vi.mocked(api.get).mockImplementation((url) =>
      url === "/organization/developer" ? new Promise(() => {}) : Promise.resolve({ data: {} })
    );
    show();
    // Other panels say "Loading…" too while their reads are outstanding; this is the tile's.
    const tile = (await screen.findAllByText("Loading…"))
      .map((el) => el.closest("a"))
      .find((a) => a?.getAttribute("href") === "/organization/credentials");
    expect(tile).toBeTruthy();
    expect(within(tile).getByText("API credentials")).toBeInTheDocument();
  });
});

describe("API requests & webhook deliveries", () => {
  const HOOKS = [{ id: "w1", label: "CRM", url: "https://crm.example/hook", enabled: true, events: [] }];
  const DELIVERIES = [
    { id: "d1", event_type: "event.live", status: "delivered", attempt_count: 1, last_response_code: 200,
      last_error: null, delivered_at: "2026-10-21T10:00:00Z", created_at: "2026-10-21T10:00:00Z" },
    { id: "d2", event_type: "event.ended", status: "failed", attempt_count: 3, last_response_code: 500,
      last_error: "timeout", delivered_at: null, created_at: "2026-10-21T11:00:00Z" },
  ];
  const serveHooks = () =>
    vi.mocked(api.get).mockImplementation((url) => {
      if (url === "/organization/developer") return Promise.resolve({ data: { ...DEVELOPER, webhooks: HOOKS } });
      if (url === "/organization/developer/webhooks/w1/deliveries") return Promise.resolve({ data: DELIVERIES });
      return Promise.resolve({ data: {} });
    });
  const panel = () => within(screen.getByRole("region", { name: "API requests & webhook deliveries" }));

  it("says request telemetry is not connected instead of drawing nothing as something", async () => {
    serveHooks();
    show();
    expect(await panel().findByText("Request telemetry is not connected")).toBeInTheDocument();
    expect(panel().getByRole("tab", { name: "Request activity" })).toHaveAttribute("aria-selected", "true");
  });

  it("lists the organization's real deliveries, newest first, and only failures under Errors", async () => {
    const user = userEvent.setup();
    serveHooks();
    show();
    await user.click(await panel().findByRole("tab", { name: "Webhook deliveries" }));
    const items = await panel().findAllByRole("listitem");
    expect(items.map((li) => li.querySelector("p").textContent)).toEqual(["event.ended", "event.live"]);
    expect(within(items[0]).getByText("failed · 500")).toBeInTheDocument();
    await user.click(panel().getByRole("tab", { name: "Errors" }));
    expect(panel().getAllByRole("listitem").map((li) => li.querySelector("p").textContent)).toEqual(["event.ended"]);
  });

  it("has no deliveries to show when no endpoint is registered", async () => {
    const user = userEvent.setup();
    serve(DEVELOPER);
    show();
    await user.click(await panel().findByRole("tab", { name: "Webhook deliveries" }));
    expect(await panel().findByText("No webhook endpoints are registered, so there are no deliveries.")).toBeInTheDocument();
  });
});
