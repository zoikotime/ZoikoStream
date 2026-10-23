// Settings › General › Custom Domain — saving a domain, through the real Settings page.
//
// Reported: entering a domain and saving showed "Couldn't save custom domains, custom domains
// are not available temporarily". Two facts produce exactly that wording:
//
//   * the server refuses every new domain with 503 "Custom domains are temporarily
//     unavailable." while the feature is switched off — fail-closed by design until a
//     certificate provider, a CNAME target that resolves and the platform hostnames are
//     configured (services/custom_domains.availability, SETUP_GUIDE "Custom Domains Setup").
//     No such configuration exists in this repository's deploy config or local .env.
//   * until a60bfa6 (2026-09-28) the page-wide Save PATCHed /organization/domain along with
//     every other section and reported "Couldn't save Custom domain — <server detail>".
//
// The current page already behaves correctly; these pin it. With the feature off it offers no
// domain to save and says why, and no other Save sends the domain. With it on, a save goes to
// PATCH /organization/domain as {domain}, success is said only after the server answered, a
// refusal is shown in the server's words without losing what was typed, and a saved domain is
// what the page shows when it is opened again. The server side (401/403/422/503 and the save
// itself) is pinned by server/test_custom_domain_lifecycle.py.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
import { notify } from "../../ui/Toast";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";

const UNAVAILABLE = "Custom domains are temporarily unavailable.";
// GET /organization/domain exactly as the real route answers with no provider configured.
const OFF = { domain: null, domain_verified: false, status: "not_configured", available: false, cname_target: null, dns_records: [], error: null, check: null, can_verify: false };
const ON = { ...OFF, available: true, cname_target: "cname.zoikostream.com" };
const PENDING = {
  ...ON, domain: "events.acme.com", status: "pending_dns", can_verify: true, requested_at: "2026-10-22T10:00:00Z",
  dns_records: [
    { type: "CNAME", name: "events.acme.com", value: "cname.zoikostream.com", purpose: "Routes your event pages to ZoikoStream" },
    { type: "TXT", name: "_zoikostream.events.acme.com", value: "zoikostream-domain-verification=" + "a".repeat(64), purpose: "Proves your organization owns this domain" },
  ],
};
const PAYLOADS = {
  "/organization/profile": { name: "Acme", slug: "acme", website: "", support_email: "", industry: null, company_size: null, description: "" },
  "/organization/security": { require_2fa: false, enforce_sso: false, min_password_length: 8, session_timeout: "8 hours", allowed_domains: "" },
  "/organization/notifications": { event_scheduled: true },
  "/organization/branding": { primary_color: "violet", logo_url: null },
  "/organization/notifications/catalog": [],
};

let domain;
beforeEach(() => {
  vi.clearAllMocks();
  domain = ON;
  vi.mocked(api.get).mockImplementation((url) =>
    url === "/organization/domain" ? Promise.resolve({ data: domain })
      : url in PAYLOADS ? Promise.resolve({ data: PAYLOADS[url] }) : Promise.reject(new Error("404")));
  vi.mocked(api.patch).mockImplementation((url, body) => Promise.resolve({ data: { ...PAYLOADS[url], ...body } }));
});

const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/settings?tab=general"]}>
        <Settings />
      </MemoryRouter>
    </ThemeProvider>
  );
const panel = async () => (await screen.findByRole("heading", { name: "Custom Domain" })).closest("div").parentElement;
const domainPatches = () => vi.mocked(api.patch).mock.calls.filter(([url]) => url === "/organization/domain");
const type = (p, value) => fireEvent.change(within(p).getByLabelText("Domain"), { target: { value } });

describe("with custom domains switched off on the server", () => {
  it("offers no domain to save and says why", async () => {
    domain = OFF;
    open();
    const p = await panel();
    expect(within(p).getByRole("status")).toHaveTextContent(UNAVAILABLE);
    expect(within(p).queryByLabelText("Domain")).toBeNull();
    expect(within(p).queryByRole("button", { name: /add custom domain|save new domain/i })).toBeNull();
  });

  it("saving another section never sends the domain (the retired page-wide save did)", async () => {
    domain = OFF;
    open();
    await panel();
    const description = (await screen.findByText("Description")).parentElement.querySelector("textarea");
    fireEvent.change(description, { target: { value: "We stream things" } });
    fireEvent.click(screen.getByRole("button", { name: "Save organization profile" }));
    await waitFor(() => expect(notify.success).toHaveBeenCalledWith("Organization profile saved"));
    expect(domainPatches()).toHaveLength(0);
    expect(notify.error).not.toHaveBeenCalled();
  });
});

describe("with custom domains available", () => {
  it("sends {domain} to PATCH /organization/domain and says so only after the server answered", async () => {
    let answer;
    vi.mocked(api.patch).mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    open();
    const p = await panel();
    type(p, "events.acme.com");
    fireEvent.click(within(p).getByRole("button", { name: "Add Custom Domain" }));

    // A second press lands on a disabled button (and a browser will not implicitly submit a
    // form whose submit button is disabled — checked in Chromium, Enter included).
    const saving = within(p).getByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    fireEvent.click(saving);
    fireEvent.click(saving);
    expect(domainPatches()).toEqual([["/organization/domain", { domain: "events.acme.com" }]]);
    expect(notify.success).not.toHaveBeenCalled();

    await act(async () => answer({ data: PENDING }));
    expect(await within(p).findByText("Pending DNS verification")).toBeInTheDocument();
    expect(notify.success).toHaveBeenCalledTimes(1);
    expect(notify.success).toHaveBeenCalledWith("events.acme.com added — add the DNS records to verify it");
    expect(within(p).getByText("_zoikostream.events.acme.com")).toBeInTheDocument();
    expect(within(p).queryByRole("button", { name: "Saving…" })).toBeNull();
  });

  it("shows the saved domain when Settings is opened again", async () => {
    domain = PENDING;                     // what GET /organization/domain returns after the save
    open();
    const p = await panel();
    expect(within(p).getByText("events.acme.com", { selector: "span" })).toBeInTheDocument();
    expect(within(p).getByText("Pending DNS verification")).toBeInTheDocument();
    expect(within(p).getByRole("button", { name: /Verify now/ })).toBeEnabled();
  });

  it.each([
    ["the feature was switched off since the page loaded", 503, UNAVAILABLE, UNAVAILABLE],
    ["another organization holds the domain", 409, "This custom domain is already registered to another organization.", "This custom domain is already registered to another organization."],
    ["the hostname is not valid", 422,
      [{ type: "value_error", loc: ["body", "domain"], msg: "Value error, Enter the hostname only, like events.yourcompany.com — no https://, path or port." }],
      "Enter the hostname only, like events.yourcompany.com — no https://, path or port."],
    ["the caller is not an org admin", 403, "org_admin access required", "org_admin access required"],
  ])("when %s: the server's words, no success, nothing lost, ready to retry", async (_, status, detail, shown) => {
    vi.mocked(api.patch).mockRejectedValue({ response: { status, data: { detail } } });
    open();
    const p = await panel();
    type(p, "https://events.acme.com/x");
    fireEvent.click(within(p).getByRole("button", { name: "Add Custom Domain" }));

    await waitFor(() => expect(notify.error).toHaveBeenCalledWith(shown));
    expect(within(p).getByText(shown)).toBeInTheDocument();
    expect(notify.success).not.toHaveBeenCalled();
    expect(within(p).getByLabelText("Domain")).toHaveValue("https://events.acme.com/x");
    expect(within(p).getByRole("button", { name: "Add Custom Domain" })).toBeEnabled();
    expect(within(p).queryByText("Pending DNS verification")).toBeNull();
    expect(domainPatches()).toHaveLength(1);
  });
});
