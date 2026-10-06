// Organization Settings: each panel saves only its own section, only its changed fields, and
// keeps its own failure. The page used to PATCH all five sections on every Save, so a
// Cloudflare 525 on two of those requests surfaced as "Couldn't save Branding, Custom domain —
// The SSL/TLS handshake between Cloudflare and the origin server failed…" against panels the
// reader had not touched.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

// The REAL errMsg: how an edge error is worded is part of what is under test.
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
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";

const PAYLOADS = {
  "/organization/profile": { name: "Acme", slug: "acme", website: "", support_email: "", industry: null, company_size: null, description: "" },
  "/organization/security": { require_2fa: false, enforce_sso: false, min_password_length: 8, session_timeout: "8 hours", allowed_domains: "" },
  "/organization/notifications": { event_scheduled: true, event_starting: false, recording_ready: false, weekly_summary: false, billing: true, mentions: false, member_joined: false, security_alerts: true },
  // The custom domain is its own component with its own lifecycle actions
  // (CustomDomainPanel.test.jsx); here it is only a neighbour that must not be disturbed.
  "/organization/domain": {
    domain: "events.acme.com", domain_verified: true, status: "active", available: true,
    cname_target: "cname.zoikostream.com", dns_records: [], error: null, check: null,
    public_url: "https://events.acme.com", can_verify: true,
  },
  "/organization/branding": { primary_color: "violet", logo_url: null },
  // The Notifications panel renders only from the server's catalog (no catalog, no switches),
  // so it is supplied here exactly as production serves it: the three configurable rows.
  "/organization/notifications/catalog": ["event_scheduled", "member_joined", "recording_ready"].map((key) => ({
    key, label: key, description: "", message_class: "B", mandatory: false, configurable: true,
    available: true, scope: "organization", channel: "email", value: true,
  })),
};

const CLOUDFLARE_525 = {
  response: {
    status: 525,
    data: {
      title: "Error 525: SSL handshake failed",
      detail: "The SSL/TLS handshake between Cloudflare and the origin server failed. The origin's SSL configuration is not compatible with Cloudflare, possibly due to missing shared cipher suites or an unsupported TLS version.",
    },
  },
};

const open = (tab) =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[`/organization/settings?tab=${tab}`]}>
        <Settings />
      </MemoryRouter>
    </ThemeProvider>
  );

const patches = () => vi.mocked(api.patch).mock.calls;
const status = (id) => screen.getByTestId(`section-status-${id}`);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) =>
    url in PAYLOADS ? Promise.resolve({ data: PAYLOADS[url] }) : Promise.reject(new Error("404")));
  vi.mocked(api.patch).mockImplementation((url, body) =>
    Promise.resolve({ data: { ...PAYLOADS[url], ...body } }));
});

describe("each section saves only itself", () => {
  it("Branding sends only the changed branding field and never touches the domain", async () => {
    open("branding");
    fireEvent.click(await screen.findByRole("button", { name: "indigo" }));
    fireEvent.click(screen.getByRole("button", { name: "Save branding" }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual(["/organization/branding", { primary_color: "indigo" }]);
    expect(notify.success).toHaveBeenCalledWith("Branding saved");
  });

  it("Notifications sends only the toggled preference", async () => {
    open("notifications");
    const [first] = await screen.findAllByRole("switch");
    fireEvent.click(first);
    fireEvent.click(screen.getByRole("button", { name: "Save notifications" }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual(["/organization/notifications", { event_scheduled: false }]);
  });

  it("General sends only the edited profile field, not the custom domain", async () => {
    open("general");
    const description = (await screen.findByText("Description")).parentElement.querySelector("textarea");
    fireEvent.change(description, { target: { value: "We stream things" } });
    fireEvent.click(screen.getByRole("button", { name: "Save organization profile" }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual(["/organization/profile", { description: "We stream things" }]);
    expect(patches().some(([url]) => url === "/organization/domain")).toBe(false);
  });

  it("a changed custom domain is sent to the domain endpoint alone", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    open("general");
    fireEvent.click(await screen.findByRole("button", { name: /Change domain/ }));
    fireEvent.change(screen.getByPlaceholderText("events.yourcompany.com"), { target: { value: "stream.acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new domain" }));
    await waitFor(() => expect(patches()).toHaveLength(1));
    expect(patches()[0]).toEqual(["/organization/domain", { domain: "stream.acme.com" }]);
    window.confirm.mockRestore();
  });

  it("an empty custom domain cannot be submitted", async () => {
    open("general");
    fireEvent.click(await screen.findByRole("button", { name: /Change domain/ }));
    const save = screen.getByRole("button", { name: "Save new domain" });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(patches()).toHaveLength(0);
  });

  it("a section edited and then restored is no longer dirty", async () => {
    open("branding");
    fireEvent.click(await screen.findByRole("button", { name: "indigo" }));
    expect(screen.getByRole("button", { name: "Save branding" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "violet" }));
    expect(screen.getByRole("button", { name: "Save branding" })).toBeDisabled();
  });
});

describe("a failure stays in its own section", () => {
  it("branding saves while an invalid domain is refused, and only the domain shows it", async () => {
    vi.mocked(api.patch).mockImplementation((url, body) =>
      url === "/organization/domain"
        ? Promise.reject({ response: { status: 422, data: { detail: [{ loc: ["body", "domain"], msg: "Value error, Enter a valid hostname, like events.yourcompany.com." }] } } })
        : Promise.resolve({ data: { ...PAYLOADS[url], ...body } }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    open("general");
    fireEvent.click(await screen.findByRole("button", { name: /Change domain/ }));
    const domain = screen.getByPlaceholderText("events.yourcompany.com");
    fireEvent.change(domain, { target: { value: "not a host" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new domain" }));

    // On the field, in the server's words, without the pydantic prefix.
    expect(await screen.findByText("Enter a valid hostname, like events.yourcompany.com.")).toBeInTheDocument();
    expect(within(status("profile")).queryByRole("alert")).toBeNull();
    expect(domain).toHaveValue("not a host");                                       // input kept
    window.confirm.mockRestore();

    // Switch to Branding (tab state is the URL; the Settings component stays mounted).
    fireEvent.click(screen.getByRole("button", { name: /Branding/ }));
    fireEvent.click(await screen.findByRole("button", { name: "indigo" }));
    fireEvent.click(screen.getByRole("button", { name: "Save branding" }));
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Branding saved"));
    expect(within(status("branding")).queryByRole("alert")).toBeNull();
    expect(patches().map(([u]) => u)).toEqual(["/organization/domain", "/organization/branding"]);
  });

  it("a Cloudflare edge error is reworded, not relayed", async () => {
    vi.mocked(api.patch).mockRejectedValue(CLOUDFLARE_525);
    open("branding");
    fireEvent.click(await screen.findByRole("button", { name: "indigo" }));
    fireEvent.click(screen.getByRole("button", { name: "Save branding" }));
    const alert = await within(status("branding")).findByRole("alert");
    expect(alert).toHaveTextContent(/edge error 525/);
    expect(alert).toHaveTextContent(/nothing was changed/);
    expect(alert.textContent).not.toMatch(/Cloudflare|cipher|handshake between/i);
    const [toastText] = vi.mocked(notify.error).mock.calls.at(-1);
    expect(toastText).toMatch(/^Branding not saved: /);
    expect(toastText).not.toMatch(/Cloudflare|cipher|Custom domain/i);
  });

  it("keeps what was typed after a failure and lets it be retried", async () => {
    vi.mocked(api.patch).mockRejectedValueOnce(CLOUDFLARE_525);
    open("branding");
    const logo = await screen.findByPlaceholderText("https://cdn.yourcompany.com/logo.png");
    fireEvent.change(logo, { target: { value: "https://cdn.acme.com/logo.png" } });
    fireEvent.click(screen.getByRole("button", { name: "Save branding" }));
    await within(status("branding")).findByRole("alert");
    expect(logo).toHaveValue("https://cdn.acme.com/logo.png");
    expect(screen.getByRole("button", { name: "Save branding" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Save branding" }));
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Branding saved"));
    expect(patches()).toHaveLength(2);
  });
});

describe("saving state", () => {
  it("shows Saving… and sends one request however often Save is clicked", async () => {
    let finish;
    vi.mocked(api.patch).mockImplementation(() => new Promise((res) => { finish = res; }));
    open("branding");
    fireEvent.click(await screen.findByRole("button", { name: "indigo" }));
    const save = screen.getByRole("button", { name: "Save branding" });
    fireEvent.click(save);
    fireEvent.click(save);
    fireEvent.click(save);
    expect(patches()).toHaveLength(1);
    expect(save).toHaveTextContent("Saving…");
    expect(save).toBeDisabled();
    finish({ data: { ...PAYLOADS["/organization/branding"], primary_color: "indigo" } });
    await waitFor(() => expect(save).toHaveTextContent("Save"));
    expect(save).toBeDisabled();                    // saved: nothing left to save
  });

  it("names every section with unsaved edits in the header", async () => {
    open("branding");
    fireEvent.click(await screen.findByRole("button", { name: "indigo" }));
    fireEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    fireEvent.click((await screen.findAllByRole("switch"))[0]);
    expect(screen.getByTestId("unsaved-summary")).toHaveTextContent("Branding, Notifications");
  });
});
