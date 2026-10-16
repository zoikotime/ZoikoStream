// Settings -> Branding -> Preview. The button here was a <span> styled as a button that did
// nothing, and then a "Go Live" — the producer console's operational action — inside a mock-up.
// Both are now a "Sample Button": the trigger opens a mock-up built from the CURRENT form values,
// and the sample inside it only shows how the brand color looks and responds. Nothing in that
// path can reach the API, the router or the broadcast machinery.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, useLocation } from "react-router-dom";
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
import { ACCENT } from "../../ui/tokens";
import BrandingPreview from "../../components/organization/BrandingPreview";
import Settings from "./Settings";

const PAYLOADS = {
  "/organization/profile": { name: "Acme Live", slug: "acme", website: "", support_email: "", industry: null, company_size: null, description: "" },
  "/organization/security": { require_2fa: false, enforce_sso: false, min_password_length: 8, session_timeout: "8 hours", allowed_domains: "" },
  "/organization/notifications": { event_scheduled: true, event_starting: false, recording_ready: false, weekly_summary: false, billing: true, mentions: false, member_joined: false, security_alerts: true },
  "/organization/domain": { domain: null, domain_verified: false },
  "/organization/branding": { primary_color: "amber", logo_url: null },
};
const LOAD_URLS = [...Object.keys(PAYLOADS), "/organization/notifications/catalog"];
const START = "/organization/settings?tab=branding";

function LocationProbe() {
  const { pathname, search } = useLocation();
  return <output data-testid="location">{`${pathname}${search}`}</output>;
}

const open = () =>
  render(
    <ThemeProvider>
      <MemoryRouter initialEntries={[START]}>
        <Settings />
        <LocationProbe />
      </MemoryRouter>
    </ThemeProvider>
  );

const trigger = () => screen.findByRole("button", { name: "Sample Button — open branding preview" });
const dialog = () => screen.findByRole("dialog", { name: "Branding preview" });
const sample = async () => within(await dialog()).getByRole("button", { name: "Sample Button" });

// Everything the page did on the network apart from loading its own settings.
const otherTraffic = () => [
  ...vi.mocked(api.get).mock.calls.map(([u]) => u).filter((u) => !LOAD_URLS.includes(u)),
  ...["post", "patch", "put", "delete"].flatMap((m) => vi.mocked(api[m]).mock.calls.map(([u]) => `${m} ${u}`)),
];

// What an element shows or announces: its text plus the attributes assistive tech reads.
const said = (el) =>
  [el, ...el.querySelectorAll("*")]
    .flatMap((n) => [n === el ? el.textContent : "", ...["aria-label", "title", "alt", "aria-description"].map((a) => n.getAttribute(a) || "")])
    .join(" ");
const OPERATIONAL = /go live|broadcast|start stream|publish|record|end event|producer console/i;

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  vi.mocked(api.get).mockImplementation((url) =>
    url in PAYLOADS ? Promise.resolve({ data: PAYLOADS[url] }) : Promise.reject(new Error("404")));
  vi.mocked(api.patch).mockImplementation((url, body) => Promise.resolve({ data: { ...PAYLOADS[url], ...body } }));
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the preview button is a real control", () => {
  it("is a <button> that opens the branding preview", async () => {
    open();
    const btn = await trigger();
    expect(btn.tagName).toBe("BUTTON");
    expect(btn).toHaveAttribute("type", "button");
    expect(btn).toHaveAttribute("aria-haspopup", "dialog");
    expect(btn).toHaveTextContent(/^Sample Button$/);       // the accessible name starts with what it shows
    fireEvent.click(btn);
    const dlg = await dialog();
    expect(within(dlg).getByRole("button", { name: "Sample Button" })).toBeInTheDocument();
    expect(within(dlg).getByText("Acme Live")).toBeInTheDocument();
  });
});

describe("no operational wording", () => {
  it("nothing on the branding tab or in the preview says Go Live or Start Broadcast", async () => {
    open();
    const btn = await trigger();
    const box = btn.closest(".rounded-xl");
    expect(said(document.body)).not.toMatch(/go live|start broadcast/i);
    expect(said(box)).not.toMatch(OPERATIONAL);

    fireEvent.click(btn);
    const dlg = await dialog();
    expect(said(dlg)).not.toMatch(OPERATIONAL);
    expect(screen.queryAllByRole("button", { name: /go live|broadcast|start|stream|publish|record|end event/i, hidden: true }))
      .toEqual([]);
  });

  it("the preview shows a Sample Button: a plain type=button with exactly that name, no icon", async () => {
    open();
    fireEvent.click(await trigger());
    const inner = await sample();
    expect(inner.tagName).toBe("BUTTON");
    expect(inner).toHaveAttribute("type", "button");
    expect(inner).toHaveTextContent(/^Sample Button$/);
    expect(inner.querySelector("svg")).toBeNull();
    expect(inner).toHaveAccessibleDescription(
      "Preview how your brand color appears on buttons and controls. Hover or focus the sample control to preview its interaction states.");
  });

  it("pressing it says only that it is a sample", async () => {
    open();
    fireEvent.click(await trigger());
    fireEvent.click(await sample());
    const note = await screen.findByText(/doesn't perform any action/);
    expect(note).toHaveAttribute("role", "status");
    expect(note.textContent).not.toMatch(OPERATIONAL);
    expect(note.textContent).not.toMatch(/start/i);
  });
});

describe("no side effects", () => {
  it("sends no request, starts nothing, changes no event and goes nowhere — however often it is pressed", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    open();
    fireEvent.click(await trigger());
    const inner = await sample();
    fireEvent.click(inner);
    fireEvent.click(inner);
    fireEvent.click(inner);
    await screen.findByText(/doesn't perform any action/);
    expect(otherTraffic()).toEqual([]);                       // no API call of any kind, so no event status change
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(screen.getByTestId("location").textContent).toBe(START);
    expect(screen.getByRole("dialog", { name: "Branding preview" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Save branding" })).toBeDisabled();   // nothing dirtied
  });

  it("on its own, pressing the sample never closes the preview or calls back", () => {
    const onClose = vi.fn();
    render(<BrandingPreview open onClose={onClose} accent="violet" orgName="Acme Live" />);
    const inner = screen.getByRole("button", { name: "Sample Button" });
    for (let i = 0; i < 5; i += 1) fireEvent.click(inner);
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("cannot: the preview module imports no API client, router or broadcast code", () => {
    const imports = previewSource.split("\n").filter((l) => /^\s*import\s/.test(l)).join("\n");
    expect(imports).not.toMatch(/["'][^"']*\/api(Base)?["']/);
    expect(imports).not.toMatch(/livekit|broadcast|host\/|react-router/i);
    expect(previewSource).not.toMatch(/fetch\(|XMLHttpRequest|navigate\(|window\.location/);
  });
});

describe("the preview uses the brand color, including unsaved changes", () => {
  it("uses the saved color, with text that reads on it", async () => {
    open();
    const btn = await trigger();
    expect(btn.className).toContain("bg-amber-500");
    expect(btn.className).toContain("text-slate-950");      // white on amber-500 is about 2:1
    expect(btn.className).not.toContain("text-white");
    fireEvent.click(btn);
    const inner = await sample();
    expect(inner.className).toContain("bg-amber-500");
    expect(inner.className).toContain("text-slate-950");
  });

  it("uses a color picked but not saved", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: "blue" }));
    fireEvent.click(await trigger());
    const inner = await sample();
    expect(inner.className).toContain("bg-blue-600");
    expect(inner.className).toContain("text-white");
    expect(inner.className).not.toContain("bg-amber-500");
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("uses a light-mode logo URL typed but not saved", async () => {
    localStorage.setItem("theme", "light");
    open();
    fireEvent.change(await screen.findByLabelText("Light mode logo"), { target: { value: "https://cdn.acme.com/mark.svg" } });
    // Not saved: the preview reads the form, not the server.
    fireEvent.click(await trigger());
    expect(within(await dialog()).getByAltText("Acme Live logo")).toHaveAttribute("src", "https://cdn.acme.com/mark.svg");
    expect(api.patch).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("follows every change immediately — on the page and on reopening the preview", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: "rose" }));
    expect((await trigger()).className).toContain("bg-rose-600");
    fireEvent.click(await trigger());
    expect((await sample()).className).toContain("bg-rose-600");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "indigo" }));
    fireEvent.click(await trigger());
    const inner = await sample();
    expect(inner.className).toContain("bg-indigo-600");
    expect(inner.className).not.toContain("bg-rose-600");
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("repaints while open when the color changes", () => {
    const props = { open: true, onClose: vi.fn(), orgName: "Acme Live" };
    const { rerender } = render(<BrandingPreview {...props} accent="amber" />);
    expect(screen.getByRole("button", { name: "Sample Button" }).className).toContain("bg-amber-500");
    rerender(<BrandingPreview {...props} accent="rose" />);
    const inner = screen.getByRole("button", { name: "Sample Button" });
    expect(inner.className).toContain("bg-rose-600");
    expect(inner.className).not.toContain("bg-amber-500");
  });

  it.each(Object.keys(ACCENT))("draws %s from the brand palette, never a fixed red", (accent) => {
    render(<BrandingPreview open onClose={vi.fn()} accent={accent} orgName="Acme Live" />);
    const inner = screen.getByRole("button", { name: "Sample Button" });
    expect(inner.className).toContain(ACCENT[accent].solid);
    expect(inner.className).toContain(ACCENT[accent].onSolid);
    expect(inner.className).not.toMatch(/(^|\s)(bg|text)-red-/);
  });
});

describe("keyboard", () => {
  it("opens with Enter, focuses the sample, presses with Space and Enter, closes with Escape and returns focus", async () => {
    const user = userEvent.setup();
    open();
    const btn = await trigger();
    btn.focus();
    await user.keyboard("{Enter}");
    const inner = await sample();
    expect(inner).toHaveFocus();                            // focus moves into the preview
    expect(inner.className).toMatch(/focus-visible:ring-2/);  // with a visible ring
    await user.keyboard(" ");
    expect(await screen.findByText(/doesn't perform any action/)).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(inner).toHaveFocus();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() => expect(btn).toHaveFocus());
    expect(otherTraffic()).toEqual([]);
  });

  it("reopens with the default hint, not the pressed one", async () => {
    open();
    fireEvent.click(await trigger());
    fireEvent.click(await sample());
    await screen.findByText(/doesn't perform any action/);
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(await trigger());
    expect(await sample()).toHaveAccessibleDescription(/^Preview how your brand color appears/);
  });
});

describe("light and dark mode", () => {
  it.each([
    ["light", false],
    ["dark", true],
  ])("in %s mode the preview follows the console theme", async (theme, dark) => {
    localStorage.setItem("theme", theme);
    open();
    fireEvent.click(await trigger());
    const inner = await sample();
    expect(document.documentElement.classList.contains("dark")).toBe(dark);
    // The same brand fill in both themes; the focus ring and its offset switch with the theme.
    expect(inner.className).toContain("bg-amber-500");
    expect(inner.className).toMatch(/focus-visible:ring-slate-900/);
    expect(inner.className).toMatch(/dark:focus-visible:ring-white/);
    expect(inner.className).toMatch(/dark:focus-visible:ring-offset-slate-950/);
    const stage = screen.getByTestId("branding-preview-stage");
    expect(stage.className).toMatch(/bg-slate-50/);
    expect(stage.className).toMatch(/dark:bg-slate-950/);
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
