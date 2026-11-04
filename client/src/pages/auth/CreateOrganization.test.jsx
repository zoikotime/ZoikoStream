// Register: every required field is always present, one per row; the OPTIONAL slug waits
// behind a disclosure (it is what kept the desktop card from fitting the viewport).
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", async (importOriginal) => {
  const real = await importOriginal();
  return { ...real, default: { get: vi.fn(), post: vi.fn() } };
});
vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import api from "../../api";
import CreateOrganization from "./CreateOrganization";

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={["/signup"]}>
      <CreateOrganization />
    </MemoryRouter>,
  );

beforeEach(() => vi.clearAllMocks());

describe("the registration form", () => {
  it("shows every required field, and the slug only on request", () => {
    renderPage();
    for (const label of ["Organization Name", "Organization Admin Name", "Work Email", "Password", "Confirm Password"]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
    expect(screen.queryByLabelText("Organization Slug (optional)")).toBeNull();
    expect(screen.getByRole("button", { name: "Create Organization" })).toBeInTheDocument();

    const toggle = screen.getByRole("button", { name: "Customize event link (optional)" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(toggle);
    expect(screen.getByLabelText("Organization Slug (optional)")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Customize event link (optional)" })).toBeNull();
  });

  it("still validates and submits exactly what it did before", async () => {
    vi.mocked(api.post).mockResolvedValue({ data: { status: "EMAIL_VERIFICATION_REQUIRED", email: "a***@acme.example", expires_in_minutes: 30 } });
    renderPage();
    fireEvent.click(screen.getByRole("button", { name: "Create Organization" }));
    expect(await screen.findByText("Organization name is required.")).toBeInTheDocument();
    expect(api.post).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText("Organization Name"), { target: { value: "Acme Inc." } });
    fireEvent.change(screen.getByLabelText("Organization Admin Name"), { target: { value: "Ada Admin" } });
    fireEvent.change(screen.getByLabelText("Work Email"), { target: { value: "ada@acme.example" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "correct-horse" } });
    fireEvent.change(screen.getByLabelText("Confirm Password"), { target: { value: "correct-horse" } });
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.click(screen.getByRole("button", { name: "Create Organization" }));

    await waitFor(() => expect(api.post).toHaveBeenCalledWith("/auth/register", {
      full_name: "Ada Admin", organization_name: "Acme Inc.", email: "ada@acme.example", password: "correct-horse",
    }));
  });
});
