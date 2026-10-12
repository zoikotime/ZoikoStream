import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import GuideWidget from "./GuideWidget";
import { clearSession, parseSSE } from "./guideApi";
import { renderable } from "./parts/actionUtils";

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

const BOOT = {
  enabled: true,
  assistant: { name: "Guide", descriptor: "Public AI Assistant" },
  limits: { max_message_chars: 4000 },
  privacy: { privacy_notice_href: "/privacy" },
  direct_routes: [{ action_id: "status-page", type: "open_status", label: "Service status", href: "/status" }],
  suggested_intents: [],
};
const SESSION = {
  session_id: "s-1",
  csrf_token: "csrf-1",
  session_state: "ACTIVE",
  welcome: "Welcome",
  event_context: null,
  suggested_intents: [
    { intent_id: "I-PRODUCT", label: "Product overview", priority: 10 },
    { intent_id: "I-VIEWER", label: "Viewer help", priority: 9 },
    { intent_id: "I-DEMO", label: "Request a demo", priority: 8 },
  ],
};
const ANSWER = {
  session_state: "ACTIVE",
  assistant_message: {
    message_id: "m-1",
    text: "Yes. Families can stream a memorial service to invited relatives.",
    answer_class: "factual",
    grounded: true,
    uncertainty: { state: "none", message: null },
    sources: [{ source_id: "use-cases", title: "Use cases", source_type: "product", version: "2026.10", updated_at: "2026-10-07", canonical_path: "/" }],
    actions: [
      { action_id: "request-demo", type: "start_lead_flow", label: "Request a demo" },
      { action_id: "evil", type: "navigate", label: "Claim prize", href: "https://evil.example" },
      { action_id: "contact-sales", type: "handoff", label: "Talk to sales", href: "/contact" },
    ],
  },
};
const FLOW = {
  flow_id: "f-1", flow_type: "viewer_other", title: "Viewer help", status: "in_progress",
  resolution_code: null, message: null, notice: null, actions: [],
  step: { step_id: "category", prompt: "What do you need help with?", input_type: "choice",
    options: [{ option_id: "video", label: "Video or buffering" }], allow_skip: false, allow_back: false },
};
const LEAD = {
  flow_id: "l-1234567890", flow_kind: "demo", status: "collecting", schema_version: "lead-v1",
  purpose_notice: { text: "Tell us how to reach you. We'll use these details only to respond to your demo request.", notice_version: "sales-contact-v1", privacy_href: "/privacy" },
  consent: { purpose: "sales_contact", required: true, notice_version: "sales-contact-v1", text: "I agree that ZoikoStream may contact me about this request using the details I provide." },
  fields: [
    { field_id: "full_name", label: "Full name", type: "text", required: true },
    { field_id: "work_email", label: "Work email", type: "email", required: true },
  ],
  missing_required: ["full_name", "work_email"],
};

let calls;
function installFetch(overrides = {}) {
  calls = [];
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const path = url.replace(/^.*\/api\/v1\/public-guide/, "");
    calls.push({ path, method: init.method || "GET", headers: init.headers || {}, body: init.body ? JSON.parse(init.body) : undefined });
    const key = `${init.method || "GET"} ${path.split("?")[0]}`;
    if (overrides[key]) return overrides[key](init);
    if (key === "GET /bootstrap") return json(BOOT);
    if (key === "POST /sessions") return json(SESSION, 201);
    if (key === "POST /sessions/s-1/messages") return json(ANSWER);
    if (key === "POST /sessions/s-1/support-flows") return json(FLOW, 201);
    if (key === "POST /sessions/s-1/lead-flows") return json(LEAD, 201);
    return json({ code: "GUIDE_RESOURCE_NOT_FOUND" }, 404);
  });
}

function Where() {
  return <div data-testid="where">{useLocation().pathname}</div>;
}

function renderWidget(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<><GuideWidget page={{ page_id: "home", page_type: "home" }} pathname={path} /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openGuide(user) {
  await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
  return screen.findByRole("dialog", { name: "Guide" });
}

beforeEach(() => {
  clearSession();
  installFetch();
});
afterEach(() => vi.restoreAllMocks());

describe("Guide widget", () => {
  it("bootstraps without a session and shows the launcher", async () => {
    renderWidget();
    expect(await screen.findByRole("button", { name: /open zoikostream guide/i })).toBeInTheDocument();
    expect(calls.map((c) => c.path.split("?")[0])).toEqual(["/bootstrap"]); // API-AT-01
  });

  it("renders nothing when Guide is disabled", async () => {
    installFetch({ "GET /bootstrap": () => json({ ...BOOT, enabled: false }) });
    const { container } = renderWidget();
    await waitFor(() => expect(calls.length).toBe(1));
    expect(container.querySelector(".zg-launcher")).toBeNull();
  });

  it("renders nothing when the API is down (site unaffected)", async () => {
    installFetch({ "GET /bootstrap": () => Promise.reject(new TypeError("offline")) });
    const { container } = renderWidget();
    await waitFor(() => expect(calls.length).toBe(1));
    expect(container.querySelector(".zg-launcher")).toBeNull();
  });

  it("opens with AI identity, privacy access and suggested intents; focus enters, then returns", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(within(dialog).getByText("Public AI Assistant")).toBeInTheDocument(); // PR-036
    expect(await within(dialog).findByRole("button", { name: "Product overview" })).toBeInTheDocument();
    await waitFor(() => expect(dialog.querySelector(".zg-titles")).toHaveFocus());
    await user.click(within(dialog).getByRole("button", { name: "Close Guide" }));
    await waitFor(() => expect(screen.getByRole("button", { name: /open zoikostream guide/i })).toHaveFocus());
  });

  it("sends a message with CSRF and never an Authorization header", async () => {
    localStorage.setItem("token", "platform-jwt");
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "Can you stream a memorial?{Enter}");
    await waitFor(() => expect(dialog.querySelector(".zg-answer")).toHaveTextContent(/Families can stream a memorial service/));
    // The completed answer is also announced once through the polite live region.
    expect(dialog.querySelector('[aria-live="polite"]')).toHaveTextContent(/Families can stream/);
    const msg = calls.find((c) => c.path === "/sessions/s-1/messages");
    expect(msg.headers["X-ZS-CSRF"]).toBe("csrf-1");
    expect(msg.headers.Authorization).toBeUndefined(); // public Guide stays anonymous
    expect(msg.body.text).toBe("Can you stream a memorial?");
    localStorage.removeItem("token");
  });

  it("shows sources and only approved, relative actions", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.click(within(dialog).getByRole("button", { name: "Product overview" }));
    expect(await within(dialog).findByRole("button", { name: "Source: Use cases" })).toBeInTheDocument();
    const actions = within(dialog.querySelector(".zg-actions"));
    expect(actions.getByRole("button", { name: "Request a demo" })).toBeInTheDocument();
    expect(within(dialog).queryByText("Claim prize")).toBeNull(); // PG-AT-19
    await user.click(actions.getByRole("button", { name: "Talk to sales" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/contact"); // in-app navigation
  });

  it("starts the deterministic viewer flow from the quick intent", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Viewer help" }));
    expect(await within(dialog).findByText("What do you need help with?")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Video or buffering" })).toBeInTheDocument();
    expect(dialog.querySelector(".zg-card input")).toBeNull(); // no free-text/PIN field
  });

  it("lead form: notice first, consent unchecked, submit blocked without consent", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Request a demo" }));
    expect(await within(dialog).findByText(/only to respond to your demo request/)).toBeInTheDocument();
    const box = within(dialog).getByRole("checkbox");
    expect(box).not.toBeChecked(); // never pre-checked
    await user.type(within(dialog).getByLabelText(/Full name/), "Jamie Rivera");
    await user.type(within(dialog).getByLabelText(/Work email/), "jamie@example.org");
    await user.click(within(dialog).getByRole("button", { name: "Submit request" }));
    expect(await within(dialog).findByText(/Please confirm we may contact you/)).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/submit"))).toBe(false);
  });

  it("lead form: unknown outcome is not shown as success", async () => {
    installFetch({
      "PATCH /sessions/s-1/lead-flows/l-1234567890": () => json({ ...LEAD, status: "ready_to_submit" }),
      "PUT /sessions/s-1/consents/sales_contact": () => json({ receipt_id: "cns_1", status: "granted" }),
      "POST /sessions/s-1/lead-flows/l-1234567890/submit": () =>
        json({ code: "GUIDE_OUTCOME_UNKNOWN", outcome: "unknown", detail: "I can't confirm whether that request was received yet.", actions: [] }, 504),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Request a demo" }));
    await user.type(await within(dialog).findByLabelText(/Full name/), "Jamie Rivera");
    await user.type(within(dialog).getByLabelText(/Work email/), "jamie@example.org");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Submit request" }));
    expect(await within(dialog).findByText("We can't confirm this yet")).toBeInTheDocument();
    expect(within(dialog).queryByText("Request received")).toBeNull();
    const submit = calls.find((c) => c.path.endsWith("/submit"));
    expect(submit.headers["Idempotency-Key"]).toBeTruthy();
  });

  it("rate limit shows a wait notice and direct routes", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => json({ code: "GUIDE_RATE_LIMITED", detail: "slow down" }, 429, { "retry-after": "20" }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    expect(await within(dialog).findByText(/several requests in a short period/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Service status" })).toBeInTheDocument();
  });

  it("a session rejected right after creation ends cleanly with a cookie hint (no stacking)", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => json({ code: "GUIDE_SESSION_INVALID" }, 401),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    expect(await within(dialog).findByText("Conversation ended")).toBeInTheDocument();
    expect(within(dialog).getByText(/Cookies for this site may be blocked/)).toBeInTheDocument();
    expect(within(dialog).getAllByRole("button", { name: "Start new conversation" })).toHaveLength(1);
    expect(within(dialog).queryByRole("button", { name: "Product overview" })).toBeNull();
  });

  it("conversational replies show follow-up topics that start the right flow", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => json({ session_state: "ACTIVE", assistant_message: {
        message_id: "m-2", text: "I'm Guide, the AI assistant on the ZoikoStream website.",
        answer_class: "conversation", grounded: false, uncertainty: { state: "none", message: null },
        sources: [], actions: [], suggestions: [{ intent_id: "I-VIEWER", label: "Viewer help" }] } }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "who are you?{Enter}");
    const group = await within(dialog).findByRole("group", { name: "Suggested topics" });
    expect(within(dialog).queryByText("Was this helpful?")).toBeNull(); // no rating small talk
    await user.click(within(group).getByRole("button", { name: "Viewer help" }));
    expect(await within(dialog).findByText("What do you need help with?")).toBeInTheDocument();
  });

  it("session creation failure shows the unavailable state with direct routes", async () => {
    installFetch({ "POST /sessions": () => json({ code: "GUIDE_UNAVAILABLE" }, 503) });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(await within(dialog).findByText(/You can still use ZoikoStream normally/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Service status" })).toBeInTheDocument();
  });
});

describe("Guide API helpers", () => {
  it("parses SSE and ignores malformed events", () => {
    const events = parseSSE('event: response.text.delta\ndata: {"delta":"Hi"}\n\nevent: x\ndata: {bad json\n\nevent: response.completed\ndata: {"grounded":true}\n\n');
    expect(events).toEqual([
      { event: "response.text.delta", data: { delta: "Hi" } },
      { event: "response.completed", data: { grounded: true } },
    ]);
  });

  it("only renders known actions with relative hrefs", () => {
    expect(renderable({ type: "navigate", label: "x", href: "/contact" })).toBe(true);
    expect(renderable({ type: "navigate", label: "x", href: "//evil.example" })).toBe(false);
    expect(renderable({ type: "navigate", label: "x", href: "javascript:alert(1)" })).toBe(false);
    expect(renderable({ type: "delete_account", label: "x", href: "/x" })).toBe(false);
    expect(renderable({ type: "start_support_flow", label: "x" })).toBe(true);
  });
});

describe("Guide widget on a viewer page (WF-10)", () => {
  beforeEach(() => {
    clearSession();
    installFetch({
      "POST /sessions": () => json({
        ...SESSION,
        suggested_intents: [{ intent_id: "I-VIEWER", label: "Join / access help", priority: 10 }],
        event_context: { event_display_name: "Town Hall", public_status: "live", public_start_at: "2026-10-12T06:05:00Z", public_timezone: "Asia/Kolkata" },
      }, 201),
    });
  });

  it("shows the viewer card: public context, viewer topics and the organizer-details footer", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/viewer-test"]}>
        <GuideWidget page={{ page_id: "viewer", page_type: "viewer" }} pathname="/viewer-test" />
      </MemoryRouter>,
    );
    const launcher = await screen.findByRole("button", { name: "Open ZoikoStream Guide" });
    expect(launcher).toHaveClass("zg-launcher--viewer");
    await user.click(launcher);
    const dialog = await screen.findByRole("dialog", { name: "Guide" });
    expect(await within(dialog).findByText("Help with this event")).toBeInTheDocument();
    expect(within(dialog).getByText("Viewer help · Town Hall")).toBeInTheDocument();
    const ctx = within(dialog).getByRole("region", { name: "Public context" });
    expect(within(ctx).getByText("Live now")).toBeInTheDocument();
    expect(within(ctx).getByText(/Monday, October 12.*11:35/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Join / access help" })).toBeInTheDocument();
    expect(within(dialog).getByText("Private organizer details are not available in public chat.")).toBeInTheDocument();
  });
});
