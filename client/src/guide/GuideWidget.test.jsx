import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import GuideWidget from "./GuideWidget";
import { clearSession, parseSSE } from "./guideApi";
import { renderable } from "./parts/actionUtils";
import { setGuideAnalyticsConsent } from "./guideAnalytics";

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
  welcome: "Welcome to ZoikoStream Guide. I can help with product questions, pricing and demos, or viewer support.",
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
afterEach(() => {
  vi.restoreAllMocks();
  setGuideAnalyticsConsent(null);
});

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
    expect(await within(dialog).findByRole("heading", { name: "What do you need help with?" })).toBeInTheDocument();
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
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
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
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    await user.click(await within(dialog).findByRole("button", { name: "Submit demo request" }));
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
    expect(await within(dialog).findByRole("heading", { name: "What do you need help with?" })).toBeInTheDocument();
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

describe("Guide widget on a console page", () => {
  const page = { page_id: "contact", page_type: "contact" };
  const at = (pathname, suppressed) => (
    <MemoryRouter initialEntries={["/contact"]}>
      <GuideWidget page={suppressed ? null : page} pathname={pathname} suppressed={suppressed} />
    </MemoryRouter>
  );

  it("an open panel closes and nothing renders, so it never sits on the Operator Guide's launcher", async () => {
    const user = userEvent.setup();
    const { rerender, container } = render(at("/contact", false));
    await openGuide(user);

    rerender(at("/organization/members", true));
    expect(screen.queryByRole("dialog", { name: "Guide" })).toBeNull();
    expect(container.querySelector(".zg-launcher")).toBeNull();
    expect(container.querySelector(".zg")).toBeNull();

    // Back on a public page: the launcher returns, the panel does not pop open by itself.
    rerender(at("/contact", false));
    expect(await screen.findByRole("button", { name: /open zoikostream guide/i })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Guide" })).toBeNull();
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

const sse = (events) =>
  new Response(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(""), {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });

const LEAD_WITH_ORG = {
  ...LEAD,
  fields: [...LEAD.fields, { field_id: "organization", label: "Organization", type: "text", required: false, max_length: 160 }],
};

describe("Guide UX requirements", () => {
  it("renders the canonical welcome sentence from the session (CF §28)", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(await within(dialog).findByRole("heading", { name: "Welcome to ZoikoStream Guide." })).toBeInTheDocument();
    expect(within(dialog).getByText("I can help with product questions, pricing and demos, or viewer support.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Hi there/)).toBeNull();
  });

  it("collapses a long answer behind an accessible Show more / Show less toggle (PR-006)", async () => {
    const tail = "The full detail paragraph explains every option in depth.";
    const long = `${"Short summary sentence. ".repeat(10)}\n\n${"More context follows here. ".repeat(20)}\n\n${tail}`;
    installFetch({
      "POST /sessions/s-1/messages": () => json({ ...ANSWER, assistant_message: { ...ANSWER.assistant_message, text: long } }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    const more = await within(dialog).findByRole("button", { name: "Show more" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    expect(within(dialog).queryByText(tail)).toBeNull();
    // Sources and actions stay visible while collapsed.
    expect(within(dialog).getByRole("button", { name: "Source: Use cases" })).toBeInTheDocument();
    await user.click(more);
    expect(within(dialog).getByText(tail)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
  });

  it("keeps lead form values when Guide is closed (X or Escape) and reopened (Figma §10.1)", async () => {
    const user = userEvent.setup();
    renderWidget();
    let dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Request a demo" }));
    await user.type(await within(dialog).findByLabelText(/Full name/), "Jamie Rivera");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Close Guide" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    dialog = await openGuide(user);
    expect(within(dialog).getByLabelText(/Full name/)).toHaveValue("Jamie Rivera");
    expect(within(dialog).getByRole("checkbox")).toBeChecked();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    dialog = await openGuide(user);
    expect(within(dialog).getByLabelText(/Full name/)).toHaveValue("Jamie Rivera");
    expect(localStorage.length).toBe(0); // never persisted to browser storage
  });

  it("lead flow: header title, review step, Submit demo request, confirmed (WF-06, Figma §8)", async () => {
    installFetch({
      "POST /sessions/s-1/lead-flows": () => json(LEAD_WITH_ORG, 201),
      "PATCH /sessions/s-1/lead-flows/l-1234567890": () => json({ ...LEAD_WITH_ORG, status: "ready_to_submit" }),
      "PUT /sessions/s-1/consents/sales_contact": () => json({ receipt_id: "cns_1", status: "granted" }),
      "POST /sessions/s-1/lead-flows/l-1234567890/submit": () =>
        json({ outcome: "confirmed", message: "Thanks. The sales team will contact you.", submission_reference: "ZS-123" }),
    });
    setGuideAnalyticsConsent("granted");
    const events = [];
    const onEvent = (e) => events.push(e.detail);
    window.addEventListener("zoikostream:guide-analytics", onEvent);
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Request a demo" }));
    expect(await within(dialog).findByText("Request a demo", { selector: "#zg-title + p" })).toBeInTheDocument();
    await user.type(within(dialog).getByLabelText(/Full name/), "Jamie Rivera");
    await user.type(within(dialog).getByLabelText(/Work email/), "jamie@example.org");
    // Organization: 2-160 characters (Figma §11), validated after interaction.
    await user.type(within(dialog).getByLabelText(/Organization/), "A");
    await user.tab();
    expect(await within(dialog).findByText("Enter at least 2 characters.")).toBeInTheDocument();
    expect(within(dialog).getByLabelText(/Organization/)).toHaveAttribute("maxlength", "160");
    await user.type(within(dialog).getByLabelText(/Organization/), "cme Events");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    const review = await within(dialog).findByRole("heading", { name: "Review your request" });
    const card = review.closest("section");
    expect(within(card).getByText("Acme Events")).toBeInTheDocument();
    expect(calls.some((c) => c.path.endsWith("/submit"))).toBe(false); // nothing sent before the final step
    await user.click(within(card).getByRole("button", { name: "Submit demo request" }));
    expect(await within(dialog).findByText("Request received")).toBeInTheDocument();
    expect(within(dialog).getByText("Public AI Assistant")).toBeInTheDocument(); // header back to default
    window.removeEventListener("zoikostream:guide-analytics", onEvent);
    const names = events.map((e) => e.event);
    // CF §30: a demo flow is guide_demo_started (also a flow/lead start); confirmed only = submitted.
    expect(names).toContain("guide_demo_started");
    expect(events.find((e) => e.event === "guide_demo_started").aliases).toEqual(["guide_flow_started", "guide_lead_started"]);
    expect(names).toContain("guide_demo_submitted");
    expect(events.find((e) => e.event === "guide_demo_submitted").aliases).toContain("guide_lead_submitted");
    const blob = JSON.stringify(events);
    expect(blob).not.toMatch(/Jamie|jamie@example\.org|Acme/); // no lead values in analytics
  });

  it("emits spec analytics events without message text or PII (PRD §16, CF §30, WF §07)", async () => {
    setGuideAnalyticsConsent("granted");
    const events = [];
    const onEvent = (e) => events.push(e.detail);
    window.addEventListener("zoikostream:guide-analytics", onEvent);
    window.dataLayer = [];
    installFetch({ "POST /sessions/s-1/feedback": () => new Response(null, { status: 202 }) });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    await within(dialog).findByRole("button", { name: "Source: Use cases" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "my email is jamie@example.org{Enter}");
    await waitFor(() => expect(events.filter((e) => e.event === "guide_answer_completed")).toHaveLength(2));
    await user.click(within(dialog).getAllByRole("button", { name: "No, this wasn't helpful" })[0]);
    await user.click(within(dialog).getByRole("button", { name: "Out of date" }));
    await within(dialog).findByText("Thanks for the feedback.");
    window.removeEventListener("zoikostream:guide-analytics", onEvent);
    const names = events.map((e) => e.event);
    for (const n of ["guide_launcher_impression", "guide_opened", "guide_intent_selected", "guide_message_submitted", "guide_answer_completed", "guide_feedback"]) {
      expect(names).toContain(n);
    }
    // Wireframe §07 names travel as aliases of the same occurrence (no double counting).
    expect(events.find((e) => e.event === "guide_launcher_impression").aliases).toContain("guide_launcher_viewed");
    expect(events.find((e) => e.event === "guide_message_submitted").aliases).toContain("guide_message_sent");
    expect(events.find((e) => e.event === "guide_answer_completed").aliases).toContain("guide_response_rendered");
    expect(events.find((e) => e.event === "guide_feedback").aliases).toContain("guide_feedback_submitted");
    // PRD §16 parameters: consent_state, intent_class + length_bucket, latency_bucket.
    expect(events.find((e) => e.event === "guide_opened")).toMatchObject({ entry_point: "launcher", consent_state: "granted" });
    const typed = events.filter((e) => e.event === "guide_message_submitted").at(-1);
    expect(typed).toMatchObject({ intent_class: "free_text", length_bucket: "21_80" });
    expect(events.find((e) => e.event === "guide_answer_completed")).toMatchObject({ answer_class: "factual", latency_bucket: "lt_1s", source_count: 1 });
    expect(events.find((e) => e.event === "guide_intent_selected").intent_id).toBe("I-PRODUCT");
    expect(window.dataLayer.map((e) => e.event)).toContain("guide_opened");
    const blob = JSON.stringify(events) + JSON.stringify(window.dataLayer);
    expect(blob).not.toMatch(/jamie|Families can stream|Product overview|s-1|csrf/);
    delete window.dataLayer;
  });

  it("feedback offers exactly the WF-17 reasons", async () => {
    installFetch({ "POST /sessions/s-1/feedback": () => new Response(null, { status: 202 }) });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    await user.click(await within(dialog).findByRole("button", { name: "No, this wasn't helpful" }));
    const group = within(dialog).getByRole("group", { name: "Optional: What could be better?" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Incorrect", "Out of date", "Not clear", "Did not answer my question", "Skip",
    ]);
    await user.click(within(group).getByRole("button", { name: "Did not answer my question" }));
    await within(dialog).findByText("Thanks for the feedback.");
    const fb = calls.find((c) => c.path === "/sessions/s-1/feedback");
    expect(fb.body).toMatchObject({ rating: "down", reason_code: "not_helpful" });
  });

  it("ended state offers Start new conversation and Close Guide (WF-17)", async () => {
    installFetch({ "POST /sessions/s-1/end": () => new Response(null, { status: 204 }) });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.click(within(dialog).getByRole("button", { name: "Conversation options" }));
    await user.click(within(dialog).getByRole("menuitem", { name: /End conversation/ }));
    const panel = (await within(dialog).findByText("Conversation ended")).closest(".zg-state");
    expect(within(panel).getByRole("button", { name: "Start new conversation" })).toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Close Guide" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: /open zoikostream guide/i })).toHaveFocus());
  });

  it("network failure keeps the text and Retry re-sends it with a new id (not accepted)", async () => {
    let n = 0;
    installFetch({
      "POST /sessions/s-1/messages": () => (++n === 1 ? Promise.reject(new TypeError("offline")) : json(ANSWER)),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "Can you stream a memorial?{Enter}");
    expect(await within(dialog).findByText(/Guide couldn't be reached/)).toBeInTheDocument();
    expect(within(dialog).getByText("Can you stream a memorial?")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(dialog.querySelector(".zg-answer")).toHaveTextContent(/Families can stream/));
    const sent = calls.filter((c) => c.path === "/sessions/s-1/messages");
    expect(sent).toHaveLength(2);
    expect(sent[1].body.text).toBe("Can you stream a memorial?");
    expect(sent[1].body.client_message_id).not.toBe(sent[0].body.client_message_id);
    expect(within(dialog).queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("interrupted stream is labelled incomplete and Retry reuses the accepted id", async () => {
    let n = 0;
    installFetch({
      "POST /sessions/s-1/messages": () =>
        ++n === 1
          ? sse([["response.started", { assistant_message_id: "m-1" }], ["response.text.delta", { delta: "Partial answ" }]])
          : json(ANSWER),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "Tell me more{Enter}");
    expect(await within(dialog).findByText("Incomplete response")).toBeInTheDocument();
    expect(within(dialog).getByText(/The answer was interrupted/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(dialog.querySelectorAll(".zg-answer")[1]).toHaveTextContent(/Families can stream/));
    const sent = calls.filter((c) => c.path === "/sessions/s-1/messages");
    expect(sent[1].body.client_message_id).toBe(sent[0].body.client_message_id); // server holds it: replay
  });

  it("429 with Retry-After: RATE_LIMITED state, accessible countdown, composer disabled until it ends", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => json({ code: "GUIDE_RATE_LIMITED" }, 429, { "retry-after": "1" }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "hello{Enter}");
    const timer = await within(dialog).findByRole("timer");
    expect(timer).toHaveTextContent("You can try again in 1 second.");
    expect(within(dialog).getAllByText(/several requests in a short period/)).toHaveLength(1);
    expect(within(dialog).getByLabelText("Ask Guide a question")).toBeDisabled();
    expect(within(dialog).getByRole("button", { name: "Retry" })).toBeDisabled();
    await waitFor(() => expect(within(dialog).getByLabelText("Ask Guide a question")).not.toBeDisabled(), { timeout: 3000 });
    expect(within(dialog).queryByRole("timer")).toBeNull();
  });

  it("429 without Retry-After shows no invented countdown", async () => {
    installFetch({ "POST /sessions/s-1/messages": () => json({ code: "GUIDE_RATE_LIMITED" }, 429) });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    expect(await within(dialog).findByText(/several requests in a short period/)).toBeInTheDocument();
    expect(within(dialog).queryByRole("timer")).toBeNull();
  });

  it("degraded answer switches the header to Help temporarily limited (Figma §8)", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => json({ session_state: "DEGRADED", assistant_message: {
        ...ANSWER.assistant_message, answer_class: "degraded", grounded: false, sources: [], actions: [] } }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    expect(await within(dialog).findByText("Help temporarily limited", { selector: "#zg-title + p" })).toBeInTheDocument();
  });

  it("a chosen follow-up topic shows selected and disables its set (GC-005)", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => json({ session_state: "ACTIVE", assistant_message: {
        message_id: "m-2", text: "I'm Guide.", answer_class: "conversation", grounded: false,
        uncertainty: { state: "none", message: null }, sources: [], actions: [],
        suggestions: [{ intent_id: "I-VIEWER", label: "Viewer help" }, { intent_id: "I-PRICING", label: "Pricing" }] } }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "who are you?{Enter}");
    const group = await within(dialog).findByRole("group", { name: "Suggested topics" });
    await user.click(within(group).getByRole("button", { name: "Viewer help" }));
    expect(within(group).getByRole("button", { name: "Viewer help" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "Viewer help" })).toBeDisabled();
    expect(within(group).getByRole("button", { name: "Pricing" })).toBeDisabled();
  });

  it("warns before idle expiry and Stay in conversation refreshes the session (Figma §13)", async () => {
    installFetch({
      "POST /sessions": () => json({ ...SESSION, idle_timeout_seconds: 60 }, 201),
      "PATCH /sessions/s-1/context": () => json({ ...SESSION, idle_timeout_seconds: 60 }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(await within(dialog).findByText(/will end soon because it has been inactive/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Stay in conversation" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === "/sessions/s-1/context")).toBe(true));
  });

  it("idle expiry ends the conversation and keeps the unsent draft", async () => {
    installFetch({ "POST /sessions": () => json({ ...SESSION, idle_timeout_seconds: 1 }, 201) });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await within(dialog).findByRole("button", { name: "Product overview" });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "half-typed question");
    expect(await within(dialog).findByText(/ended because it was inactive/, {}, { timeout: 3000 })).toBeInTheDocument();
    expect(within(dialog).getByLabelText("Ask Guide a question")).toHaveValue("half-typed question");
  });
});

describe("Guide on mobile: Back closes nested detail, then Guide (WF-18 A5)", () => {
  let width;
  beforeEach(() => {
    width = window.innerWidth;
    window.innerWidth = 375;
    window.history.replaceState(null, "", "/");
  });
  afterEach(() => {
    window.innerWidth = width;
  });

  const back = () =>
    new Promise((resolve) => {
      window.addEventListener("popstate", () => setTimeout(resolve, 0), { once: true });
      window.history.back();
    });

  it("Back closes source detail first, then Guide, without leaving the page", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(window.history.state?.zgGuide).toBe(true);
    await user.click(await within(dialog).findByRole("button", { name: "Product overview" }));
    await user.click(await within(dialog).findByRole("button", { name: "Source: Use cases" }));
    expect(within(dialog).getByText("Open public source")).toBeInTheDocument();
    await act(back);
    await waitFor(() => expect(within(dialog).queryByText("Open public source")).toBeNull());
    expect(screen.getByRole("dialog", { name: "Guide" })).toBeInTheDocument();
    expect(window.history.state?.zgGuide).toBe(true);
    await act(back);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(window.location.pathname).toBe("/");
    expect(screen.getByTestId("where")).toHaveTextContent("/");
  });

  it("closing with X removes the Guide history entry", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(window.history.state?.zgGuide).toBe(true);
    await user.click(within(dialog).getByRole("button", { name: "Close Guide" }));
    await waitFor(() => expect(window.history.state?.zgGuide).toBeFalsy());
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
