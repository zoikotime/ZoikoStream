import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), patch: vi.fn() },
  errMsg: (error) => error?.message ?? "failed",
}));
vi.mock("./RetentionPolicyPanel", () => ({ default: () => null }));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import Settings from "./Settings";

const settings = {
  brand: { value: { name: "ZoikoStream", primary_color: "#8b5cf6", support_email: "support@example.com" } },
  storage_limits: { value: {} },
  streaming_limits: { value: {} },
  global: { value: {} },
};

async function openSettings() {
  vi.mocked(api.get).mockResolvedValue({ data: settings });
  render(<ThemeProvider><Settings /></ThemeProvider>);
  return screen.findByRole("button", { name: "Choose primary color" });
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.patch).mockResolvedValue({ data: {} });
});

describe("Platform Configuration primary color picker", () => {
  it("opens inline so the remaining settings are pushed down instead of covered", async () => {
    const trigger = await openSettings();
    fireEvent.click(trigger);

    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const picker = screen.getByRole("group", { name: "Primary color picker" });
    expect(picker).toHaveClass("mt-3");
    expect(picker.className).not.toMatch(/\b(?:absolute|fixed)\b/);
    expect(screen.getByText("Storage & Streaming Limits")).toBeInTheDocument();
  });

  it("synchronizes hex edits and picker selection through the saved form value", async () => {
    const trigger = await openSettings();
    const hex = screen.getByRole("textbox", { name: "Primary color hex value" });
    fireEvent.change(hex, { target: { value: "#123abc" } });
    expect(hex).toHaveValue("#123abc");

    fireEvent.click(trigger);
    fireEvent.change(screen.getByRole("slider", { name: "Primary color hue" }), {
      target: { value: "120" },
    });
    expect(hex.value).toMatch(/^#[0-9a-f]{6}$/i);
    expect(hex).not.toHaveValue("#123abc");

    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith(
      "/admin/settings",
      expect.objectContaining({
        values: expect.objectContaining({
          brand: expect.objectContaining({ primary_color: hex.value }),
        }),
      }),
    ));
  });

  it("leaves invalid typed text untouched until a valid picker selection is made", async () => {
    const trigger = await openSettings();
    const hex = screen.getByRole("textbox", { name: "Primary color hex value" });
    fireEvent.change(hex, { target: { value: "not-a-color" } });
    expect(hex).toHaveValue("not-a-color");

    fireEvent.click(trigger);
    fireEvent.change(screen.getByRole("slider", { name: "Primary color hue" }), {
      target: { value: "200" },
    });
    expect(hex.value).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it("closes on Escape or an outside click and restores focus on Escape", async () => {
    const trigger = await openSettings();
    fireEvent.click(trigger);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("group", { name: "Primary color picker" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();

    fireEvent.click(trigger);
    fireEvent.pointerDown(screen.getByText("Storage & Streaming Limits"));
    expect(screen.queryByRole("group", { name: "Primary color picker" })).not.toBeInTheDocument();
  });

  it("supports keyboard changes to saturation and brightness", async () => {
    const trigger = await openSettings();
    fireEvent.click(trigger);
    const valueArea = screen.getByRole("slider", { name: "Color saturation and brightness" });
    const hex = screen.getByRole("textbox", { name: "Primary color hex value" });
    const original = hex.value;
    fireEvent.keyDown(valueArea, { key: "ArrowUp" });
    expect(hex.value).not.toBe(original);
  });
});
