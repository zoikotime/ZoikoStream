// Support console for custom domains: staff see the evidence and can re-check, disable,
// re-enable or release — and there is no control that marks a domain verified.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("react-hot-toast", () => ({ default: { success: vi.fn(), error: vi.fn() } }));

import api from "../../api";
import toast from "react-hot-toast";
import CustomDomains from "./CustomDomains";

const ROW = {
  org_id: "org-a", organization: "Acme", domain: "events.acme.com", status: "pending_dns",
  cname_ok: true, cname_found: "cname.zoikostream.com", txt_ok: false, txt_present: true,
  certificate_status: null, custom_hostname_status: null, custom_hostname_id: null,
  error_code: "txt_mismatch", error: "A TXT record exists at _zoikostream.events.acme.com, but its value doesn't match.",
  requested_at: "2026-10-06T10:00:00Z", verified_at: null, activated_at: null, last_checked_at: "2026-10-06T10:05:00Z",
  failing_since: null, deactivates_at: null,
};
const PAYLOAD = {
  availability: { available: true, reason: null, cname_target: "cname.zoikostream.com", provider: "cloudflare" },
  items: [ROW, { ...ROW, org_id: "org-b", organization: "Beta", domain: "live.beta.io", status: "disabled", error: null, error_code: null }],
};

const open = () => render(<MemoryRouter><CustomDomains /></MemoryRouter>);

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: PAYLOAD });
  vi.mocked(api.post).mockResolvedValue({ data: {} });
});

describe("the console", () => {
  it("shows each request with its DNS, ownership and failure evidence", async () => {
    open();
    const row = (await screen.findByText("events.acme.com")).closest("tr");
    expect(within(row).getByText("Acme")).toBeInTheDocument();
    expect(within(row).getByText("Pending DNS")).toBeInTheDocument();
    expect(within(row).getByText(/Routed/)).toBeInTheDocument();
    expect(within(row).getByText(/Wrong value/)).toBeInTheDocument();
    expect(within(row).getByText(/its value doesn't match/)).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("cname.zoikostream.com");
  });

  it("explains why the feature is unavailable", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: { availability: { available: false, reason: "cname_target_unresolved" }, items: [] } });
    open();
    expect(await screen.findByRole("status")).toHaveTextContent(/does not resolve in public DNS/);
    expect(screen.getByText("No organization has requested a custom domain")).toBeInTheDocument();
  });

  it("re-runs the checks for one organization", async () => {
    open();
    fireEvent.click(await screen.findByTitle("Re-run checks for events.acme.com"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/custom-domains/org-a/verify"));
    expect(toast.success).toHaveBeenCalledWith("Checked events.acme.com");
  });

  it("disabling asks for a reason and sends it", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("abuse report");
    open();
    fireEvent.click(await screen.findByTitle("Disable events.acme.com"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/custom-domains/org-a/disable", { reason: "abuse report" }));
  });

  it("a cancelled prompt does nothing", async () => {
    vi.spyOn(window, "prompt").mockReturnValue(null);
    open();
    fireEvent.click(await screen.findByTitle("Release events.acme.com"));
    expect(api.post).not.toHaveBeenCalled();
  });

  it("a disabled domain is re-enabled, not re-verified", async () => {
    open();
    await screen.findByText("live.beta.io");
    expect(screen.queryByTitle("Re-run checks for live.beta.io")).toBeNull();
    fireEvent.click(screen.getByTitle("Re-enable live.beta.io"));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/admin/custom-domains/org-b/enable"));
  });

  it("surfaces the server's refusal, e.g. elevation required", async () => {
    vi.spyOn(window, "prompt").mockReturnValue("abuse report");
    vi.mocked(api.post).mockRejectedValue({ response: { status: 403, data: { detail: "This action needs elevated access. Start an elevation session and try again." } } });
    open();
    fireEvent.click(await screen.findByTitle("Disable events.acme.com"));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("This action needs elevated access. Start an elevation session and try again."));
  });

  it("offers no way to mark a domain verified", async () => {
    open();
    await screen.findByText("events.acme.com");
    expect(screen.queryByRole("button", { name: /verif(y|ied)$/i })).toBeNull();
    expect(screen.queryByText(/mark (as )?verified/i)).toBeNull();
  });
});
