// Settings -> Branding: a light-mode and a dark-mode logo.
//
// One "Logo URL" used to serve both themes, so a logo drawn for light backgrounds vanished in
// dark mode — and the preview sat on the console's own background, which hid exactly that.
// Each logo now has its own field, previewed on the background it is for, validated inline,
// and saved on its own.
import { configure, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

configure({ asyncUtilTimeout: 8000 });
vi.setConfig({ testTimeout: 30000 });

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

const LIGHT = "https://cdn.acme.com/logo.png";
const DARK = "https://cdn.acme.com/logo-dark.svg";

let branding;
const payloads = () => ({
  "/organization/profile": { name: "Acme Live", slug: "acme", website: "", support_email: "", industry: null, company_size: null, description: "" },
  "/organization/security": { require_2fa: false, enforce_sso: false, min_password_length: 8, session_timeout: "8 hours", allowed_domains: "" },
  "/organization/notifications": {},
  "/organization/domain": { domain: null, domain_verified: false },
  "/organization/branding": branding,
});

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  // A pre-existing organization: one logo, no dark one (the API returns null for it).
  branding = { primary_color: "violet", logo_url: LIGHT, logo_url_dark: null };
  vi.mocked(api.get).mockImplementation((url) => {
    const p = payloads();
    return url in p ? Promise.resolve({ data: p[url] }) : Promise.reject(new Error("404"));
  });
  vi.mocked(api.patch).mockImplementation((url, body) => {
    branding = { ...branding, ...body };
    return Promise.resolve({ data: branding });
  });
});

function open(theme = "light") {
  localStorage.setItem("theme", theme);
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/settings?tab=branding"]}>
        <Settings />
      </MemoryRouter>
    </ThemeProvider>
  );
}

const lightInput = () => screen.findByLabelText("Light mode logo");
const darkInput = () => screen.findByLabelText("Dark mode logo");
const tile = (surface) => screen.getByTestId(`logo-preview-${surface}`);
const tileImg = (surface) => tile(surface).querySelector("img");
const save = () => screen.getByRole("button", { name: "Save branding" });

describe("two logo fields", () => {
  it("shows a Light mode logo and a Dark mode logo, each labelled and described", async () => {
    open();
    expect(await lightInput()).toBeInTheDocument();
    expect(await darkInput()).toBeInTheDocument();
    expect(screen.getByText("Used on light backgrounds.")).toBeInTheDocument();
    expect(screen.getByText("Used on dark backgrounds. Falls back to the light logo when not provided.")).toBeInTheDocument();
    expect(await lightInput()).toHaveAccessibleDescription(/used on light backgrounds/i);
  });

  it("an existing single-logo organization keeps its light logo, and dark mode borrows it", async () => {
    open();
    expect(await lightInput()).toHaveValue(LIGHT);
    expect(await darkInput()).toHaveValue("");
    expect(tileImg("light")).toHaveAttribute("src", LIGHT);
    // The light logo, on the DARK background — so a logo that disappears there is visible now.
    expect(tileImg("dark")).toHaveAttribute("src", LIGHT);
    expect(screen.getByText(/your light logo is used on dark backgrounds/i)).toBeInTheDocument();
  });

  it("previews each logo on the background it is for, whatever the console theme", async () => {
    for (const theme of ["light", "dark"]) {
      const view = open(theme);
      await lightInput();
      expect(tile("light").className).toMatch(/\bbg-white\b/);
      expect(tile("light").className).not.toMatch(/dark:/);
      expect(tile("dark").className).toMatch(/\bbg-slate-950\b/);
      expect(tile("dark").className).not.toMatch(/dark:/);
      view.unmount();
    }
  });

  it("with no logos at all, both previews show the ZoikoStream logo viewers will see", async () => {
    branding = { primary_color: "violet", logo_url: null, logo_url_dark: null };
    open();
    await lightInput();
    expect(within(tile("light")).getByAltText("ZoikoStream")).toBeInTheDocument();
    expect(within(tile("dark")).getByAltText("ZoikoStream")).toBeInTheDocument();
    expect(screen.getAllByText("Not set — viewers see the ZoikoStream logo.")).toHaveLength(2);
  });
});

describe("saving", () => {
  it("saves the dark logo on its own, without re-sending the light one", async () => {
    const user = userEvent.setup();
    open();
    await user.type(await darkInput(), DARK);
    expect(tileImg("dark")).toHaveAttribute("src", DARK);
    await user.click(save());

    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/organization/branding", { logo_url_dark: DARK }));
    expect(api.patch).toHaveBeenCalledTimes(1);
    expect(await darkInput()).toHaveValue(DARK);
  });

  it("clearing the dark logo sends null and the preview falls back to the light logo", async () => {
    const user = userEvent.setup();
    branding = { ...branding, logo_url_dark: DARK };
    open();
    expect(await darkInput()).toHaveValue(DARK);
    expect(tileImg("dark")).toHaveAttribute("src", DARK);

    await user.clear(await darkInput());
    await user.click(save());
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/organization/branding", { logo_url_dark: null }));
    expect(tileImg("dark")).toHaveAttribute("src", LIGHT);
  });

  it("an old stored logo that predates the rules does not block saving the brand color", async () => {
    const user = userEvent.setup();
    branding = { primary_color: "violet", logo_url: "http://legacy.acme.com/logo.png", logo_url_dark: null };
    open();
    await lightInput();
    await user.click(screen.getByRole("button", { name: "blue" }));
    await user.click(save());
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith("/organization/branding", { primary_color: "blue" }));
  });
});

describe("validation", () => {
  it("an invalid dark logo URL shows an inline message when the field is left, and is not saved", async () => {
    const user = userEvent.setup();
    open();
    await user.type(await darkInput(), "http://cdn.acme.com/logo-dark.png");
    await user.tab();

    const input = await darkInput();
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/use an https:\/\/ link to the image/i);
    // Not loaded as a preview: an http image is not what viewers would get.
    expect(tile("dark").dataset.status).toBe("invalid");

    await user.click(save());
    expect(api.patch).not.toHaveBeenCalled();
    expect(screen.getByTestId("section-status-branding")).toHaveTextContent("check the highlighted fields");
  });

  it("refuses a link that is not an image file, on the field that has it", async () => {
    const user = userEvent.setup();
    open();
    await user.clear(await lightInput());
    await user.type(await lightInput(), "https://cdn.acme.com/brand-guide.pdf");
    await user.click(save());
    expect(await lightInput()).toHaveAccessibleDescription(/link to an image file/i);
    expect(await darkInput()).not.toHaveAttribute("aria-invalid");
    expect(api.patch).not.toHaveBeenCalled();
  });

  it("editing the field clears its message", async () => {
    const user = userEvent.setup();
    open();
    await user.type(await darkInput(), "ftp://x");
    await user.tab();
    expect(await darkInput()).toHaveAttribute("aria-invalid", "true");
    await user.type(await darkInput(), "y");
    expect(await darkInput()).not.toHaveAttribute("aria-invalid");
  });

  it("a server refusal lands on the field, in plain words", async () => {
    const user = userEvent.setup();
    vi.mocked(api.patch).mockRejectedValueOnce({
      response: { status: 422, data: { detail: [{ loc: ["body", "logo_url_dark"], msg: "Value error, Use an https:// link to the image." }] } },
    });
    open();
    await user.type(await darkInput(), DARK);
    await user.click(save());
    await waitFor(() => expect(screen.getByText("Use an https:// link to the image.")).toBeInTheDocument());
    expect(screen.queryByText(/value error/i)).not.toBeInTheDocument();
  });
});

describe("previews survive bad images", () => {
  it("a broken dark logo is reported, keeps its tile, and does not disturb the light preview", async () => {
    const user = userEvent.setup();
    open();
    await user.type(await darkInput(), "https://cdn.acme.com/missing.png");
    const before = tile("dark").className;
    fireEvent.error(tileImg("dark"));

    expect(tile("dark").dataset.status).toBe("error");
    expect(tile("dark").className).toBe(before);
    expect(tile("dark").className).toMatch(/\bh-16\b/);
    expect(tile("dark").className).toMatch(/\bw-28\b/);
    expect(tileImg("dark")).toBeNull();
    expect(screen.getByText(/didn't load as an image/i)).toBeInTheDocument();
    expect(tileImg("light")).toHaveAttribute("src", LIGHT);
  });

  it("a loaded logo (PNG or SVG, transparent or not) is shown fitted inside its tile", async () => {
    const user = userEvent.setup();
    open();
    await user.type(await darkInput(), DARK);
    fireEvent.load(tileImg("dark"));
    expect(tile("dark").dataset.status).toBe("ok");
    expect(tileImg("dark").className).toMatch(/object-contain/);
    expect(tileImg("dark").className).toMatch(/max-h-full/);
    expect(tileImg("dark").className).not.toMatch(/invert|filter/);
  });
});

describe("the branding preview dialog follows the theme", () => {
  it.each([
    ["light", LIGHT],
    ["dark", DARK],
  ])("in %s mode it shows that theme's logo", async (theme, expected) => {
    branding = { ...branding, logo_url_dark: DARK };
    open(theme);
    await lightInput();
    fireEvent.click(screen.getByRole("button", { name: "Go Live — open branding preview" }));
    const dialog = await screen.findByRole("dialog", { name: "Branding preview" });
    expect(within(dialog).getByAltText("Acme Live logo")).toHaveAttribute("src", expected);
  });
});
