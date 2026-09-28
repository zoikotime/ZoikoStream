// Settings -> Branding -> Preview. The "Go Live" here was a <span> styled as a button: it
// looked like an action and did nothing. It now opens a mock-up built from the CURRENT form
// values, and nothing in that path can reach the broadcast machinery.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import previewSource from "../../components/organization/BrandingPreview.jsx?raw";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn(), delete: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Admin", email: "ada@example.com", role: "org_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";

const PAYLOADS = {
  "/organization/profile": { name: "Acme Live", slug: "acme", website: "", support_email: "", industry: null, company_size: null, description: "" },
  "/organization/security": { require_2fa: false, enforce_sso: false, min_password_length: 8, session_timeout: "8 hours", allowed_domains: "" },
  "/organization/notifications": { event_scheduled: true, event_starting: false, recording_ready: false, weekly_summary: false, billing: true, mentions: false, member_joined: false, security_alerts: true },
  "/organization/domain": { domain: null, domain_verified: false },
  "/organization/branding": { primary_color: "amber", logo_url: null },
};
const LOAD_URLS = [...Object.keys(PAYLOADS), "/organization/notifications/catalog"];

const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/settings?tab=branding"]}>
        <Settings />
      </MemoryRouter>
    </ThemeProvider>
  );

const trigger = () => screen.findByRole("button", { name: "Go Live — open branding preview" });
const dialog = () => screen.findByRole("dialog", { name: "Branding preview" });

// Everything the page did on the network apart from loading its own settings.
const otherTraffic = () => [
  ...vi.mocked(api.get).mock.calls.map(([u]) => u).filter((u) => !LOAD_URLS.includes(u)),
  ...["post", "patch", "put", "delete"].flatMap((m) => vi.mocked(api[m]).mock.calls.map(([u]) => `${m} ${u}`)),
];

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockImplementation((url) =>
    url in PAYLOADS ? Promise.resolve({ data: PAYLOADS[url] }) : Promise.reject(new Error("404")));
  vi.mocked(api.patch).mockImplementation((url, body) => Promise.resolve({ data: { ...PAYLOADS[url], ...body } }));
});

describe("the preview button is a real control", () => {
  it("is a <button> that opens the branding preview", async () => {
    open();
    const btn = await trigger();
    expect(btn.tagName).toBe("BUTTON");
    expect(btn).toHaveAttribute("type", "button");
    expect(btn).toHaveAttribute("aria-haspopup", "dialog");
    fireEvent.click(btn);
    const dlg = await dialog();
    expect(within(dlg).getByRole("button", { name: /Go Live/ })).toBeInTheDocument();
    expect(within(dlg).getByText("Acme Live")).toBeInTheDocument();
  });
});

describe("nothing real is started", () => {
  it("sends no request — no Go Live call, no broadcast session — however often it is pressed", async () => {
    open();
    fireEvent.click(await trigger());
    const inner = within(await dialog()).getByRole("button", { name: /Go Live/ });
    fireEvent.click(inner);
    fireEvent.click(inner);
    expect(await screen.findByText(/Preview only — nothing was started/)).toBeInTheDocument();
    expect(otherTraffic()).toEqual([]);
  });

  it("cannot: the preview module imports no API client and no broadcast code", () => {
    const imports = previewSource.split("\n").filter((l) => /^\s*import\s/.test(l)).join("\n");
    expect(imports).not.toMatch(/["'][^"']*\/api["']/);
    expect(imports).not.toMatch(/livekit|broadcast|host\//i);
  });
});

describe("the preview reflects unsaved branding", () => {
  it("uses a color picked but not saved", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: "blue" }));
    fireEvent.click(await trigger());
    const inner = within(await dialog()).getByRole("button", { name: /Go Live/ });
    expect(inner.className).toContain("bg-blue-600");
    expect(inner.className).not.toContain("bg-amber-500");
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("uses a logo URL typed but not saved", async () => {
    open();
    fireEvent.change(await screen.findByPlaceholderText("https://cdn.yourcompany.com/logo.png"),
      { target: { value: "https://cdn.acme.com/mark.svg" } });
    fireEvent.click(await trigger());
    expect(within(await dialog()).getByAltText("Acme Live logo")).toHaveAttribute("src", "https://cdn.acme.com/mark.svg");
  });
});

describe("closing", () => {
  it("closes from the Close button", async () => {
    open();
    fireEvent.click(await trigger());
    const dlg = await dialog();
    fireEvent.click(within(dlg.closest("[role=dialog]")).getAllByRole("button", { name: "Close" }).at(-1));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("closes on Escape and on a click outside the dialog", async () => {
    open();
    fireEvent.click(await trigger());
    await dialog();
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(await trigger());
    const dlg = await dialog();
    fireEvent.click(dlg);                                   // inside: stays open
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(dlg.parentElement);                     // outside the panel
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});

describe("keyboard", () => {
  it("opens with Enter, presses with Space, closes with Escape and returns focus", async () => {
    const user = userEvent.setup();
    open();
    const btn = await trigger();
    btn.focus();
    await user.keyboard("{Enter}");
    const inner = within(await dialog()).getByRole("button", { name: /Go Live/ });
    expect(inner).toHaveFocus();                            // focus moves into the preview
    await user.keyboard(" ");
    expect(await screen.findByText(/Preview only — nothing was started/)).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(btn).toHaveFocus());
    expect(otherTraffic()).toEqual([]);
  });
});

describe("saving branding is unchanged", () => {
  it("opening the preview dirties nothing, and a real save still sends only the change", async () => {
    open();
    fireEvent.click(await trigger());
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Save branding" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "rose" }));
    fireEvent.click(screen.getByRole("button", { name: "Save branding" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.patch).mock.calls[0]).toEqual(["/organization/branding", { primary_color: "rose" }]);
  });
});
