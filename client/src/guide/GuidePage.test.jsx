// Full-page Guide (/guide): design content, no session until the first interaction, topics
// routed to the right engines, composer, and the unavailable state. Backend mocked at fetch.
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import GuidePage from "./GuidePage";
import { clearSession } from "./guideApi";

vi.mock("../auth/AuthContext", () => ({ useAuth: () => ({ user: null }) }));

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

const BOOT = {
  enabled: true,
  limits: { max_message_chars: 4000 },
  privacy: { privacy_notice_href: "/privacy" },
  direct_routes: [{ action_id: "contact-support", type: "open_help", label: "Contact support", href: "/contact" }],
  suggested_intents: [],
};
const SESSION = { session_id: "s-1", csrf_token: "csrf-1", session_state: "ACTIVE", welcome: "Welcome.", suggested_intents: [] };
const MSG = {
  message_id: "m-1", text: "ZoikoStream is a live-events streaming platform.", answer_class: "factual", grounded: true,
  uncertainty: { state: "none", message: null }, sources: [], actions: [],
};
const FLOW = {
  flow_id: "f-1", flow_type: "playback", title: "Playback help", status: "in_progress", resolution_code: null,
  message: null, notice: null, actions: [],
  step: { step_id: "symptom", prompt: "What is happening?", input_type: "choice",
    options: [{ option_id: "no_start", label: "Video will not start" }], allow_skip: false, allow_back: false },
};

let calls;
function installFetch(overrides = {}) {
  calls = [];
  globalThis.fetch = vi.fn(async (url, init = {}) => {
    const path = url.replace(/^.*\/api\/v1\/public-guide/, "");
    const key = `${init.method || "GET"} ${path.split("?")[0]}`;
    calls.push({ key, body: init.body ? JSON.parse(init.body) : undefined });
    if (overrides[key]) return overrides[key](init);
    if (key === "GET /bootstrap") return json(BOOT);
    if (key === "POST /sessions") return json(SESSION, 201);
    if (key === "POST /sessions/s-1/messages") return json({ session_state: "ACTIVE", assistant_message: MSG });
    if (key === "POST /sessions/s-1/support-flows") return json(FLOW, 201);
    return json({ code: "GUIDE_RESOURCE_NOT_FOUND" }, 404);
  });
}

function Where() {
  const { pathname } = useLocation();
  return <div data-testid="where">{pathname}</div>;
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/guide"]}>
      <Routes>
        <Route path="*" element={<><GuidePage /><Where /></>} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  clearSession();
  try {
    sessionStorage.clear();
  } catch {
    /* storage unavailable */
  }
});

describe("Guide page", () => {
  it("renders the design content and creates no session on load", async () => {
    installFetch();
    renderPage();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Your Guide to Live Events, Anytime.");
    expect(await screen.findByText("Online")).toBeInTheDocument();
    for (const title of ["Join or Access an Event", "Fix Playback Issues", "Captions & Accessibility",
      "Replay & Watch Again", "About Zoiko Stream", "Plans, Pricing or Request a Demo"]) {
      expect(screen.getByRole("button", { name: new RegExp(title.replace(/[&,]/g, ".")) })).toBeInTheDocument();
    }
    expect(screen.getByRole("heading", { name: "Why Viewers Choose Zoiko Stream" })).toBeInTheDocument();
    expect(screen.getByText(/do not share passwords, PINs, secret event links/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Attachments aren't supported in Guide" })).toBeDisabled();
    expect(screen.getByAltText("ZoikoStream")).toHaveAttribute("src", "/zoiko-logo-white.png");
    expect(calls.map((c) => c.key)).toEqual(["GET /bootstrap"]);
  });

  it("a viewer topic opens the session, then starts that flow", async () => {
    installFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Online");
    await user.click(screen.getByRole("button", { name: /Fix Playback Issues/ }));
    const conv = await screen.findByRole("region", { name: "Your conversation with Guide" });
    expect(await within(conv).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    const keys = calls.map((c) => c.key);
    expect(keys.indexOf("POST /sessions")).toBeLessThan(keys.indexOf("POST /sessions/s-1/support-flows"));
    expect(calls.find((c) => c.key === "POST /sessions").body.page_context).toMatchObject({ page_id: "guide", path: "/guide" });
    expect(calls.find((c) => c.key === "POST /sessions/s-1/support-flows").body).toMatchObject({ flow_type: "playback" });
  });

  it("the About topic asks Guide with its intent", async () => {
    installFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Online");
    await user.click(screen.getByRole("button", { name: /About Zoiko Stream/ }));
    const conv = await screen.findByRole("region", { name: "Your conversation with Guide" });
    expect(await within(conv).findByText(MSG.text)).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /sessions/s-1/messages").body).toMatchObject({ text: "What is ZoikoStream?", intent_hint: "I-PRODUCT" });
  });

  it("the composer sends the first message after opening the session", async () => {
    installFetch();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Online");
    await user.type(screen.getByLabelText("Ask Zoiko Stream Guide a question"), "Do you support captions?{Enter}");
    const conv = await screen.findByRole("region", { name: "Your conversation with Guide" });
    expect(await within(conv).findByText(MSG.text)).toBeInTheDocument();
    expect(within(conv).getByText("Do you support captions?")).toBeInTheDocument();
    expect(calls.find((c) => c.key === "POST /sessions/s-1/messages").body.text).toBe("Do you support captions?");
  });

  it("when Guide is switched off the page still renders and topics show the direct routes", async () => {
    installFetch({ "GET /bootstrap": () => json({ ...BOOT, enabled: false }) });
    const user = userEvent.setup();
    renderPage();
    expect(await screen.findByText("Unavailable")).toBeInTheDocument();
    expect(screen.getByLabelText("Ask Zoiko Stream Guide a question")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Join or Access an Event/ }));
    expect(await screen.findByRole("heading", { name: "Guide is temporarily unavailable." })).toBeInTheDocument();
    expect(calls.some((c) => c.key === "POST /sessions")).toBe(false);
  });

  it("Contact Support goes to the contact page", async () => {
    installFetch();
    const user = userEvent.setup();
    renderPage();
    await user.click(screen.getAllByRole("link", { name: /Contact Support/ })[0]);
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/contact"));
  });
});

describe("Homepage hand-off", () => {
  it("/guide?topic=playback starts that topic once and clears the parameter", async () => {
    installFetch();
    render(
      <MemoryRouter initialEntries={["/guide?topic=playback"]}>
        <Routes>
          <Route path="*" element={<><GuidePage /><Search /></>} />
        </Routes>
      </MemoryRouter>,
    );
    const conv = await screen.findByRole("region", { name: "Your conversation with Guide" });
    expect(await within(conv).findByRole("heading", { name: "What is happening?" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("search")).toHaveTextContent(/^$/));
    expect(calls.filter((c) => c.key === "POST /sessions/s-1/support-flows")).toHaveLength(1);
  });

  it("an unknown topic does nothing and creates no session", async () => {
    installFetch();
    render(
      <MemoryRouter initialEntries={["/guide?topic=nope"]}>
        <Routes>
          <Route path="*" element={<GuidePage />} />
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByText("Online");
    expect(calls.some((c) => c.key === "POST /sessions")).toBe(false);
  });

  it("the homepage band links every topic into /guide", async () => {
    const { default: GuideTopicsBand } = await import("./GuideTopicsBand");
    render(<MemoryRouter><GuideTopicsBand /></MemoryRouter>);
    expect(screen.getByRole("link", { name: /Fix Playback Issues/ })).toHaveAttribute("href", "/guide?topic=playback");
    expect(screen.getByRole("link", { name: /Plans, Pricing or Request a Demo/ })).toHaveAttribute("href", "/guide?topic=pricing");
    expect(screen.getByRole("link", { name: /Open Guide/ })).toHaveAttribute("href", "/guide");
    expect(screen.getAllByRole("link")).toHaveLength(7);
  });
});

function Search() {
  const { search } = useLocation();
  return <div data-testid="search">{search}</div>;
}
