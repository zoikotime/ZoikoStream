// The "SA / Super Admin" row at the foot of the admin rail.
//
// Reported as hard to click, as if something covered it. Measured in Chrome at four sizes,
// nothing did: it sat fully on screen and the topmost element at its centre was its own text.
// It was simply not a control - a div with an avatar and a name, no button, no handler - while
// looking exactly like the account button in the topbar. It is now that account control: one
// button for the whole row, opening the same two actions as the topbar's avatar menu.
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
}));
const logout = vi.fn();
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Super Admin", email: "ops@zoikostream.com", role: "super_admin" }, logout }),
}));

import AdminSidebar from "./AdminSidebar";

function Where() {
  return <p data-testid="where">{useLocation().pathname}</p>;
}

const show = () => {
  const onClose = vi.fn();
  render(
    <MemoryRouter initialEntries={["/admin/dashboard"]}>
      <AdminSidebar open onClose={onClose} state={{}} unknown={false} onChange={vi.fn()} />
      <Routes><Route path="*" element={<Where />} /></Routes>
    </MemoryRouter>
  );
  return { onClose, button: screen.getByRole("button", { name: "Account menu" }) };
};

beforeEach(() => vi.clearAllMocks());

describe("Admin rail — the account control", () => {
  it("is one button for the whole row, with nothing interactive nested inside it", () => {
    const { button } = show();
    expect(button).toHaveTextContent("SA");
    expect(button).toHaveTextContent("Super Admin");
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(button.querySelector("button, a, input")).toBeNull();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it.each([["the avatar", "SA"], ["the name", "Super Admin"]])("opens the account menu from %s", (_, text) => {
    const { button } = show();
    fireEvent.click(within(button).getByText(text));
    const menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitem", { name: /Profile/ })).toBeInTheDocument();
    expect(within(menu).getByRole("menuitem", { name: /Log out/ })).toBeInTheDocument();
    expect(within(menu).getByText("ops@zoikostream.com")).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("opens upward, the only side with room at the foot of the rail", () => {
    const { button } = show();
    fireEvent.click(button);
    expect(screen.getByRole("menu")).toHaveClass("bottom-full");
  });

  it("works from the keyboard, and Escape returns focus to the button", async () => {
    const user = userEvent.setup();
    const { button } = show();
    button.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    expect(button).toHaveFocus();
  });

  it("Profile goes where the topbar's Profile goes, and closes the drawer", async () => {
    const { button, onClose } = show();
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: /Profile/ }));
    await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/admin/platform-configuration"));
    expect(onClose).toHaveBeenCalled();
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });

  it("Log out signs out through the same logout the topbar uses", () => {
    const { button } = show();
    fireEvent.click(button);
    fireEvent.click(screen.getByRole("menuitem", { name: /Log out/ }));
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("closes when the operator clicks elsewhere, and toggles from its own button", () => {
    const { button } = show();
    fireEvent.click(button);
    fireEvent.mouseDown(document.body);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});
