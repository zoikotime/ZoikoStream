// Profile photos on the client: the shared avatar (photo → initials → icon, never a broken
// image), the editor (pick → preview → save, change, remove, inline errors), and the live
// update — after a save, the profile header, the console sidebar and the account menu all show
// the new photo in the SAME render, with no reload.
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  API_BASE: "http://api.test",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
  errMsg: (e, fallback) => e?.response?.data?.detail ?? fallback ?? "Something went wrong.",
}));
vi.mock("../../ui/Toast", () => ({ notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() } }));

import api from "../../api";
import { AuthProvider, useAuth } from "../../auth/AuthContext";
import { __resetSessionChannelForTests } from "../../auth/sessionStore";
import OrganizationLayout from "../../layouts/OrganizationLayout";
import { ThemeProvider } from "../../theme/ThemeContext";
import { notify } from "../../ui/Toast";
import UserAvatar from "../../ui/UserAvatar";
import { PROFILE_UPDATED_EVENT } from "../../utils/avatar";
import ProfileHeader from "../organization/profile/ProfileHeader";
import ProfilePhotoEditor from "./ProfilePhotoEditor";

const UID = "6f1d2a4e-8b3c-4d5e-9f60-718293a4b5c6";
const PHOTO = `/api/avatars/${UID}/${"a".repeat(32)}.webp`;
const PHOTO_2 = `/api/avatars/${UID}/${"b".repeat(32)}.webp`;
const USER = { id: UID, full_name: "Vihari Rao", email: "vihari@example.com", username: "vihari", role: "org_admin", avatar_url: null };

const png = (bytes = 1024) => new File([new Uint8Array(bytes)], "me.png", { type: "image/png" });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  sessionStorage.clear();
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  __resetSessionChannelForTests();
  localStorage.clear();
  document.documentElement.classList.remove("dark");
});

// ── the shared avatar ─────────────────────────────────────────────────────────────────────

describe("UserAvatar", () => {
  it("shows initials when there is no photo", () => {
    const { container } = render(<UserAvatar name="Vihari Rao" />);
    expect(container.querySelector("[data-avatar]")).toHaveAttribute("data-avatar", "initials");
    expect(screen.getByText("VR")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows the photo, cropped to the circle, with a meaningful alt", () => {
    render(<UserAvatar name="Vihari Rao" src={PHOTO} className="h-10 w-10" />);
    const img = screen.getByRole("img", { name: "Profile photo of Vihari Rao" });
    expect(img).toHaveAttribute("src", `http://api.test${PHOTO}`);
    expect(img).toHaveClass("object-cover");
  });

  it("falls back to initials, never a broken image, when the photo fails to load", () => {
    const { container } = render(<UserAvatar name="Vihari Rao" src={PHOTO} className="h-10 w-10" />);
    const box = container.querySelector("[data-avatar]");
    fireEvent.error(screen.getByRole("img"));
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("VR")).toBeInTheDocument();
    expect(container.querySelector("[data-avatar]")).toBe(box);          // same box: no layout shift
    expect(box).toHaveClass("h-10", "w-10");
  });

  it("shows a generic person icon when there are no initials either", () => {
    const { container } = render(<UserAvatar name="" />);
    expect(container.querySelector("[data-avatar]")).toHaveAttribute("data-avatar", "icon");
  });

  it.each([
    "https://evil.example.com/a.webp",
    "javascript:alert(1)",
    "/api/avatars/../../etc/passwd",
    "/static/x.png",
  ])("never loads anything that is not a platform avatar path (%s)", (src) => {
    const { container } = render(<UserAvatar name="Vihari Rao" src={src} />);
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText("VR")).toBeInTheDocument();
  });
});

// ── the editor ────────────────────────────────────────────────────────────────────────────

vi.mock("../../auth/AuthContext", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, useAuth: (...args) => (globalThis.__authOverride ?? real.useAuth)(...args) };
});

function renderEditor(user = USER) {
  const refresh = vi.fn(() => Promise.resolve(user));
  globalThis.__authOverride = () => ({ user, refresh });
  render(<ProfilePhotoEditor user={user} />);
  return { refresh, input: screen.getByLabelText("Choose a profile photo") };
}

describe("ProfilePhotoEditor", () => {
  afterEach(() => { delete globalThis.__authOverride; });

  it("offers Upload photo, and no Remove, when there is no photo", () => {
    renderEditor();
    expect(screen.getByRole("button", { name: "Upload photo" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove photo" })).not.toBeInTheDocument();
  });

  it("refuses an unsupported type inline, and sends nothing", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const { input } = renderEditor();
    await user.upload(input, new File(["GIF89a"], "anim.gif", { type: "image/gif" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Upload a PNG, JPG or WebP image.");
    expect(input).toHaveAttribute("aria-describedby", expect.stringContaining(screen.getByRole("alert").id));
    expect(api.post).not.toHaveBeenCalled();
  });

  it("refuses a file over 5 MB inline, and sends nothing", async () => {
    const user = userEvent.setup();
    const { input } = renderEditor();
    await user.upload(input, png(5 * 1024 * 1024 + 1));
    expect(screen.getByRole("alert")).toHaveTextContent("That image is larger than 5 MB.");
    expect(api.post).not.toHaveBeenCalled();
  });

  it("previews the chosen photo, then uploads it, refreshes the session and announces the change", async () => {
    const user = userEvent.setup();
    const updated = { ...USER, avatar_url: PHOTO };
    vi.mocked(api.post).mockResolvedValue({ data: updated });
    const heard = [];
    const listener = (e) => heard.push(e.detail.user);
    window.addEventListener(PROFILE_UPDATED_EVENT, listener);
    const { refresh, input } = renderEditor();
    const file = png();
    await user.upload(input, file);
    expect(screen.getByRole("img", { name: "Preview of your new profile photo" })).toHaveAttribute("src", "blob:preview");
    expect(api.post).not.toHaveBeenCalled();                              // nothing sent before Save
    await user.click(screen.getByRole("button", { name: "Save photo" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/profile/avatar", file, {
      headers: { "Content-Type": "image/png" },
    }));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(heard).toEqual([updated]);
    expect(notify.success).toHaveBeenCalledWith("Profile photo updated");
    window.removeEventListener(PROFILE_UPDATED_EVENT, listener);
  });

  it("Cancel discards the chosen photo without sending it", async () => {
    const user = userEvent.setup();
    const { input } = renderEditor();
    await user.upload(input, png());
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("img", { name: /preview/i })).not.toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();
  });

  it("with a photo, offers Change and Remove; Remove clears it and announces the change", async () => {
    const user = userEvent.setup();
    vi.mocked(api.delete).mockResolvedValue({ data: { ...USER, avatar_url: null } });
    const { refresh } = renderEditor({ ...USER, avatar_url: PHOTO });
    expect(screen.getByRole("button", { name: "Change photo" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove photo" }));
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith("/profile/avatar"));
    await waitFor(() => expect(refresh).toHaveBeenCalled());
    expect(notify.success).toHaveBeenCalledWith("Profile photo removed");
  });

  it("shows the server's refusal inline", async () => {
    const user = userEvent.setup();
    vi.mocked(api.post).mockRejectedValue({ response: { status: 415, data: { detail: "Upload a PNG, JPG or WebP image." } } });
    const { input } = renderEditor();
    await user.upload(input, png());
    await user.click(screen.getByRole("button", { name: "Save photo" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload a PNG, JPG or WebP image.");
  });

  it("is keyboard operable: the buttons are reachable, the hidden input is not a stray tab stop", async () => {
    const user = userEvent.setup();
    const { input } = renderEditor({ ...USER, avatar_url: PHOTO });
    expect(input).toHaveAttribute("tabindex", "-1");
    await user.tab();
    expect(screen.getByRole("button", { name: "Change photo" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "Remove photo" })).toHaveFocus();
  });
});

// ── the profile header: avatar → editor ───────────────────────────────────────────────────

describe("the profile header avatar", () => {
  afterEach(() => { delete globalThis.__authOverride; });

  it.each(["light", "dark"])("is a labelled button that opens the photo editor (%s mode)", async (theme) => {
    localStorage.setItem("theme", theme);
    globalThis.__authOverride = () => ({ user: USER, refresh: vi.fn() });
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <MemoryRouter>
          <ProfileHeader user={{ ...USER, avatar_url: PHOTO }} />
        </MemoryRouter>
      </ThemeProvider>,
    );
    expect(document.documentElement.classList.contains("dark")).toBe(theme === "dark");
    const button = screen.getByRole("button", { name: "Change profile photo" });
    expect(within(button).getByRole("img", { name: "Profile photo of Vihari Rao" })).toBeInTheDocument();
    button.focus();
    expect(button).toHaveFocus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "Change photo" })).toBeInTheDocument();
  });

  it("invites a first photo when there is none", () => {
    globalThis.__authOverride = () => ({ user: USER, refresh: vi.fn() });
    render(<MemoryRouter><ProfileHeader user={USER} /></MemoryRouter>);
    expect(screen.getByRole("button", { name: "Add a profile photo" })).toHaveTextContent("VR");
  });

  // There used to be TWO cameras on the avatar: the centred hover overlay and an always-on
  // badge at its bottom-left, both decorating the same button — two on every hover.
  it.each([["with a photo", PHOTO], ["with initials", null]])(
    "shows exactly one camera, inside the one photo control (%s)", (_case, avatar) => {
      globalThis.__authOverride = () => ({ user: USER, refresh: vi.fn() });
      const { container } = render(<MemoryRouter><ProfileHeader user={{ ...USER, avatar_url: avatar }} /></MemoryRouter>);
      const button = screen.getByRole("button", { name: avatar ? "Change profile photo" : "Add a profile photo" });
      const cameras = container.querySelectorAll("svg[class*='lucide-camera']");
      expect(cameras).toHaveLength(1);
      expect(button.contains(cameras[0])).toBe(true);
      // One control: nothing interactive nested inside it, and no second photo control beside it.
      expect(button.querySelector("button, a, input, [tabindex]")).toBeNull();
      expect(screen.getAllByRole("button", { name: /profile photo/i })).toHaveLength(1);
      // The avatar itself is untouched: the photo, or the initials when there is none.
      if (avatar) expect(within(button).getByRole("img", { name: "Profile photo of Vihari Rao" })).toBeInTheDocument();
      else expect(button).toHaveTextContent("VR");
    });

  it("keeps the centred camera on hover and focus, and always on a screen that cannot hover", () => {
    globalThis.__authOverride = () => ({ user: USER, refresh: vi.fn() });
    const { container } = render(<MemoryRouter><ProfileHeader user={USER} /></MemoryRouter>);
    const overlay = container.querySelector("[data-avatar-edit]");
    expect(overlay).toHaveAttribute("aria-hidden", "true");
    expect(overlay.className).toContain("inset-0");                    // centred over the avatar
    for (const cls of ["opacity-0", "group-hover:opacity-100", "group-focus-visible:opacity-100",
                       "[@media(hover:none)]:opacity-100"]) {
      expect(overlay.className.split(/\s+/)).toContain(cls);
    }
  });

  it("opens the photo editor on a click and on Space, and keeps its focus ring", async () => {
    globalThis.__authOverride = () => ({ user: USER, refresh: vi.fn() });
    const user = userEvent.setup();
    render(<MemoryRouter><ProfileHeader user={USER} /></MemoryRouter>);
    const button = screen.getByRole("button", { name: "Add a profile photo" });
    expect(button.className).toMatch(/focus-visible:ring/);
    await user.click(button);
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    button.focus();
    await user.keyboard(" ");
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});

// ── member lists ──────────────────────────────────────────────────────────────────────────

describe("the Members page", () => {
  afterEach(() => { delete globalThis.__authOverride; });

  it("shows each member's photo, and initials for those without one", async () => {
    globalThis.__authOverride = () => ({ user: USER, refresh: vi.fn() });
    vi.mocked(api.get).mockImplementation((url) => {
      if (url === "/organization/users") {
        return Promise.resolve({ data: { items: [
          { ...USER, is_active: true, created_at: "2026-08-24T00:00:00Z", avatar_url: PHOTO },
          { id: "m2", full_name: "Asha Member", email: "asha@example.com", role: "host", is_active: true,
            created_at: "2026-08-25T00:00:00Z", avatar_url: null },
        ] } });
      }
      return Promise.resolve({ data: { items: [] } });
    });
    const { default: OrganizationMembers } = await import("../../pages/organization/InviteMembers");
    const { container } = render(
      <ThemeProvider><MemoryRouter><OrganizationMembers /></MemoryRouter></ThemeProvider>,
    );
    await screen.findByText("Asha Member");
    expect(container.querySelector(`img[src="http://api.test${PHOTO}"]`)).not.toBeNull();
    expect(screen.getByText("AM")).toBeInTheDocument();
  });
});

// ── live, platform-wide update ────────────────────────────────────────────────────────────

describe("after a new photo is saved", () => {
  it("the profile header, the sidebar and the account menu all show it — no reload", async () => {
    let me = { ...USER };
    vi.mocked(api.get).mockImplementation((url) => {
      if (url === "/auth/me") return Promise.resolve({ data: me });
      if (url === "/auth/session") return Promise.resolve({ data: {} });
      if (url === "/organization/console-state") {
        return Promise.resolve({ data: {
          organization: { name: "naveen" }, workspace: { label: "production" }, workspaces: [], badges: {},
          health: { status: "ok" },
          user: { name: me.full_name, email: me.email, role_label: "Organization Owner", avatar_url: me.avatar_url },
        } });
      }
      return Promise.resolve({ data: {} });
    });
    vi.mocked(api.post).mockImplementation((url) => {
      if (url === "/profile/avatar") {
        me = { ...me, avatar_url: PHOTO_2 };
        return Promise.resolve({ data: me });
      }
      return Promise.resolve({ data: {} });
    });
    localStorage.setItem("token", "signed.in.token");

    function ProfileProbe() {
      const { user } = useAuth();
      return user ? <ProfileHeader user={user} /> : null;
    }
    const user = userEvent.setup();
    render(
      <ThemeProvider>
        <MemoryRouter initialEntries={["/organization/profile"]}>
          <AuthProvider>
            <Routes>
              <Route element={<OrganizationLayout />}>
                <Route path="/organization/profile" element={<ProfileProbe />} />
              </Route>
            </Routes>
          </AuthProvider>
        </MemoryRouter>
      </ThemeProvider>,
    );

    const avatarButton = await screen.findByRole("button", { name: "Add a profile photo" });
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/organization/console-state"));
    const photos = () => [...document.querySelectorAll(`img[src="http://api.test${PHOTO_2}"]`)];
    expect(photos()).toHaveLength(0);

    await user.click(avatarButton);
    const dialog = await screen.findByRole("dialog");
    await user.upload(within(dialog).getByLabelText("Choose a profile photo"), png());
    await user.click(within(dialog).getByRole("button", { name: "Save photo" }));

    // Header + sidebar + account-menu trigger, all on the page that never reloaded.
    await waitFor(() => expect(photos().length).toBeGreaterThanOrEqual(3));
    expect(screen.getByRole("button", { name: "Change profile photo" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Account menu" }));
    expect(within(screen.getByRole("menu")).getByRole("img", { name: "Profile photo of Vihari Rao" }))
      .toHaveAttribute("src", `http://api.test${PHOTO_2}`);
    await act(async () => {});
  });
});
