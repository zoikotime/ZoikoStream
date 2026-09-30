// Organization Settings -> Notifications, the CATALOG-driven panel — which is the panel
// production actually renders, because GET /organization/notifications/catalog exists.
//
// ── THE BUG ─────────────────────────────────────────────────────────────────────────────
// The panel indexes the form state with the key the SERVER puts on each catalog row
// ("event_scheduled"), but the form state was built with camelCase names
// ("eventScheduled"). So `settings.notifs[it.key]` was undefined on every row:
//
//   * each switch drew itself OFF regardless of what was stored, so a saved preference
//     looked like it had reverted on reload;
//   * a toggle wrote a key nothing else read, leaving the camelCase field untouched;
//   * the PATCH body is the diff of the camelCase fields, so it came out EMPTY and the save
//     handler returned before sending anything. Nothing ever reached the database.
//
// SettingsSave.test.jsx did not catch it: its api.get rejects any URL outside PAYLOADS, so
// the catalog request failed there and it only ever exercised the no-catalog FALLBACK list,
// which was keyed camelCase and therefore worked. These tests supply a catalog, so they run
// the branch real deployments run.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";

// `recording_ready` is deliberately stored ON: a switch that ignores stored state renders it
// off, which is the reverting-on-refresh symptom made visible.
const STORED = {
  event_scheduled: true, event_starting: false, recording_ready: true, weekly_summary: false,
  billing: true, mentions: false, member_joined: false, security_alerts: true,
};

const row = (key, label, over = {}) => ({
  key, label, description: `About ${label}.`, message_class: "B",
  mandatory: false, configurable: true, available: true,
  scope: "organization", channel: "email", value: STORED[key],
  ...over,
});

// Shaped like services/notifications.CATALOG: three configurable, one mandatory, one with no
// send path — so the grouping and the non-switch rows are covered too.
const CATALOG = [
  row("event_scheduled", "Event scheduled"),
  row("member_joined", "Member joined"),
  row("recording_ready", "Recording ready"),
  row("security_alerts", "Security alerts", { mandatory: true, configurable: false }),
  row("weekly_summary", "Weekly summary", { configurable: false, available: false }),
];

const PAYLOADS = {
  "/organization/profile": { name: "Acme", slug: "acme", website: "", support_email: "", industry: null, company_size: null, description: "" },
  "/organization/security": { require_2fa: false, enforce_sso: false, min_password_length: 8, session_timeout: "8 hours", allowed_domains: "" },
  "/organization/notifications": STORED,
  "/organization/notifications/catalog": CATALOG,
  "/organization/domain": { domain: "events.acme.com", domain_verified: true },
  "/organization/branding": { primary_color: "violet", logo_url: null },
};

const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/settings?tab=notifications"]}>
        <Settings />
      </MemoryRouter>
    </ThemeProvider>
  );

const patches = () => vi.mocked(api.patch).mock.calls;
const saveButton = () => screen.getByRole("button", { name: "Save notifications" });
// The switches, in catalog order: event_scheduled, member_joined, recording_ready.
const switches = () => screen.findAllByRole("switch");

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) =>
    url in PAYLOADS ? Promise.resolve({ data: PAYLOADS[url] }) : Promise.reject(new Error("404")));
  vi.mocked(api.patch).mockImplementation((url, body) =>
    Promise.resolve({ data: { ...PAYLOADS[url], ...body } }));
});

describe("the catalog panel reads the stored preferences", () => {
  it("renders one switch per configurable row, not for mandatory or unavailable ones", async () => {
    open();
    expect(await switches()).toHaveLength(3);
    // "Always on" reads twice: once as the group heading, once as the badge that stands in
    // for the switch on a mandatory row. The unavailable row's badge says "Unavailable",
    // which its group heading ("Not available yet") does not repeat.
    expect(screen.getAllByText("Always on")).toHaveLength(2);
    expect(screen.getByText("Unavailable")).toBeInTheDocument();
  });

  it("each switch shows the value the server stored, not a default", async () => {
    open();
    const [scheduled, joined, recording] = await switches();
    // The regression: all three read `undefined` and rendered off while the keys mismatched.
    expect(scheduled).toBeChecked();      // stored true
    expect(joined).not.toBeChecked();     // stored false
    expect(recording).toBeChecked();      // stored true
  });

  it("opens with nothing to save, because the form matches what was loaded", async () => {
    open();
    await switches();
    expect(saveButton()).toBeDisabled();
  });
});

describe("a toggle reaches the database", () => {
  it("enables Save, which was dead while the toggle wrote a key the payload ignored", async () => {
    open();
    const [scheduled] = await switches();
    fireEvent.click(scheduled);
    await waitFor(() => expect(saveButton()).toBeEnabled());
  });

  it("sends a PATCH holding only that preference, under the API's own key name", async () => {
    open();
    const [, joined] = await switches();
    fireEvent.click(joined);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual(["/organization/notifications", { member_joined: true }]);
  });

  it("turns a stored preference off as well as on", async () => {
    open();
    const [, , recording] = await switches();
    fireEvent.click(recording);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual(["/organization/notifications", { recording_ready: false }]);
  });

  it("carries every toggled preference in one request", async () => {
    open();
    const [scheduled, joined] = await switches();
    fireEvent.click(scheduled);
    fireEvent.click(joined);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual([
      "/organization/notifications", { event_scheduled: false, member_joined: true },
    ]);
  });

  it("never sends a key the catalog did not offer as a switch", async () => {
    open();
    const [scheduled] = await switches();
    fireEvent.click(scheduled);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(Object.keys(patches()[0][1])).toEqual(["event_scheduled"]);
  });
});

describe("the panel settles after saving", () => {
  it("clears the unsaved state instead of staying dirty forever", async () => {
    open();
    const [scheduled] = await switches();
    fireEvent.click(scheduled);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(1));
    // Both symptoms at once: the button leaves "Saving…" AND the section stops being dirty,
    // which it could not do while the server's answer was re-keyed into fields nobody read.
    await waitFor(() => expect(saveButton()).toBeDisabled());
    expect(saveButton()).toHaveTextContent("Save");
  });

  it("keeps the toggled value on screen after the server confirms it", async () => {
    open();
    const [scheduled] = await switches();
    fireEvent.click(scheduled);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(1));
    await waitFor(() => expect(saveButton()).toBeDisabled());
    const [after] = await switches();
    expect(after).not.toBeChecked();
  });

  it("a second save sends only what changed since the first", async () => {
    open();
    const [scheduled, joined] = await switches();
    fireEvent.click(scheduled);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(1));
    await waitFor(() => expect(saveButton()).toBeDisabled());
    fireEvent.click(joined);
    fireEvent.click(saveButton());
    await waitFor(() => expect(patches()).toHaveLength(2));
    expect(patches()[1]).toEqual(["/organization/notifications", { member_joined: true }]);
  });
});
