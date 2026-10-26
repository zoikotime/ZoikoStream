// Spec-gap coverage for the public Guide: Figma §1.2/§3/§8/§13/§14, WF-02/WF-12, CF §30, and
// the client side of contracts C2-C7. The backend is mocked at fetch level.
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import GuideWidget from "./GuideWidget";
import GuideMount from "./GuideMount";
import AskGuideCta from "./AskGuideCta";
import { clearSession } from "./guideApi";
import { askGuide, parseGuideParam, setGuideAvailable } from "./askGuide";
import { getGuideAnalyticsConsent, setGuideAnalyticsConsent } from "./guideAnalytics";
import { resetPrivacyNotice } from "./parts/privacyNotice";

vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

const BOOT = {
  enabled: true,
  assistant: { name: "Guide", descriptor: "Public AI Assistant" },
  limits: { max_message_chars: 4000 },
  privacy: { privacy_notice_href: "/privacy" },
  direct_routes: [
    { action_id: "help-center", type: "open_help", label: "Help Center", href: "/contact" },
    { action_id: "status-page", type: "open_status", label: "Service status", href: "/status" },
  ],
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
const MSG = {
  message_id: "m-1",
  text: "Yes. Families can stream a memorial service to invited relatives.",
  answer_class: "factual",
  grounded: true,
  uncertainty: { state: "none", message: null },
  sources: [{ source_id: "use-cases", title: "Use cases", source_type: "product", version: "2026.10", updated_at: "2026-10-07", canonical_path: "/" }],
  actions: [],
};
const answer = (patch = {}) => json({ session_state: "ACTIVE", assistant_message: { ...MSG, ...patch } });

const STEP1 = {
  flow_id: "f-1", flow_type: "playback", title: "Playback help", status: "in_progress",
  resolution_code: null, message: null, notice: null, actions: [],
  step: { step_id: "symptom", prompt: "What is happening?", input_type: "choice",
    options: [{ option_id: "no_start", label: "Video will not start" }], allow_skip: false, allow_back: false },
};
const STEP2 = {
  ...STEP1,
  step: { step_id: "refresh", prompt: "Refresh the event page. Did that fix it?", input_type: "choice",
    options: [{ option_id: "yes", label: "Yes" }, { option_id: "no", label: "No - continue" }], allow_skip: false, allow_back: true },
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
    if (key === "POST /sessions/s-1/messages") return answer();
    if (key === "POST /sessions/s-1/support-flows") return json(STEP1, 201);
    if (key === "POST /sessions/s-1/support-flows/f-1/steps") return json(STEP2);
    if (key === "POST /sessions/s-1/lead-flows") return json(LEAD, 201);
    return json({ code: "GUIDE_RESOURCE_NOT_FOUND" }, 404);
  });
}

function Where() {
  const { pathname, search } = useLocation();
  return <div data-testid="where">{pathname + search}</div>;
}

function renderWidget(page = { page_id: "home", page_type: "home" }, path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<><GuideWidget page={page} pathname={path} /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

async function openGuide(user) {
  await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
  const dialog = await screen.findByRole("dialog", { name: "Guide" });
  await within(dialog).findByRole("button", { name: "Product overview" });
  return dialog;
}

function collectEvents() {
  const events = [];
  const on = (e) => events.push(e.detail);
  window.addEventListener("zoikostream:guide-analytics", on);
  return { events, stop: () => window.removeEventListener("zoikostream:guide-analytics", on) };
}

const sse = (events) =>
  new Response(events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join(""), {
    status: 200, headers: { "content-type": "text/event-stream" },
  });

function setOnline(value) {
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => value });
  act(() => {
    window.dispatchEvent(new Event(value ? "online" : "offline"));
  });
}

beforeEach(() => {
  clearSession();
  resetPrivacyNotice();
  installFetch();
});
afterEach(() => {
  vi.restoreAllMocks();
  setGuideAnalyticsConsent(null);
  delete navigator.onLine;
  Object.defineProperty(navigator, "onLine", { configurable: true, get: () => true });
});

describe("Visual system and welcome (brand Guide design; Figma §3, §7.1; WF-02, WF-12)", () => {
  it("launcher and header carry the decorative brand mark; assistant rows are reading blocks", async () => {
    const user = userEvent.setup();
    renderWidget();
    const launcher = await screen.findByRole("button", { name: "Open ZoikoStream Guide" });
    // The mark is decoration: the button's name comes from its label, never the image.
    expect(launcher.querySelector(".zg-launcher-mark img")).toHaveAttribute("alt", "");
    const dialog = await openGuide(user);
    expect(dialog.querySelector(".zg-brandmark img")).toHaveAttribute("alt", "");
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "Can you stream a memorial?{Enter}");
    const block = await waitFor(() => {
      const el = dialog.querySelector(".zg-answer");
      expect(el).toHaveTextContent(/Families can stream/);
      return el;
    });
    expect(block).toHaveClass("zg-block");
    expect(block.closest(".zg-row")).toBeNull();
    expect(dialog.querySelector(".zg-bubble")).toBeNull();
    expect(within(dialog).getByText("Can you stream a memorial?").closest(".zg-msg--user")).not.toBeNull();
  });

  it("composer placeholder is the exact spec copy", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(within(dialog).getByLabelText("Ask Guide a question")).toHaveAttribute("placeholder", "Ask Guide...");
  });

  it("first use shows Privacy in Guide with the processing statement; Privacy and Help Center links; popular question", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    const notice = within(dialog).getByRole("region", { name: "Privacy in Guide" });
    expect(within(notice).getByText(/We may process your messages to answer your request and operate the service\./)).toBeInTheDocument();
    expect(within(notice).getByText(/Do not enter passwords, PINs, payment card details, or private event links\./)).toBeInTheDocument();
    const links = within(dialog).getByRole("navigation", { name: "Guide links" });
    expect(within(links).getByRole("button", { name: "Privacy" })).toBeInTheDocument();
    expect(within(links).getByRole("button", { name: "Help Center" })).toBeInTheDocument();
    await user.click(within(notice).getByRole("button", { name: "Continue" }));
    expect(within(dialog).queryByRole("region", { name: "Privacy in Guide" })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "How secure is ZoikoStream?" }));
    await waitFor(() => expect(calls.some((c) => c.path === "/sessions/s-1/messages")).toBe(true));
    expect(calls.find((c) => c.path === "/sessions/s-1/messages").body).toMatchObject({ text: "How secure is ZoikoStream?", intent_hint: "I-SECURITY" });
  });

  it("Help Center link navigates in-app", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(within(dialog).getByRole("navigation", { name: "Guide links" })).getByRole("button", { name: "Help Center" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/contact");
  });
});

describe("Launcher keyboard (WF-01 acceptance)", () => {
  it("opens with Enter and with Space, and focus returns on close", async () => {
    const user = userEvent.setup();
    renderWidget();
    const launcher = await screen.findByRole("button", { name: "Open ZoikoStream Guide" });
    launcher.focus();
    await user.keyboard("{Enter}");
    let dialog = await screen.findByRole("dialog", { name: "Guide" });
    await user.click(within(dialog).getByRole("button", { name: "Close Guide" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Open ZoikoStream Guide" })).toHaveFocus());
    await user.keyboard(" ");
    dialog = await screen.findByRole("dialog", { name: "Guide" });
    expect(dialog).toBeInTheDocument();
  });
});

describe("Viewer support steps (WF-08/09, Figma §13.2)", () => {
  it("focus moves to the new step heading and the prompt is announced when a step advances", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    const first = await within(dialog).findByRole("heading", { name: "What is happening?" });
    await waitFor(() => expect(first).toHaveFocus());
    expect(dialog.querySelector('[aria-live="polite"]')).toHaveTextContent("What is happening?");
    await user.click(within(dialog).getByRole("button", { name: "Video will not start" }));
    const next = await within(dialog).findByRole("heading", { name: "Refresh the event page. Did that fix it?" });
    await waitFor(() => expect(next).toHaveFocus());
    expect(dialog.querySelector('[aria-live="polite"]')).toHaveTextContent("Refresh the event page. Did that fix it?");
  });

  it("Back, Start over and Stop viewer help send the deterministic commands", async () => {
    installFetch({
      "POST /sessions/s-1/support-flows": () => json(STEP2, 201),
      "POST /sessions/s-1/support-flows/f-1/steps": (init) => {
        const body = JSON.parse(init.body);
        if (body.command === "cancel") return json({ ...STEP2, status: "cancelled", step: null, resolution_code: "user_cancelled" });
        return json(body.command === "back" ? STEP1 : STEP2);
      },
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    const card = (await within(dialog).findByRole("heading", { name: /Refresh the event page/ })).closest("section");
    await user.click(within(card).getByRole("button", { name: "Start over" }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/steps")).at(-1).body.command).toBe("restart"));
    await user.click(await within(card).findByRole("button", { name: "Back" }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/steps")).at(-1).body.command).toBe("back"));
    expect(await within(card).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Stop viewer help" }));
    expect(await within(card).findByText("Viewer help stopped.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith("/steps")).at(-1).body).toMatchObject({ step_id: "symptom", command: "cancel" });
  });

  it("header Back steps back inside the flow, and leaves it from the first step (Figma §8)", async () => {
    installFetch({
      "POST /sessions/s-1/support-flows": () => json(STEP2, 201),
      "POST /sessions/s-1/support-flows/f-1/steps": (init) => {
        const body = JSON.parse(init.body);
        if (body.command === "cancel") return json({ ...STEP1, status: "cancelled", step: null, resolution_code: "user_cancelled" });
        return json(STEP1);
      },
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(within(dialog.querySelector(".zg-header")).queryByRole("button", { name: "Back" })).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    await within(dialog).findByRole("heading", { name: /Refresh the event page/ });
    const header = dialog.querySelector(".zg-header");
    await user.click(within(header).getByRole("button", { name: "Back" }));
    await within(dialog).findByRole("heading", { name: "What is happening?" });
    expect(calls.filter((c) => c.path.endsWith("/steps")).at(-1).body.command).toBe("back");
    await user.click(within(header).getByRole("button", { name: "Back" }));
    expect(await within(dialog).findByText("Viewer help stopped.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith("/steps")).at(-1).body.command).toBe("cancel");
    expect(within(header).queryByRole("button", { name: "Back" })).toBeNull();
  });

  it("C5: a playback step shows the server's status card", async () => {
    installFetch({
      "POST /sessions/s-1/support-flows": () => json({
        ...STEP1,
        status_card: { state: "degraded", summary: "Some viewers may see buffering.", checked_at: "2026-10-23T10:00:00Z", status_url: "/status" },
      }, 201),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    const card = await within(dialog).findByRole("region", { name: "ZoikoStream service status" });
    expect(within(card).getByText("Degraded performance")).toBeInTheDocument();
    expect(within(card).getByText("Some viewers may see buffering.")).toBeInTheDocument();
    expect(within(card).getByText(/^Checked /)).toBeInTheDocument();
    await user.click(within(card).getByRole("button", { name: "Open status page" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/status");
  });

  it("C3: replacing an active viewer flow asks first; confirming resends with confirm_replace", async () => {
    let n = 0;
    installFetch({
      "POST /sessions/s-1/support-flows": (init) => {
        n += 1;
        if (n === 1) return json(STEP2, 201);
        const body = JSON.parse(init.body);
        if (!body.confirm_replace) {
          return json({ code: "GUIDE_STATE_CONFLICT", detail: "Another viewer flow is active.", confirm_required: true, active_flow_type: "playback" }, 409);
        }
        return json({ ...STEP1, flow_id: "f-2", flow_type: "captions", title: "Captions help",
          step: { ...STEP1.step, step_id: "captions", prompt: "Are captions turned on in the player?" } }, 201);
      },
      "POST /sessions/s-1/messages": () => answer({ actions: [{ action_id: "captions-help", type: "start_support_flow", flow_type: "captions", label: "Captions help" }] }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    await within(dialog).findByRole("heading", { name: /Refresh the event page/ });
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "captions?{Enter}");
    await user.click(await within(dialog).findByRole("button", { name: "Captions help" }));
    const confirm = await within(dialog).findByRole("alertdialog", { name: "Switch to captions help?" });
    expect(within(confirm).getByText(/Your progress in playback help will be cleared/)).toBeInTheDocument();
    await user.click(within(confirm).getByRole("button", { name: "Keep current steps" }));
    expect(within(dialog).queryByRole("alertdialog")).toBeNull();
    expect(calls.filter((c) => c.path === "/sessions/s-1/support-flows")).toHaveLength(2);

    await user.click(within(dialog).getByRole("button", { name: "Captions help" }));
    await user.click(within(await within(dialog).findByRole("alertdialog")).getByRole("button", { name: "Switch topic" }));
    expect(await within(dialog).findByRole("heading", { name: "Are captions turned on in the player?" })).toBeInTheDocument();
    const starts = calls.filter((c) => c.path === "/sessions/s-1/support-flows");
    expect(starts.at(-1).body).toEqual({ flow_type: "captions", confirm_replace: true });
    expect(within(dialog).getByText("Continuing below.")).toBeInTheDocument(); // replaced flow
  });
});

describe("Organizer handoff (CF §25)", () => {
  it("Contact organizer requests the organizer handoff and shows its route; escalation is recorded", async () => {
    setGuideAnalyticsConsent("granted");
    const { events, stop } = collectEvents();
    installFetch({
      "POST /sessions/s-1/messages": () => answer({ actions: [{ action_id: "contact-organizer", type: "handoff", target: "organizer", label: "Contact organizer" }] }),
      "POST /sessions/s-1/handoffs": () => json({ target: "organizer", message: "Contact the event organizer using the details on your invitation.", actions: [] }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "I lost my invite{Enter}");
    await user.click(await within(dialog).findByRole("button", { name: "Contact organizer" }));
    expect(await within(dialog).findByText("Contact the event organizer using the details on your invitation.")).toBeInTheDocument();
    const h = calls.find((c) => c.path === "/sessions/s-1/handoffs");
    expect(h.body).toEqual({ target: "organizer" });
    expect(h.headers["Idempotency-Key"]).toBeTruthy();
    stop();
    const names = events.map((e) => e.event);
    expect(names).toContain("guide_escalation_selected");
    expect(names).toContain("guide_escalated"); // route shown (CF §30)
  });
});

describe("Lead form (WF-06, CF §8, Figma §11, §13)", () => {
  async function openDemo(user) {
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Request a demo" }));
    await within(dialog).findByLabelText(/Full name/);
    return dialog;
  }

  it("focuses the flow heading when the form opens", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openDemo(user);
    await waitFor(() => expect(within(dialog).getByRole("heading", { name: /Tell us how to reach you/ })).toHaveFocus());
  });

  it("CF-AT-10: a server field error is shown inline and every other value is kept", async () => {
    installFetch({
      "PATCH /sessions/s-1/lead-flows/l-1234567890": () =>
        json({ code: "GUIDE_VALIDATION_ERROR", detail: "One or more fields need attention.", field_errors: [{ field: "work_email", message: "Use a work email address." }] }, 422),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openDemo(user);
    await user.type(within(dialog).getByLabelText(/Full name/), "Jamie Rivera");
    await user.type(within(dialog).getByLabelText(/Work email/), "jamie@example.org");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    const email = within(dialog).getByLabelText(/Work email/);
    expect(await within(dialog).findByText("Use a work email address.")).toBeInTheDocument();
    expect(email).toHaveAttribute("aria-invalid", "true");
    expect(email).toHaveAttribute("aria-describedby", expect.stringContaining(`${email.id}-err`));
    await waitFor(() => expect(email).toHaveFocus());
    expect(within(dialog).getByLabelText(/Full name/)).toHaveValue("Jamie Rivera");
    expect(email).toHaveValue("jamie@example.org");
    expect(within(dialog).getByRole("checkbox")).toBeChecked();
  });

  it("more than one error shows an error summary that links to each field and takes focus", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openDemo(user);
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    const summary = await within(dialog).findByRole("alert", { name: "Please fix 3 fields:" });
    await waitFor(() => expect(summary).toHaveFocus());
    const links = within(summary).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([
      "Full name: This field is required.",
      "Work email: This field is required.",
      "Contact consent: Please confirm we may contact you about this request.",
    ]);
    await user.click(links[1]);
    expect(within(dialog).getByLabelText(/Work email/)).toHaveFocus();
  });

  it("failed submission says so, offers Try again and alternate routes, never success", async () => {
    installFetch({
      "PATCH /sessions/s-1/lead-flows/l-1234567890": () => json({ ...LEAD, status: "ready_to_submit" }),
      "PUT /sessions/s-1/consents/sales_contact": () => json({ receipt_id: "cns_1", status: "granted" }),
      "POST /sessions/s-1/lead-flows/l-1234567890/submit": () =>
        json({ outcome: "failed", message: "Your request didn't go through. You can try again or contact sales directly.", actions: [{ action_id: "contact-sales", type: "navigate", label: "Contact sales", href: "/contact" }] }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openDemo(user);
    await user.type(within(dialog).getByLabelText(/Full name/), "Jamie Rivera");
    await user.type(within(dialog).getByLabelText(/Work email/), "jamie@example.org");
    await user.click(within(dialog).getByRole("checkbox"));
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    await user.click(await within(dialog).findByRole("button", { name: "Submit demo request" }));
    expect(await within(dialog).findByText(/Your request didn't go through/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Contact sales" })).toBeInTheDocument();
    expect(within(dialog).queryByText("Request received")).toBeNull();
    const first = calls.find((c) => c.path.endsWith("/submit")).headers["Idempotency-Key"];
    await user.click(within(dialog).getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/submit"))).toHaveLength(2));
    expect(calls.filter((c) => c.path.endsWith("/submit"))[1].headers["Idempotency-Key"]).not.toBe(first); // a failed attempt gets a new key
  });

  it("C6: leaving a field saves the lead draft (debounced) without consent", async () => {
    installFetch({ "PATCH /sessions/s-1/leads/l-1234567890/draft": () => new Response(null, { status: 204 }) });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openDemo(user);
    await user.type(within(dialog).getByLabelText(/Full name/), "Jamie Rivera");
    await user.tab();
    await user.type(within(dialog).getByLabelText(/Work email/), "not-an-email");
    await user.tab();
    await waitFor(() => expect(calls.some((c) => c.path === "/sessions/s-1/leads/l-1234567890/draft")).toBe(true), { timeout: 2000 });
    const drafts = calls.filter((c) => c.path === "/sessions/s-1/leads/l-1234567890/draft");
    expect(drafts).toHaveLength(1); // both blurs coalesced into one save
    expect(drafts[0].method).toBe("PATCH");
    expect(drafts[0].headers["X-ZS-CSRF"]).toBe("csrf-1");
    expect(drafts[0].body).toEqual({ full_name: "Jamie Rivera" }); // invalid values are not saved
    expect(calls.some((c) => c.path.includes("/consents/"))).toBe(false);
  });

  it("header Back pauses the lead form with values kept; Continue request resumes it", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openDemo(user);
    await user.type(within(dialog).getByLabelText(/Full name/), "Jamie Rivera");
    expect(within(dialog).getByText("Request a demo", { selector: "#zg-title + p" })).toBeInTheDocument();
    await user.click(within(dialog.querySelector(".zg-header")).getByRole("button", { name: "Back" }));
    expect(await within(dialog).findByText("Your request is paused. Your details are kept.")).toBeInTheDocument();
    expect(within(dialog).getByText("Public AI Assistant", { selector: "#zg-title + p" })).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Continue request" }));
    expect(await within(dialog).findByLabelText(/Full name/)).toHaveValue("Jamie Rivera");
  });

  it("C7: paused lead capture shows the server's message and routes verbatim", async () => {
    installFetch({
      "POST /sessions/s-1/lead-flows": () => json({
        code: "GUIDE_UNAVAILABLE", detail: "Requests through Guide are paused.",
        paused: { feature: "leads", message: "Requests through Guide are paused. You can contact sales directly.",
          routes: [{ action_id: "contact-sales", type: "navigate", label: "Contact sales", href: "/contact" }] },
      }, 503),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Request a demo" }));
    expect(await within(dialog).findByText("Requests through Guide are paused. You can contact sales directly.")).toBeInTheDocument();
    expect(within(dialog).queryByText(/Guide is having trouble right now/)).toBeNull();
    await user.click(within(dialog).getByRole("button", { name: "Contact sales" }));
    expect(screen.getByTestId("where")).toHaveTextContent("/contact");
  });

  it("C7: paused viewer support resolves route ids against the direct routes", async () => {
    installFetch({
      "POST /sessions/s-1/support-flows": () => json({
        code: "GUIDE_UNAVAILABLE",
        paused: { feature: "viewer_support", message: "Viewer help in Guide is paused. Viewer support is still available directly.", routes: ["help-center", "status-page"] },
      }, 503),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    const text = await within(dialog).findByText("Viewer help in Guide is paused. Viewer support is still available directly.");
    const notice = text.closest(".zg-notice").parentElement;
    expect(within(notice).getByRole("button", { name: "Help Center" })).toBeInTheDocument();
    expect(within(notice).getByRole("button", { name: "Service status" })).toBeInTheDocument();
  });
});

describe("Streaming contract (C2) and clarification (C4)", () => {
  it("shows the progress stage as an accessible status line before text arrives", async () => {
    const enc = new TextEncoder();
    let ctrl;
    const stream = new ReadableStream({ start: (c) => { ctrl = c; } });
    const ev = (e, d) => ctrl.enqueue(enc.encode(`event: ${e}\ndata: ${JSON.stringify(d)}\n\n`));
    installFetch({
      "POST /sessions/s-1/messages": () => new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "what is it?{Enter}");
    expect(await within(dialog).findByText("Sending your question…")).toBeInTheDocument();
    ev("response.started", { assistant_message_id: "m-9" });
    ev("response.progress", { stage: "retrieving" });
    const status = await within(dialog).findByText("Searching ZoikoStream sources…");
    expect(status.closest('[role="status"]')).not.toBeNull();
    ev("response.progress", { stage: "checking" });
    expect(await within(dialog).findByText("Checking the answer against sources…")).toBeInTheDocument();
    ev("response.text.delta", { delta: "ZoikoStream is a live events platform." });
    ev("response.completed", { answer_class: "factual", grounded: true, session_state: "ACTIVE" });
    ctrl.close();
    await waitFor(() => expect(dialog.querySelector(".zg-answer")).toHaveTextContent("ZoikoStream is a live events platform."));
    expect(within(dialog).queryByText("Checking the answer against sources…")).toBeNull();
  });

  it("response.error keeps the message with Retry, reusing the accepted id", async () => {
    let n = 0;
    installFetch({
      "POST /sessions/s-1/messages": () =>
        ++n === 1
          ? sse([["response.started", { assistant_message_id: "m-1" }], ["response.progress", { stage: "retrieving" }], ["response.error", { code: "GUIDE_UNAVAILABLE", retryable: true }]])
          : answer(),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "Tell me more{Enter}");
    await user.click(await within(dialog).findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(dialog.querySelector(".zg-answer")).toHaveTextContent(/Families can stream/));
    const sent = calls.filter((c) => c.path === "/sessions/s-1/messages");
    expect(sent[1].body.client_message_id).toBe(sent[0].body.client_message_id);
  });

  it("response.error that is not retryable shows a stable error, not a retry loop", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => sse([["response.started", { assistant_message_id: "m-1" }], ["response.error", { code: "GUIDE_INTERNAL_ERROR", retryable: false }]]),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "Tell me more{Enter}");
    expect(await within(dialog).findByRole("alert")).toBeInTheDocument();
    expect(within(dialog).queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("C4: clarification options render as chips and send the chosen label", async () => {
    let n = 0;
    installFetch({
      "POST /sessions/s-1/messages": () =>
        ++n === 1
          ? answer({ message_id: "m-c", answer_class: "discovery", sources: [], text: "ZoikoStream can support memorial-style live events.",
            clarification: { question: "Which matters most for your event: live viewing, replay, or both?",
              options: [{ id: "live", label: "Live viewing" }, { id: "replay", label: "Replay" }, { id: "both", label: "Both" }] } })
          : answer(),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "We need to stream a memorial service{Enter}");
    const group = await within(dialog).findByRole("group", { name: "Which matters most for your event: live viewing, replay, or both?" });
    expect(within(group).getAllByRole("button").map((b) => b.textContent)).toEqual(["Live viewing", "Replay", "Both"]);
    await user.click(within(group).getByRole("button", { name: "Replay" }));
    await waitFor(() => expect(calls.filter((c) => c.path === "/sessions/s-1/messages")).toHaveLength(2));
    expect(calls.filter((c) => c.path === "/sessions/s-1/messages")[1].body.text).toBe("Replay");
    expect(within(group).getByRole("button", { name: "Replay" })).toHaveAttribute("aria-pressed", "true");
    expect(within(group).getByRole("button", { name: "Both" })).toBeDisabled();
  });
});

describe("Source chip variants (Figma §5 Guide/SourceChip)", () => {
  it("FreshnessWarning and External variants", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () => answer({
        sources: [
          { source_id: "old", title: "Old page", source_type: "product", version: "2025.01", updated_at: "2025-01-02", canonical_path: "/", freshness: "stale" },
          { source_id: "ext", title: "Partner docs", source_type: "developer", version: "1", updated_at: null, canonical_path: null, external: true, external_url: "https://docs.example.com/guide" },
          { source_id: "bad", title: "Bad link", source_type: "developer", version: "1", canonical_path: null, external: true, external_url: "javascript:alert(1)" },
        ],
      }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "hi{Enter}");
    const stale = await within(dialog).findByRole("button", { name: /Source: Old page/ });
    expect(stale).toHaveClass("zg-source--stale");
    expect(stale).toHaveTextContent("May be out of date");
    await user.click(stale);
    expect(within(dialog).getByText(/due for review and may be out of date/)).toBeInTheDocument();
    const ext = within(dialog).getByRole("button", { name: /Source: Partner docs, external site/ });
    await user.click(ext);
    const link = within(dialog).getByRole("link", { name: /Open public source/ });
    expect(link).toHaveAttribute("href", "https://docs.example.com/guide");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await user.click(within(dialog).getByRole("button", { name: /Source: Bad link/ }));
    expect(within(dialog).queryByRole("link", { name: /Open public source/ })).toBeNull();
  });
});

describe("Jump to latest (Figma §9.2)", () => {
  it("appears when the visitor scrolls up and returns to the newest content", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    const body = dialog.querySelector(".zg-body");
    Object.defineProperty(body, "scrollHeight", { configurable: true, value: 2000 });
    Object.defineProperty(body, "clientHeight", { configurable: true, value: 500 });
    body.scrollTop = 100;
    fireEvent.scroll(body);
    const jump = await within(dialog).findByRole("button", { name: "Jump to latest" });
    await user.click(jump);
    expect(body.scrollTop).toBe(2000);
    expect(within(dialog).queryByRole("button", { name: "Jump to latest" })).toBeNull();
  });
});

describe("Offline (Figma §5 ErrorState/Offline, §14)", () => {
  it("disables the composer while offline, keeps the draft, and recovers on reconnect", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    const input = within(dialog).getByLabelText("Ask Guide a question");
    await user.type(input, "half typed");
    setOnline(false);
    expect(await within(dialog).findByText("You're offline.")).toBeInTheDocument();
    expect(input).toBeDisabled();
    expect(input).toHaveValue("half typed");
    expect(within(dialog).getByRole("button", { name: "Product overview" })).toBeDisabled();
    setOnline(true);
    await waitFor(() => expect(input).not.toBeDisabled());
    expect(within(dialog).queryByText("You're offline.")).toBeNull();
  });

  it("opening offline creates no session, focuses the offline heading, and starts once back online", async () => {
    const user = userEvent.setup();
    renderWidget();
    const launcher = await screen.findByRole("button", { name: "Open ZoikoStream Guide" });
    setOnline(false);
    await user.click(launcher);
    const dialog = await screen.findByRole("dialog", { name: "Guide" });
    const heading = await within(dialog).findByRole("heading", { name: "You're offline." });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(calls.some((c) => c.path === "/sessions")).toBe(false);
    setOnline(true);
    expect(await within(dialog).findByRole("button", { name: "Product overview" })).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/sessions")).toHaveLength(1);
  });

  it("unavailable state moves focus to its error heading (Figma §13.2)", async () => {
    installFetch({ "POST /sessions": () => json({ code: "GUIDE_UNAVAILABLE" }, 503) });
    const user = userEvent.setup();
    renderWidget();
    await user.click(await screen.findByRole("button", { name: "Open ZoikoStream Guide" }));
    const dialog = await screen.findByRole("dialog", { name: "Guide" });
    const heading = await within(dialog).findByRole("heading", { name: "Guide is temporarily unavailable." });
    await waitFor(() => expect(heading).toHaveFocus());
  });
});

describe("Analytics consent (WF-12 A3, WF §7.1, UX-012)", () => {
  it("sends nothing until the site grants analytics consent", async () => {
    const { events, stop } = collectEvents();
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Product overview" }));
    await within(dialog).findByRole("button", { name: "Source: Use cases" });
    stop();
    expect(events).toEqual([]);
    expect(getGuideAnalyticsConsent()).toBe("unknown");
    expect(calls.find((c) => c.path === "/sessions").body.consent_snapshot).toEqual({ analytics: "unknown", consent_version: null });
  });

  it("reads the site consent event and reports consent_state on guide_opened", async () => {
    window.dispatchEvent(new CustomEvent("zoikostream:consent", { detail: { analytics: "granted" } }));
    const { events, stop } = collectEvents();
    const user = userEvent.setup();
    renderWidget();
    await openGuide(user);
    stop();
    expect(events.find((e) => e.event === "guide_opened")).toMatchObject({ consent_state: "granted" });
    expect(calls.find((c) => c.path === "/sessions").body.consent_snapshot.analytics).toBe("granted");
  });

  it("denied consent (site or Global Privacy Control) keeps analytics off without affecting Guide", async () => {
    setGuideAnalyticsConsent("granted");
    Object.defineProperty(navigator, "globalPrivacyControl", { configurable: true, value: true });
    const { events, stop } = collectEvents();
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Product overview" }));
    await within(dialog).findByRole("button", { name: "Source: Use cases" });
    stop();
    delete navigator.globalPrivacyControl;
    expect(events).toEqual([]);
  });

  it("reads Google Consent Mode from the data layer", () => {
    window.dataLayer = [["consent", "default", { analytics_storage: "denied" }], ["consent", "update", { analytics_storage: "granted" }]];
    expect(getGuideAnalyticsConsent()).toBe("granted");
    window.dataLayer = [["consent", "update", { analytics_storage: "denied" }]];
    expect(getGuideAnalyticsConsent()).toBe("denied");
    delete window.dataLayer;
  });

  it("CF §30 meanings: flow_resolved only on resolution, knowledge gap codes, unknown actions recorded not executed", async () => {
    setGuideAnalyticsConsent("granted");
    const { events, stop } = collectEvents();
    installFetch({
      "POST /sessions/s-1/support-flows/f-1/steps": () => json({ ...STEP1, status: "escalated", step: null, resolution_code: "attempts_exhausted", message: "Let's get you to support.", actions: [] }),
      "POST /sessions/s-1/messages": () => answer({ answer_class: "knowledge_gap", grounded: false, sources: [], missing_topic_code: "pricing_enterprise", intent_class: "PRICING",
        actions: [{ action_id: "wipe", type: "delete_account", label: "Delete my account" }] }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    await user.click(await within(dialog).findByRole("button", { name: "Video will not start" }));
    await waitFor(() => expect(dialog.querySelector(".zg-card .zg-notice")).toHaveTextContent("Let's get you to support."));
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "enterprise price?{Enter}");
    await waitFor(() => expect(events.some((e) => e.event === "guide_knowledge_gap")).toBe(true));
    await waitFor(() => expect(events.some((e) => e.event === "guide_unknown_action")).toBe(true));
    stop();
    const ended = events.find((e) => e.event === "guide_viewer_flow_completed");
    expect(ended.aliases).toEqual(["guide_escalated"]);
    expect(ended).toMatchObject({ outcome: "escalated", resolution_code: "attempts_exhausted" });
    expect(events.some((e) => e.event === "guide_flow_resolved" || e.aliases.includes("guide_flow_resolved"))).toBe(false);
    expect(events.find((e) => e.event === "guide_knowledge_gap")).toMatchObject({ intent_class: "PRICING", missing_topic_code: "pricing_enterprise" });
    expect(events.find((e) => e.event === "guide_unknown_action")).toMatchObject({ action_type: "delete_account" });
    expect(within(dialog).queryByText("Delete my account")).toBeNull();
    // A sales flow is not a demo start.
    expect(events.some((e) => e.event === "guide_demo_started")).toBe(false);
  });

  it("guide_flow_resolved fires on a resolved viewer flow", async () => {
    setGuideAnalyticsConsent("granted");
    const { events, stop } = collectEvents();
    installFetch({
      "POST /sessions/s-1/support-flows/f-1/steps": () => json({ ...STEP1, status: "resolved", step: null, resolution_code: "resolved_refresh", message: "Great — if anything else comes up, I can help." }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Viewer help" }));
    await user.click(await within(dialog).findByRole("button", { name: "Video will not start" }));
    await waitFor(() => expect(dialog.querySelector(".zg-card .zg-notice")).toHaveTextContent(/Great — if anything else comes up/));
    stop();
    expect(events.find((e) => e.event === "guide_viewer_flow_completed").aliases).toEqual(["guide_flow_resolved"]);
  });
});

describe("Mobile full-screen shell (WF-18, Figma §4.2, §13)", () => {
  let width;
  beforeEach(() => {
    width = window.innerWidth;
    window.innerWidth = 375;
    window.history.replaceState(null, "", "/");
  });
  afterEach(() => {
    window.innerWidth = width;
  });

  it("is aria-modal and Tab / Shift+Tab cycle inside Guide", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(dialog).toHaveAttribute("aria-modal", "true");
    const close = within(dialog).getByRole("button", { name: "Help Center" });
    close.focus();
    // Walk to the last focusable control, then one more Tab wraps to the first.
    const focusables = [...dialog.querySelectorAll('button:not([disabled]), textarea:not([disabled]), a[href]')];
    focusables.at(-1).focus();
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(document.activeElement).toBe(focusables[0]);
    await user.tab({ shift: true });
    expect(document.activeElement).toBe(focusables.at(-1));
  });

  it("header Back closes a nested source detail first", async () => {
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Product overview" }));
    await user.click(await within(dialog).findByRole("button", { name: "Source: Use cases" }));
    expect(within(dialog).getByText("Open public source")).toBeInTheDocument();
    await user.click(within(dialog.querySelector(".zg-header")).getByRole("button", { name: "Back" }));
    expect(within(dialog).queryByText("Open public source")).toBeNull();
    expect(screen.getByRole("dialog", { name: "Guide" })).toBeInTheDocument();
  });

  it("desktop is not modal", async () => {
    window.innerWidth = 1280;
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    expect(dialog).toHaveAttribute("aria-modal", "false");
  });
});

describe("Inline Ask Guide CTA and ?guide= deep link (WF §2.1)", () => {
  it("parses only known, non-sensitive values", () => {
    expect(parseGuideParam("open")).toEqual({ intentId: null });
    expect(parseGuideParam("viewer-help")).toEqual({ intentId: "I-VIEWER" });
    expect(parseGuideParam("Request-Demo")).toEqual({ intentId: "I-DEMO" });
    expect(parseGuideParam("my pin is 1234")).toBeNull();
    expect(parseGuideParam("__proto__")).toBeNull();
  });

  it("the CTA renders only while Guide is available, and opens it on the quick action", async () => {
    setGuideAvailable(false);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <AskGuideCta quickAction="viewer-help" label="Ask Guide about viewing" />
        <GuideWidget page={{ page_id: "home", page_type: "home" }} pathname="/" />
      </MemoryRouter>,
    );
    const cta = await screen.findByRole("button", { name: "Ask Guide about viewing" });
    await user.click(cta);
    const dialog = await screen.findByRole("dialog", { name: "Guide" });
    expect(await within(dialog).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/sessions").body.page_context.entry_point).toBe("inline"); // API EntryPoint
    expect(calls.some((c) => c.path.endsWith("/messages"))).toBe(false); // nothing auto-sent
  });

  it("a question intent opens Guide without sending anything", async () => {
    const user = userEvent.setup();
    renderWidget();
    await screen.findByRole("button", { name: "Open ZoikoStream Guide" });
    act(() => askGuide({ quickAction: "product-overview" }));
    const dialog = await screen.findByRole("dialog", { name: "Guide" });
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Product overview" })).toHaveFocus());
    expect(calls.some((c) => c.path.endsWith("/messages"))).toBe(false);
    void user;
  });

  it("?guide=viewer-help opens Guide, starts viewer help, and is removed from the URL", async () => {
    render(
      <MemoryRouter initialEntries={["/?guide=viewer-help&utm=x"]}>
        <Routes>
          <Route path="*" element={<><GuideMount /><Where /></>} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/\?utm=x$/));
    const dialog = await screen.findByRole("dialog", { name: "Zoiko Stream Guide" }, { timeout: 3000 });
    expect(await within(dialog).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/sessions").body.page_context.entry_point).toBe("inline");
    expect(calls.some((c) => c.path.endsWith("/messages"))).toBe(false);
  });

  it("an unknown ?guide= value is stripped and opens nothing", async () => {
    render(
      <MemoryRouter initialEntries={["/?guide=secret-link-abc"]}>
        <Routes>
          <Route path="*" element={<><GuideMount /><Where /></>} />
        </Routes>
      </MemoryRouter>,
    );
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent(/^\/$/));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("Integration seams (CF §10.1, Security §7)", () => {
  it("free text that starts a viewer flow renders its first step from the answer", async () => {
    installFetch({
      "POST /sessions/s-1/messages": () =>
        answer({ text: "Let's sort out playback.", support_flow: STEP1 }),
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.type(within(dialog).getByLabelText("Ask Guide a question"), "my video won't play{Enter}");
    expect(await within(dialog).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Video will not start" })).toBeInTheDocument();
  });

  it("a rotated CSRF token from the server is used on the next request", async () => {
    let n = 0;
    installFetch({
      "POST /sessions/s-1/messages": () => {
        n += 1;
        return n === 1 ? json({ session_state: "ACTIVE", assistant_message: MSG }, 200, { "X-ZS-CSRF-Rotated": "csrf-2" }) : answer();
      },
    });
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    const box = within(dialog).getByLabelText("Ask Guide a question");
    await user.type(box, "first{Enter}");
    await within(dialog).findAllByText(MSG.text);
    await user.type(box, "second{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.path === "/sessions/s-1/messages")).toHaveLength(2));
    const sent = calls.filter((c) => c.path === "/sessions/s-1/messages");
    expect(sent[0].headers["X-ZS-CSRF"]).toBe("csrf-1");
    expect(sent[1].headers["X-ZS-CSRF"]).toBe("csrf-2");
  });
});

describe("Full-page Guide entry", () => {
  it("the options menu opens the full Guide page", async () => {
    installFetch();
    const user = userEvent.setup();
    renderWidget();
    const dialog = await openGuide(user);
    await user.click(within(dialog).getByRole("button", { name: "Conversation options" }));
    await user.click(within(dialog).getByRole("menuitem", { name: /Open full page/ }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/guide"));
  });
});

describe("Overlay mode (launcher opens the full Guide design)", () => {
  function renderOverlay() {
    return render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="*" element={<GuideWidget page={{ page_id: "home", page_type: "home" }} pathname="/" variant="overlay" />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("opens the full design over the page without creating a session, and Close keeps the conversation", async () => {
    installFetch();
    const user = userEvent.setup();
    renderOverlay();
    await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
    const overlay = await screen.findByRole("dialog", { name: "Zoiko Stream Guide" });
    expect(within(overlay).getByRole("heading", { level: 1 })).toHaveTextContent("Your Guide to Live Events, Anytime.");
    expect(calls.some((c) => c.path === "/sessions")).toBe(false);
    await waitFor(() => expect(within(overlay).getByRole("button", { name: "Close Guide" })).toHaveFocus());

    await user.click(within(overlay).getByRole("button", { name: /Fix Playback Issues/ }));
    expect(await within(overlay).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();

    await user.click(within(overlay).getByRole("button", { name: "Close Guide" }));
    expect(screen.queryByRole("dialog", { name: "Zoiko Stream Guide" })).toBeNull();
    await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
    const again = await screen.findByRole("dialog", { name: "Zoiko Stream Guide" });
    expect(within(again).getByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/sessions")).toHaveLength(1);
  });

  it("Escape closes the overlay", async () => {
    installFetch();
    const user = userEvent.setup();
    renderOverlay();
    await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
    await screen.findByRole("dialog", { name: "Zoiko Stream Guide" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Zoiko Stream Guide" })).toBeNull());
  });
});

describe("Guide window controls (popup, maximize, minimize)", () => {
  function renderLauncher() {
    return render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="*" element={<GuideWidget page={{ page_id: "home", page_type: "home" }} pathname="/" variant="overlay" />} />
        </Routes>
      </MemoryRouter>,
    );
  }

  it("opens as a compact popup; maximize shows the full design, restore and minimize keep the conversation", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLauncher();
    await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
    const win = await screen.findByRole("dialog", { name: "Zoiko Stream Guide" });
    expect(win).toHaveClass("zgp-shell--popup");
    expect(win).toHaveAttribute("aria-modal", "false");
    expect(within(win).queryByRole("navigation", { name: "Site" })).toBeNull();

    await user.click(within(win).getByRole("button", { name: /Fix Playback Issues/ }));
    expect(await within(win).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();

    await user.click(within(win).getByRole("button", { name: "Maximize Guide" }));
    expect(win).toHaveClass("zgp-shell--max");
    expect(win).toHaveAttribute("aria-modal", "true");
    expect(within(win).getByRole("navigation", { name: "Site" })).toBeInTheDocument();
    expect(within(win).getByRole("heading", { name: "What is happening?" })).toBeInTheDocument();

    await user.click(within(win).getByRole("button", { name: "Restore Guide size" }));
    expect(win).toHaveClass("zgp-shell--popup");

    await user.click(within(win).getByRole("button", { name: "Minimize Guide" }));
    expect(screen.queryByRole("dialog", { name: "Zoiko Stream Guide" })).toBeNull();
    await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
    const again = await screen.findByRole("dialog", { name: "Zoiko Stream Guide" });
    expect(within(again).getByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    expect(calls.filter((c) => c.path === "/sessions")).toHaveLength(1);
  });

  it("Close returns a maximized window to popup size for the next open", async () => {
    installFetch();
    const user = userEvent.setup();
    renderLauncher();
    await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
    const win = await screen.findByRole("dialog", { name: "Zoiko Stream Guide" });
    await user.click(within(win).getByRole("button", { name: "Maximize Guide" }));
    await user.click(within(win).getByRole("button", { name: "Close Guide" }));
    await user.click(await screen.findByRole("button", { name: /open zoikostream guide/i }));
    expect(await screen.findByRole("dialog", { name: "Zoiko Stream Guide" })).toHaveClass("zgp-shell--popup");
  });
});
