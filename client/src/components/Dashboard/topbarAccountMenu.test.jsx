// The organization topbar's account menu (avatar → name → Organization profile → Log out).
//
// THE BUG: on Settings (and every page with an OrganizationPageHeader) the menu's upper half
// rendered blank. The page's own `sticky top-0 z-20` bar painted OVER the menu, because the
// topbar was also z-20 and an equal z-index is won by whichever comes later in the DOM. The
// topbar now sits on the shared z.header layer, above page content and below overlays.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const auth = vi.hoisted(() => ({ user: null, logout: null }));

vi.mock("../../api", () => ({
  default: { get: vi.fn(() => Promise.resolve({ data: {} })), post: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: auth.user, logout: auth.logout }),
}));

import { ThemeProvider } from "../../theme/ThemeContext";
import { z } from "../../ui/tokens";
import Topbar from "./Topbar";

function renderTopbar(state) {
  return render(
    <ThemeProvider>
      <MemoryRouter>
        <Topbar state={state} unknown={false} onRetry={vi.fn()} />
      </MemoryRouter>
    </ThemeProvider>,
  );
}

const open = async (u) => u.click(screen.getByRole("button", { name: "Account menu" }));

beforeEach(() => {
  auth.user = { full_name: "Vihari Rao", email: "vihari@example.com" };
  auth.logout = vi.fn();
});

describe("the account menu", () => {
  it("shows the avatar initials and the name in its header", async () => {
    const u = userEvent.setup();
    renderTopbar({ user: { name: "Vihari Rao", role_label: "Organization Owner" } });
    await open(u);
    const menu = screen.getByRole("menu");
    expect(menu).toHaveTextContent("Vihari Rao");
    expect(menu).toHaveTextContent("Organization Owner");
    expect(menu).toHaveTextContent("VR");                      // the initials avatar
  });

  it("falls back to the signed-in account's name when the overview has none", async () => {
    const u = userEvent.setup();
    renderTopbar({ user: { name: null, role_label: "Organization Owner" } });
    await open(u);
    expect(screen.getByRole("menu")).toHaveTextContent("Vihari Rao");
  });

  it("never throws on a missing name, and shows a neutral initial", async () => {
    auth.user = { full_name: null, email: null };
    const u = userEvent.setup();
    renderTopbar({ user: { name: null } });
    await open(u);
    const menu = screen.getByRole("menu");
    expect(menu).toHaveTextContent("?");
    expect(menu).toHaveTextContent("Member");
  });

  it("opens and closes, and Log out still logs out", async () => {
    const u = userEvent.setup();
    renderTopbar({ user: { name: "Vihari Rao" } });
    await open(u);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await open(u);
    await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
    await open(u);
    await u.click(screen.getByRole("menuitem", { name: /Log out/ }));
    expect(auth.logout).toHaveBeenCalledTimes(1);
  });

  it("sits above the pages' own sticky z-20 bars, on the shared header layer", () => {
    const { container } = renderTopbar({ user: { name: "Vihari Rao" } });
    const header = container.querySelector("header");
    expect(header.className).toContain(z.header);
    expect(header.className).not.toMatch(/\bz-20\b/);
  });
});
