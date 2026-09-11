// API-key management, after Settings' lite duplicate was replaced by this component.
//
// The point of these is that there is now exactly ONE implementation and ONE route pair.
// The failure mode being guarded against is subtle: two screens over one `org.api_keys`
// column, where the Settings copy could not show expiry or lifecycle state at all, so the
// same key looked different depending on where you opened it.
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

vi.mock("../../ui/Toast", () => ({
  notify: { error: vi.fn(), success: vi.fn(), alert: vi.fn() },
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import ApiCredentials from "./ApiCredentials";
import Credentials from "../../pages/organization/Credentials";

const KEYS = {
  api_keys: [
    {
      id: "k1",
      label: "Production server",
      prefix: "zk_live_abc",
      created_at: "2026-08-01T10:00:00Z",
      expires_at: "2026-12-01T10:00:00Z",
      revoked: false,
    },
    {
      id: "k2",
      label: "Retired laptop",
      prefix: "zk_live_xyz",
      created_at: "2026-05-01T10:00:00Z",
      expires_at: null,
      revoked: true,
    },
  ],
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: KEYS });
  vi.mocked(api.post).mockResolvedValue({ data: { id: "k3", key: "zk_live_secret_shown_once" } });
  vi.mocked(api.delete).mockResolvedValue({ data: {} });
});

const renderIn = (ui) =>
  render(
    <ThemeProvider>
      <MemoryRouter>{ui}</MemoryRouter>
    </ThemeProvider>
  );

describe("one implementation, two mount points", () => {
  it("reads the same endpoint whether standalone or embedded", async () => {
    renderIn(<Credentials />);
    await screen.findByText("Production server");
    expect(vi.mocked(api.get).mock.calls[0][0]).toBe("/organization/developer");

    vi.clearAllMocks();
    vi.mocked(api.get).mockResolvedValue({ data: KEYS });

    renderIn(<ApiCredentials embedded />);
    await screen.findByText("Production server");
    expect(vi.mocked(api.get).mock.calls[0][0]).toBe("/organization/developer");
  });

  it("never touches the retired /organization/api-keys route pair", async () => {
    renderIn(<ApiCredentials embedded />);
    await screen.findByText("Production server");
    for (const [url] of vi.mocked(api.get).mock.calls) {
      expect(url).not.toBe("/organization/api-keys");
    }
  });

  it("drops the page header when embedded, keeps it standalone", async () => {
    const view = renderIn(<Credentials />);
    expect(await screen.findByRole("heading", { level: 1, name: /credentials/i })).toBeInTheDocument();
    view.unmount();

    renderIn(<ApiCredentials embedded />);
    await screen.findByText("Production server");
    // Settings' own Panel supplies the title there, so a second h1 would be a duplicate.
    expect(screen.queryByRole("heading", { level: 1 })).not.toBeInTheDocument();
  });
});

describe("the capabilities Settings' lite copy never had", () => {
  it("shows expiry and lifecycle state per key", async () => {
    renderIn(<ApiCredentials embedded />);
    await screen.findByText("Production server");
    // A revoked key reads as revoked rather than as just another row.
    expect(screen.getByText(/revoked/i)).toBeInTheDocument();
    // And a key with no expiry is called out rather than shown blank.
    expect(screen.getAllByText(/never|no expiry/i).length).toBeGreaterThan(0);
  });

  it("offers create and revoke from both mount points", async () => {
    renderIn(<ApiCredentials embedded />);
    await screen.findByText("Production server");
    expect(screen.getByRole("button", { name: /new key/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /refresh/i })).toBeInTheDocument();
  });

  it("creates through the developer route pair, with an optional expiry", async () => {
    renderIn(<ApiCredentials embedded />);
    await screen.findByText("Production server");

    await userEvent.click(screen.getByRole("button", { name: /new key/i }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.type(within(dialog).getByRole("textbox"), "CI runner");
    await userEvent.click(within(dialog).getByRole("button", { name: /create|generate/i }));

    expect(api.post).toHaveBeenCalledTimes(1);
    const [url, body] = vi.mocked(api.post).mock.calls[0];
    expect(url).toBe("/organization/developer/api-keys");
    expect(body).toHaveProperty("label", "CI runner");
    // The field exists even when left blank — that is the capability Settings lacked.
    expect(body).toHaveProperty("expires_in_days");
  });
});
