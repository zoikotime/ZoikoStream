import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));

import api from "../api";
import OperatorGuide from "./OperatorGuide";
import { consoleRoute } from "./operatorGuideApi";
import { eventIdFrom, isConsolePath } from "./eligibility";
import { safeHref } from "./parts/links";

const EVENT = { id: "11111111-2222-3333-4444-555555555555", title: "Town Hall", status: "published", start_time: null };
const CONTEXT = { organization: { id: "o1", name: "Acme" }, workspace: "Events", route: "/organization/events", event: EVENT, environment: "production", role: "org_admin" };
const BOOT = {
  user: { name: "Dana Ops", role: "org_admin", is_org_admin: true },
  context: CONTEXT,
  capabilities: { docs_qa: true, actions: true },
  attention: {
    count: 1,
    items: [{ id: "readiness:e1", kind: "readiness", severity: "blocker", title: "Town Hall: readiness blocked", detail: "Starts soon", href: "/organization/events/e1", ask: "Is this event ready?", event: { id: EVENT.id, title: "Town Hall" } }],
  },
  suggestions: ["Is this event ready?", "Who can access this event?"],
  recent: [],
};

const guideMessage = (payload, id = "m1") => ({
  conversation_id: "c1",
  user_message_id: "u1",
  context: CONTEXT,
  message: { id, role: "guide", text: payload.answer, payload: { blocks: [], sources: [], actions: [], suggestions: [], mode: "standard", explanation: null, ...payload } },
});

const READINESS = guideMessage({
  answer: "Not yet. 1 gate(s) are not met: Host assigned.",
  explanation: "No host is assigned yet.",
  blocks: [{
    type: "readiness", title: "Readiness · Town Hall", overall: { state: "blocked", label: "BLOCKED" },
    rows: [{ label: "Host assigned", state: "blocked", detail: "Not met · required for this event" }, { label: "Title set", state: "ready" }],
    note: "Guide explains readiness; it cannot change it.", source_ids: ["readiness:e1"],
  }],
  sources: [{ id: "readiness:e1", label: "Readiness gates", class: "authoritative", owner: "Readiness service", age_text: "2 sec ago", updated_at: "2026-10-09T10:00:00Z", stale: false }],
  actions: [{ type: "navigate", label: "Review event setup", href: "/organization/events/e1" }, { type: "navigate", label: "Phish", href: "https://evil.example" }],
  suggestions: ["Who can access this event?"],
});

const PROPOSAL = {
  type: "proposal", id: "p1", action_id: "event.set_feature", risk: "R3", status: "proposed",
  title: "Turn off chat", target: { type: "event", id: EVENT.id, label: "Town Hall" },
  change: { field: "chat_enabled", label: "Chat", from: "On", to: "Off" },
  impact: ["Chat will be unavailable to viewers."], confirm_label: "Confirm: Turn off chat", cancel_label: "Cancel",
};

function setup({ boot = BOOT, bootError = null } = {}) {
  api.get.mockImplementation((url) => {
    if (url.endsWith("/bootstrap")) return bootError ? Promise.reject(bootError) : Promise.resolve({ data: boot });
    if (url.endsWith("/context/events")) return Promise.resolve({ data: { items: [EVENT] } });
    if (url.endsWith("/attention")) return Promise.resolve({ data: boot.attention });
    return Promise.resolve({ data: {} });
  });
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/organization/events"]}>
      <OperatorGuide route="/organization/events" routeEventId={EVENT.id} />
    </MemoryRouter>,
  );
  return user;
}

async function openPanel(user) {
  await user.click(await screen.findByRole("button", { name: /open guide/i }));
  return screen.findByRole("dialog", { name: "Guide" });
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("eligibility and links", () => {
  it("shows only on console routes and sends only the event query parameter", () => {
    expect(isConsolePath("/organization/events")).toBe(true);
    expect(isConsolePath("/host/dashboard")).toBe(true);
    expect(isConsolePath("/admin")).toBe(true);
    expect(isConsolePath("/events/abc/watch")).toBe(false);
    expect(isConsolePath("/")).toBe(false);
    expect(isConsolePath("/organizationx")).toBe(false);
    expect(consoleRoute("/host/dashboard", "?event=e1&token=secret")).toBe("/host/dashboard?event=e1");
    expect(eventIdFrom(`/organization/events/${EVENT.id}`, "")).toBe(EVENT.id);
  });

  it("only navigates to relative console paths", () => {
    expect(safeHref("/organization/events")).toBe("/organization/events");
    expect(safeHref("https://evil.example")).toBeNull();
    expect(safeHref("//evil.example")).toBeNull();
    expect(safeHref("/\\evil.example")).toBeNull();
  });
});

describe("launcher and panel", () => {
  it("announces attention without animation and opens with the keyboard shortcut", async () => {
    const user = setup();
    const launcher = await screen.findByRole("button", { name: /1 item need attention/i });
    expect(launcher).toHaveAttribute("aria-keyshortcuts", "Control+. Meta+.");
    await user.keyboard("{Control>}.{/Control}");
    const panel = await screen.findByRole("dialog", { name: "Guide" });
    expect(panel).toHaveAttribute("aria-modal", "false");
    expect(within(panel).getByText("Acme · Events")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(launcher).toHaveFocus();
  });

  it("home lists what needs attention and suggestions", async () => {
    const user = setup();
    const panel = await openPanel(user);
    expect(within(panel).getByText("What needs attention?")).toBeInTheDocument();
    expect(within(panel).getByText("Town Hall: readiness blocked")).toBeInTheDocument();
    expect(within(panel).getByText("blocker:", { exact: false })).toHaveClass("og-sr");
  });

  it("renders nothing for a role Guide does not serve", async () => {
    setup({ bootError: { response: { status: 403, data: { code: "OPGUIDE_FORBIDDEN", message: "no" } } } });
    await waitFor(() => expect(api.get).toHaveBeenCalled());
    expect(screen.queryByRole("button", { name: /guide/i })).not.toBeInTheDocument();
  });

  it("shows the unavailable wording when Guide is off", async () => {
    const user = setup({ bootError: { response: { status: 503, data: { code: "OPGUIDE_UNAVAILABLE", message: "Guide is temporarily unavailable. Core ZoikoStream controls remain available." } } } });
    const panel = await openPanel(user);
    expect(within(panel).getByRole("alert")).toHaveTextContent("Core ZoikoStream controls remain available.");
  });
});

describe("answers", () => {
  it("shows direct answer, state as text, why, safe next actions and sources", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: READINESS });
    const panel = await openPanel(user);
    await user.type(within(panel).getByLabelText("Ask Guide"), "Is this event ready?{Enter}");
    expect(await within(panel).findByText(/^Not yet\./, { selector: ".og-answer" })).toBeInTheDocument();
    // …and is announced once to screen readers.
    expect(within(panel).getByText(/^Not yet\./, { selector: "[aria-live]" })).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith("/operator-guide/messages", expect.objectContaining({ text: "Is this event ready?", event_id: EVENT.id }));
    const card = within(panel).getByRole("region", { name: "Readiness · Town Hall" });
    expect(within(card).getByText("BLOCKED")).toBeInTheDocument();
    expect(within(card).getByText("Blocked:", { exact: false })).toBeInTheDocument(); // state is text, not colour
    expect(within(panel).getByText("No host is assigned yet.")).toBeInTheDocument();
    expect(within(panel).getByRole("button", { name: /review event setup/i })).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: /phish/i })).not.toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: /sources \(1\)/i }));
    expect(within(panel).getByText(/Platform state · Readiness service/)).toBeInTheDocument();
  });

  it("live mode is terse and labelled", async () => {
    const user = setup({ boot: { ...BOOT, context: { ...CONTEXT, event: { ...EVENT, status: "live" } } } });
    api.post.mockResolvedValueOnce({ data: guideMessage({ answer: "No critical issue detected.", mode: "live", suggestions: ["ignored"] }) });
    const panel = await openPanel(user);
    expect(within(panel).getByText(/Live Mode — brief answers only/)).toBeInTheDocument();
    await user.type(within(panel).getByLabelText("Ask Guide"), "how is it going{Enter}");
    await within(panel).findByText("No critical issue detected.", { selector: ".og-answer" });
    expect(within(panel).queryByRole("button", { name: "ignored" })).not.toBeInTheDocument();
  });
});

describe("governed actions", () => {
  it("confirms with an explicit label and an idempotency key, then shows the receipt", async () => {
    const user = setup();
    api.post
      .mockResolvedValueOnce({ data: guideMessage({ answer: "Review this change. Nothing happens until you confirm.", blocks: [PROPOSAL] }) })
      .mockResolvedValueOnce({ data: { ...PROPOSAL, status: "succeeded", receipt: { message: "Chat changed from On to Off.", correlation_id: "cor_1", verified: true } } });
    const panel = await openPanel(user);
    await user.type(within(panel).getByLabelText("Ask Guide"), "turn off chat{Enter}");
    const confirm = await within(panel).findByRole("button", { name: "Confirm: Turn off chat" });
    expect(within(panel).queryByRole("button", { name: /^(ok|yes)$/i })).not.toBeInTheDocument();
    expect(within(panel).getByText("Nothing changes until you confirm.")).toBeInTheDocument();
    await user.click(confirm);
    const [url, body, config] = api.post.mock.calls[1];
    expect(url).toBe("/operator-guide/proposals/p1/confirm");
    expect(body).toBeNull();
    expect(config.headers["Idempotency-Key"]).toMatch(/^og-/);
    expect(await within(panel).findByText("Chat changed from On to Off.")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "Confirm: Turn off chat" })).not.toBeInTheDocument();
  });

  it("drafts are labelled and applying one goes through a structured proposal", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: guideMessage({
      answer: "Here is a draft.",
      blocks: [{ type: "draft", kind: "description", subject: null, body: "Join us.", label: "Draft — not sent or saved", apply: { label: "Use as event description", action_id: "event.update_description", params: { text: "Join us." } } }],
    }) });
    api.post.mockResolvedValueOnce({ data: guideMessage({ answer: "Review this change.", blocks: [{ ...PROPOSAL, id: "p2", confirm_label: "Confirm: Update description" }] }, "m2") });
    const panel = await openPanel(user);
    await user.type(within(panel).getByLabelText("Ask Guide"), "write a description{Enter}");
    expect(await within(panel).findByText("Draft — not sent or saved")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Use as event description" }));
    expect(api.post.mock.calls[1][1]).toEqual(expect.objectContaining({ action: { action_id: "event.update_description", params: { text: "Join us." } } }));
    expect(await within(panel).findByRole("button", { name: "Confirm: Update description" })).toBeInTheDocument();
  });

  it("shows the server's wording when a confirmation is refused", async () => {
    const user = setup();
    api.post
      .mockResolvedValueOnce({ data: guideMessage({ answer: "Review this change.", blocks: [PROPOSAL] }) })
      .mockRejectedValueOnce({ response: { status: 409, data: { code: "OPGUIDE_CONFLICT", message: "This proposal expired. Ask again and I will check the current state first." } } });
    const panel = await openPanel(user);
    await user.type(within(panel).getByLabelText("Ask Guide"), "turn off chat{Enter}");
    await user.click(await within(panel).findByRole("button", { name: "Confirm: Turn off chat" }));
    expect(await within(panel).findByText(/This proposal expired/)).toBeInTheDocument();
  });
});
