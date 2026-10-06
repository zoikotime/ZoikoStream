// The Custom Domain panel renders the server's lifecycle and nothing of its own invention.
//
// It used to show "Pending" with no domain at all, flip to "Pending" locally as anyone typed,
// copy a hardcoded CNAME target that did not exist in DNS, and promise that "support" would
// verify — with no support tool behind it. Every state below is a server payload
// (server/app/services/custom_domains.py public_view).
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), info: vi.fn() } }));

import api from "../../api";
import { notify } from "../../ui/Toast";
import CustomDomainPanel from "./CustomDomainPanel";

const TOKEN = "zoikostream-domain-verification=" + "a".repeat(64);
const RECORDS = [
  { type: "CNAME", name: "events.acme.com", value: "cname.zoikostream.com", purpose: "Routes your event pages to ZoikoStream" },
  { type: "TXT", name: "_zoikostream.events.acme.com", value: TOKEN, purpose: "Proves your organization owns this domain" },
];
const NONE = { domain: null, domain_verified: false, status: "not_configured", available: true, cname_target: "cname.zoikostream.com", dns_records: [], error: null, check: null, can_verify: false };
const PENDING = { ...NONE, domain: "events.acme.com", status: "pending_dns", dns_records: RECORDS, can_verify: true, requested_at: "2026-10-06T10:00:00Z" };
const VERIFIED = { ...PENDING, status: "verified", domain_verified: true, certificate_status: "pending_validation", check: { cname_ok: true, txt_ok: true, cname_found: "cname.zoikostream.com", txt_present: true } };
const ACTIVE = { ...VERIFIED, status: "active", certificate_status: "active", public_url: "https://events.acme.com" };

let writeText;
beforeEach(() => {
  vi.clearAllMocks();
  writeText = vi.fn(() => Promise.resolve());
  Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true, writable: true });
});
afterEach(() => vi.restoreAllMocks());

const panel = (initial) => render(<CustomDomainPanel initial={initial} />);

describe("no domain", () => {
  it("offers Add Custom Domain and shows no status at all", () => {
    panel(NONE);
    expect(screen.getByRole("button", { name: "Add Custom Domain" })).toBeDisabled();
    expect(screen.queryByText(/Pending/)).toBeNull();
    expect(screen.queryByText(/completed by support/)).toBeNull();
  });

  it("adds a domain through the domain endpoint and then shows the records to publish", async () => {
    vi.mocked(api.patch).mockResolvedValue({ data: PENDING });
    panel(NONE);
    fireEvent.change(screen.getByPlaceholderText("events.yourcompany.com"), { target: { value: "events.acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Add Custom Domain" }));
    await screen.findByText("Pending DNS verification");
    expect(api.patch).toHaveBeenCalledWith("/organization/domain", { domain: "events.acme.com" });
    expect(screen.getByText(TOKEN)).toBeInTheDocument();
  });

  it("shows a refusal on the field in the server's words", async () => {
    vi.mocked(api.patch).mockRejectedValue({ response: { status: 409, data: { detail: "This domain is already registered to another ZoikoStream organization. If your organization owns it, contact support." } } });
    panel(NONE);
    fireEvent.change(screen.getByPlaceholderText("events.yourcompany.com"), { target: { value: "events.taken.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Add Custom Domain" }));
    expect(await screen.findByText(/already registered to another ZoikoStream organization/)).toBeInTheDocument();
    expect(screen.getByPlaceholderText("events.yourcompany.com")).toHaveValue("events.taken.com");
  });
});

describe("unavailable", () => {
  it("says so instead of giving DNS instructions that point nowhere", () => {
    panel({ ...NONE, available: false, cname_target: null });
    expect(screen.getByRole("status")).toHaveTextContent("Custom domains are temporarily unavailable.");
    expect(screen.queryByRole("button", { name: "Add Custom Domain" })).toBeNull();
    expect(screen.queryByText(/cname\.zoikostream\.com/)).toBeNull();
  });

  it("still lets an existing domain be removed", () => {
    panel({ ...PENDING, available: false, dns_records: [], can_verify: false });
    expect(screen.queryByRole("button", { name: /Verify now/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Remove domain/ })).toBeEnabled();
  });
});

describe("pending DNS", () => {
  it("lists the CNAME and TXT records exactly as the server sent them", () => {
    panel(PENDING);
    const table = screen.getByRole("table");
    expect(within(table).getByText("events.acme.com")).toBeInTheDocument();
    expect(within(table).getByText("cname.zoikostream.com")).toBeInTheDocument();
    expect(within(table).getByText("_zoikostream.events.acme.com")).toBeInTheDocument();
    expect(within(table).getByText(TOKEN)).toBeInTheDocument();
    expect(screen.getByText("Pending DNS verification")).toBeInTheDocument();
  });

  it("copies the CNAME target and the TXT value", async () => {
    panel(PENDING);
    fireEvent.click(screen.getByRole("button", { name: "Copy CNAME" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("cname.zoikostream.com"));
    fireEvent.click(screen.getByRole("button", { name: "Copy TXT" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(TOKEN));
  });

  it("Verify now asks the server and shows what it found", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: VERIFIED });
    panel(PENDING);
    fireEvent.click(screen.getByRole("button", { name: /Verify now/ }));
    expect(await screen.findByText(/DNS verified\. Secure certificate is being provisioned/)).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith("/organization/domain/verify");
    expect(screen.getAllByText("Found")).toHaveLength(2);
  });

  it("explains exactly what is wrong when verification fails", async () => {
    const message = "Ownership TXT record not found at _zoikostream.events.acme.com. Add it exactly as shown. DNS changes can take up to 48 hours to propagate.";
    vi.mocked(api.post).mockResolvedValue({ data: { ...PENDING, error: { code: "txt_missing", message }, check: { cname_ok: true, txt_ok: false } } });
    panel(PENDING);
    fireEvent.click(screen.getByRole("button", { name: /Verify now/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByText("Found")).toBeInTheDocument();          // the CNAME
    expect(screen.getByText("Not found")).toBeInTheDocument();      // the TXT
  });
});

describe("certificate pending, active, failed, disabled", () => {
  it("certificate pending", () => {
    panel(VERIFIED);
    expect(screen.getByText(/DNS verified\. Secure certificate is being provisioned \(pending validation\)/)).toBeInTheDocument();
  });

  it("active shows the address event pages now use", () => {
    panel(ACTIVE);
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("https://events.acme.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Change domain/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Remove domain/ })).toBeInTheDocument();
  });

  it("an active domain whose records vanished says when it stops serving", () => {
    panel({ ...ACTIVE, error: { code: "dns_changed", message: "The DNS records for events.acme.com changed or were removed." }, deactivates_at: "2026-10-09T10:00:00Z" });
    expect(screen.getByRole("alert")).toHaveTextContent(/changed or were removed/);
    expect(screen.getByRole("alert")).toHaveTextContent(/Stops serving on/);
  });

  it("a certificate failure is named", () => {
    panel({ ...VERIFIED, status: "failed", error: { code: "certificate_failed", message: "Secure certificate provisioning failed." } });
    expect(screen.getByText("Action needed")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Secure certificate provisioning failed.");
  });

  it("a disabled domain cannot be verified from here", () => {
    panel({ ...PENDING, status: "disabled", dns_records: [], can_verify: false });
    expect(screen.getByText(/ZoikoStream support disabled this domain/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Verify now/ })).toBeNull();
  });
});

describe("change and remove", () => {
  it("changing an active domain warns first, then sends only the new hostname", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.patch).mockResolvedValue({ data: { ...PENDING, domain: "live.acme.com" } });
    panel(ACTIVE);
    fireEvent.click(screen.getByRole("button", { name: /Change domain/ }));
    fireEvent.change(screen.getByPlaceholderText("events.yourcompany.com"), { target: { value: "live.acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new domain" }));
    await screen.findByText("live.acme.com");
    expect(confirm.mock.calls[0][0]).toMatch(/stop being served from events\.acme\.com/);
    expect(api.patch).toHaveBeenCalledWith("/organization/domain", { domain: "live.acme.com" });
  });

  it("declining the warning changes nothing", () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    panel(ACTIVE);
    fireEvent.click(screen.getByRole("button", { name: /Change domain/ }));
    fireEvent.change(screen.getByPlaceholderText("events.yourcompany.com"), { target: { value: "live.acme.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Save new domain" }));
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("removing confirms, deletes, and returns to the empty state", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(api.delete).mockResolvedValue({ data: NONE });
    panel(ACTIVE);
    fireEvent.click(screen.getByRole("button", { name: /Remove domain/ }));
    expect(await screen.findByRole("button", { name: "Add Custom Domain" })).toBeInTheDocument();
    expect(api.delete).toHaveBeenCalledWith("/organization/domain");
    expect(notify.success).toHaveBeenCalledWith("Custom domain removed");
  });
});
