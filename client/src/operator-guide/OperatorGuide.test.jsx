import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";

vi.mock("../api", () => ({ default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
const auth = vi.hoisted(() => ({ user: { role: "org_admin" } }));
vi.mock("../auth/AuthContext", () => ({ useAuth: () => auth }));

import api from "../api";
import OperatorGuide from "./OperatorGuide";
import AskGuideButton from "./AskGuideButton";
import { askGuide } from "./askGuide";
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
  auth.user = { role: "org_admin" };
  window.__opguidePending = undefined;
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

const OTHER = { id: "99999999-8888-7777-6666-555555555555", title: "Board Meeting", status: "published", start_time: null };

async function ask(user, panel, text) {
  await user.type(within(panel).getByLabelText("Ask Guide"), `${text}{Enter}`);
}

describe("inline Ask Guide entry points (§1.1, §5)", () => {
  it("renders nothing for a role Guide does not serve", () => {
    auth.user = { role: "viewer" };
    render(<MemoryRouter><AskGuideButton question="Is this event ready?" eventId={EVENT.id} /></MemoryRouter>);
    expect(screen.queryByRole("button", { name: /ask guide/i })).not.toBeInTheDocument();
  });

  it("opens Guide and asks with the page's event attached", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: READINESS });
    render(<MemoryRouter><AskGuideButton question="Is this event ready?" eventId={OTHER.id} /></MemoryRouter>);
    await screen.findByRole("button", { name: /open guide/i });
    await user.click(screen.getByRole("button", { name: "Ask Guide" }));
    await screen.findByRole("dialog", { name: "Guide" });
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/operator-guide/messages",
      expect.objectContaining({ text: "Is this event ready?", event_id: OTHER.id })));
  });

  it("a click before Guide has loaded is picked up when it mounts", async () => {
    askGuide({ question: "Who can access this event?", eventId: OTHER.id, route: "/organization/events" });
    api.post.mockResolvedValueOnce({ data: READINESS });
    setup();
    expect(await screen.findByRole("dialog", { name: "Guide" })).toBeInTheDocument();
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/operator-guide/messages",
      expect.objectContaining({ text: "Who can access this event?", event_id: OTHER.id })));
    expect(window.__opguidePending).toBeUndefined();
  });

  it("a stale parked request is ignored", async () => {
    window.__opguidePending = { question: "old", eventId: null, at: Date.now() - 60_000 };
    setup();
    await screen.findByRole("button", { name: /open guide/i });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("context changes are visible (§4.2)", () => {
  it("shows the server's context notice before the question it applies to", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: READINESS })
      .mockResolvedValueOnce({ data: { ...guideMessage({ answer: "Yes." }, "m2"), context: { ...CONTEXT, event: OTHER },
        context_notice: { id: "n1", role: "guide", intent: "context.change", text: "Context: now Board Meeting" } } });
    const panel = await openPanel(user);
    await ask(user, panel, "Is this event ready?");
    await within(panel).findByText(/^Not yet\./, { selector: ".og-answer" });
    await ask(user, panel, "Is Board Meeting ready?");
    const notice = (await within(panel).findByText("Context: now Board Meeting")).closest(".og-context-notice");
    const items = [...within(panel).getByLabelText("Conversation").querySelectorAll(".og-msg, .og-context-notice")];
    expect(items[items.indexOf(notice) + 1]).toHaveClass("og-msg-user");
  });

  it("a named event stays the context for the next turn", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: { ...READINESS, context: { ...CONTEXT, event: OTHER } } })
      .mockResolvedValueOnce({ data: guideMessage({ answer: "Public." }, "m2") });
    const panel = await openPanel(user);
    await ask(user, panel, "Is Board Meeting ready?");
    await within(panel).findByText(/^Not yet\./, { selector: ".og-answer" });
    await ask(user, panel, "who can access it?");
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(2));
    expect(api.post.mock.calls[1][1]).toEqual(expect.objectContaining({ event_id: OTHER.id }));
  });

  it("picking an event mid-conversation adds a thread notice at once", async () => {
    const user = setup();
    api.get.mockImplementation((url) => {
      if (url.endsWith("/bootstrap")) return Promise.resolve({ data: BOOT });
      if (url.endsWith("/context/events")) return Promise.resolve({ data: { items: [EVENT, OTHER] } });
      return Promise.resolve({ data: BOOT.attention });
    });
    api.post.mockResolvedValueOnce({ data: READINESS });
    const panel = await openPanel(user);
    await ask(user, panel, "Is this event ready?");
    await within(panel).findByText(/^Not yet\./, { selector: ".og-answer" });
    const select = within(panel).getByLabelText("Event context");
    await within(select).findByRole("option", { name: "Board Meeting" });
    await user.selectOptions(select, OTHER.id);
    expect((await within(panel).findByText("Context: now Board Meeting")).closest("[role=status]")).not.toBeNull();
  });
});

describe("answer structure", () => {
  it("labels the model explanation as Guide interpretation", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: guideMessage({ ...READINESS.message.payload, inference: true }) });
    const panel = await openPanel(user);
    await ask(user, panel, "Is this event ready?");
    expect(await within(panel).findByText(/Why · Guide interpretation/)).toBeInTheDocument();
    expect(within(panel).getByText("No host is assigned yet.").closest(".og-interpretation")).not.toBeNull();
  });

  it("flags inferred signals when there is no explanation", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: guideMessage({ answer: "Viewers fell.", inference: true }) });
    const panel = await openPanel(user);
    await ask(user, panel, "why are viewers dropping");
    expect(await within(panel).findByText(/Includes Guide interpretation/)).toBeInTheDocument();
  });

  it("shows API version and environment on technical answers", async () => {
    const user = setup();
    const technical = { api: "v1", environment: "Production", label: "Environment: Production · API: v1" };
    api.post.mockResolvedValueOnce({ data: guideMessage({
      answer: "A webhook endpoint receives event notifications.", technical,
      blocks: [{ type: "docs", title: "From ZoikoStream documentation", technical, items: [{ heading: "Webhooks", doc: "Developer platform", text: "…", source_id: "doc:developer#webhooks", path: "/organization/developers" }] }],
    }) });
    const panel = await openPanel(user);
    await ask(user, panel, "what is a webhook");
    expect(await within(panel).findAllByText(/Environment: Production · API: v1/)).toHaveLength(2);
  });

  it("renders an incident timeline in UTC from recorded timestamps", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: guideMessage({
      answer: "Incident SEV2 is open (delayed).",
      blocks: [{ type: "timeline", title: "Timeline · Town Hall", items: [
        { at: "2026-10-09T14:27:00Z", text: "Broadcast started", state: "active" },
        { at: "2026-10-09T14:31:00Z", text: "Incident SEV2 opened", state: "confirmed" }] }],
    }) });
    const panel = await openPanel(user);
    await ask(user, panel, "what's wrong");
    const card = await within(panel).findByRole("region", { name: "Timeline · Town Hall" });
    expect(within(card).getByText("14:27 UTC")).toHaveAttribute("dateTime", "2026-10-09T14:27:00Z");
    expect(within(card).getByText("Incident SEV2 opened")).toBeInTheDocument();
  });

  it("escalation offers a support-case proposal that still needs confirming", async () => {
    const user = setup();
    api.post
      .mockResolvedValueOnce({ data: guideMessage({ answer: "Program health is degraded.", blocks: [{
        type: "escalation", title: "Get human help", text: "I can prepare a support case for you to review.",
        context: { summary: "Town Hall: program degraded" },
        routes: [{ kind: "proposal", label: "Prepare a support case", action_id: "support.open_case", params: { text: "Town Hall: program degraded" } },
          { kind: "navigate", type: "navigate", label: "Open Support & Status", href: "/organization/support" }],
      }] }) })
      .mockResolvedValueOnce({ data: guideMessage({ answer: "Review this change.", blocks: [{ ...PROPOSAL, id: "p9", title: "Open a support case", confirm_label: "Confirm: Open support case" }] }, "m2") });
    const panel = await openPanel(user);
    await ask(user, panel, "what's wrong");
    const card = await within(panel).findByRole("region", { name: "Get human help" });
    expect(within(card).getByText(/Town Hall: program degraded/)).toBeInTheDocument();
    expect(within(card).getByRole("button", { name: /open support & status/i })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Prepare a support case" }));
    expect(api.post.mock.calls[1][1]).toEqual(expect.objectContaining({ action: { action_id: "support.open_case", params: { text: "Town Hall: program degraded" } } }));
    expect(await within(panel).findByRole("button", { name: "Confirm: Open support case" })).toBeInTheDocument();
  });

  it("a host's escalation names the org admin and offers no case", async () => {
    const user = setup();
    api.post.mockResolvedValueOnce({ data: guideMessage({ answer: "Your organization admin can open a support case for you.", blocks: [{
      type: "escalation", title: "Get human help", text: "Support cases are opened by your organization admin.", context: {},
      routes: [{ kind: "contact", label: "Contact your organization admin", text: "Organization admins open support cases." }],
    }] }) });
    const panel = await openPanel(user);
    await ask(user, panel, "I need to talk to a human");
    const card = await within(panel).findByRole("region", { name: "Get human help" });
    expect(within(card).getByText("Contact your organization admin.")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /support case/i })).not.toBeInTheDocument();
  });

  it("proposal shows its permission basis and the receipt shows actor and result", async () => {
    const user = setup();
    api.post
      .mockResolvedValueOnce({ data: guideMessage({ answer: "Review this change.", blocks: [{ ...PROPOSAL, permission: "Allowed: you host this event" }] }) })
      .mockResolvedValueOnce({ data: { ...PROPOSAL, status: "succeeded", receipt: { message: "Chat changed from On to Off.", correlation_id: "cor_1", actor: { id: "u1", name: "Dana Ops" }, result: "Off" } } });
    const panel = await openPanel(user);
    await ask(user, panel, "turn off chat");
    expect(await within(panel).findByText("Allowed: you host this event")).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Confirm: Turn off chat" }));
    expect(await within(panel).findByText(/By Dana Ops/)).toBeInTheDocument();
    expect(within(panel).getByText(/By Dana Ops/)).toHaveTextContent("By Dana Ops · Result: Off");
  });
});

describe("Live Mode (§6.9, §17)", () => {
  const LIVE_BOOT = { ...BOOT, context: { ...CONTEXT, event: { ...EVENT, status: "live" } } };

  it("home has a one-click live health check", async () => {
    const user = setup({ boot: LIVE_BOOT });
    api.post.mockResolvedValueOnce({ data: guideMessage({ answer: "No critical issue detected.", mode: "live" }) });
    const panel = await openPanel(user);
    await user.click(within(panel).getByRole("button", { name: "How is the broadcast doing?" }));
    expect(api.post).toHaveBeenCalledWith("/operator-guide/messages", expect.objectContaining({ text: "How is the broadcast doing?", event_id: EVENT.id }));
  });

  it("the thinking indicator is static while live", async () => {
    const user = setup({ boot: LIVE_BOOT });
    api.post.mockReturnValueOnce(new Promise(() => {}));
    const panel = await openPanel(user);
    await ask(user, panel, "status?");
    expect(await within(panel).findByText("Checking live sources…")).toBeInTheDocument();
    expect(panel.querySelector(".og-orbit")).toBeNull();
    expect(panel).toHaveClass("og-live");
  });

  it("refreshes context while open on a live event", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = setup({ boot: LIVE_BOOT });
      await openPanel(user);
      const boots = () => api.get.mock.calls.filter(([u]) => u.endsWith("/bootstrap")).length;
      await waitFor(() => expect(boots()).toBeGreaterThanOrEqual(2)); // mount + on open
      const before = boots();
      await vi.advanceTimersByTimeAsync(15_500);
      expect(boots()).toBe(before + 1);
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not poll when the event is not live", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = setup();
      await openPanel(user);
      const boots = () => api.get.mock.calls.filter(([u]) => u.endsWith("/bootstrap")).length;
      await waitFor(() => expect(boots()).toBeGreaterThanOrEqual(2));
      const before = boots();
      await vi.advanceTimersByTimeAsync(31_000);
      expect(boots()).toBe(before);
    } finally {
      vi.useRealTimers();
    }
  });
});
