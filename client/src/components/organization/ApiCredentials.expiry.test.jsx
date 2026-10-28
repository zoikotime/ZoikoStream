// Settings -> Developer -> Key inventory: expiry the reader can act on.
//
// It showed a bare date ("9/25/2026") for a key that had expired three weeks earlier, beside a
// status worked out from the browser's clock with Math.ceil — so a key expired a few hours ago
// read "Expires in 0d" and counted as Active. The server now states each key's lifecycle
// (crud.admin.key_lifecycle) and the counts (key_summary); the page says how long is left,
// warns ahead of expiry, and offers an overlapping replacement — never an extension.
import { act, render, screen, waitFor, within } from "@testing-library/react";
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
import { exactExpiry, expiryDate } from "../../data/apiKeyExpiry";

const DAY = 86_400;
const NOW = "2026-10-16T12:00:00Z";
const key = (id, label, status, seconds, warning = null, extra = {}) => ({
  id, label, prefix: `zk_live_${id}`, fingerprint: "AB12-CD34",
  created_at: "2026-08-01T10:00:00Z",
  expires_at: seconds == null ? null : new Date(Date.parse(NOW) + seconds * 1000).toISOString(),
  revoked: status === "revoked", status, usable: status === "active" || status === "expiring_soon",
  seconds_until_expiry: seconds, expiry_warning: warning, ...extra,
});

const KEYS = [
  key("t1", "testing", "expired", -21 * DAY),
  key("p1", "prod", "expiring_soon", 7 * DAY, "urgent"),
  key("f1", "far", "active", 60 * DAY),
  key("r1", "old laptop", "revoked", 30 * DAY),
];
const SUMMARY = { total: 4, active: 2, expiring_30d: 1, expiring_7d: 1, expired: 1, revoked: 1, non_expiring: 0 };
const payload = (keys = KEYS, summary = SUMMARY) =>
  ({ api_keys: keys, webhooks: [], key_summary: summary, generated_at: NOW });

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.removeItem("theme");
  document.documentElement.classList.remove("dark");
  vi.mocked(api.get).mockResolvedValue({ data: payload() });
  vi.mocked(api.post).mockResolvedValue({
    data: { ...key("n1", "prod (replacement)", "active", null), key: "zk_live_NEW_SECRET_shown_once" },
  });
});

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <ApiCredentials embedded />
      </MemoryRouter>
    </ThemeProvider>
  );
const rowOf = (label) => screen.getByText(label, { selector: "p" }).closest("tr");

describe("each key says how long it has left", () => {
  it("leads with the time left, keeps the exact date, and never calls an expired key Active", async () => {
    show();
    await screen.findByText("testing");
    const expired = rowOf("testing");
    expect(within(expired).getByText("Expired 21 days ago")).toBeInTheDocument();
    expect(within(expired).getByText("Expired")).toBeInTheDocument();
    expect(within(expired).queryByText("Active")).toBeNull();
    // The date stays, in the viewer's own zone and locale.
    expect(within(expired).getByText(expiryDate(KEYS[0].expires_at))).toBeInTheDocument();
    // The Expires column and the stronger badge both say it.
    expect(within(rowOf("prod")).getAllByText("Expires in 7 days")).toHaveLength(2);
    expect(within(rowOf("far")).getByText("Expires in 60 days")).toBeInTheDocument();
    expect(within(rowOf("far")).getByText("Active")).toBeInTheDocument();
    expect(within(rowOf("old laptop")).getByText("Revoked")).toBeInTheDocument();
    expect(within(rowOf("old laptop")).getByText("No longer usable")).toBeInTheDocument();     // no countdown
  });

  it("states the exact local moment and the canonical UTC instant on hover", async () => {
    show();
    await screen.findByText("testing");
    const cell = within(rowOf("testing")).getByText("Expired 21 days ago").parentElement;
    expect(cell).toHaveAttribute("title", `${exactExpiry(KEYS[0].expires_at)} · 2026-09-25T12:00:00Z (UTC)`);
  });

  it("advances the relative date and status from the same expiry instant while the page is open", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse(NOW));
    const expiring = key("edge", "boundary", "active", 30);
    vi.mocked(api.get).mockResolvedValue({
      data: payload([expiring], { total: 1, active: 1, expiring_30d: 0, expiring_7d: 0, expired: 0, revoked: 0, non_expiring: 0 }),
    });
    try {
      show();
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      const row = rowOf("boundary");
      expect(within(row).getByText("Expires within the hour")).toBeInTheDocument();

      await act(async () => {
        await vi.advanceTimersByTimeAsync(60_000);
      });

      expect(within(row).getByText("Expired less than an hour ago")).toBeInTheDocument();
      expect(within(row).getByText("Expired")).toBeInTheDocument();
      expect(within(row).getByText(expiryDate(expiring.expires_at))).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("the counts", () => {
  it("come from the server's summary: expired keys never count as active", async () => {
    show();
    await screen.findByText("testing");
    const card = (label) => screen.getAllByText(label)[0].closest("div").parentElement.textContent;
    expect(card("Active")).toMatch(/2/);
    expect(card("Expiring within 30 days")).toMatch(/1/);
    expect(screen.getAllByText("Expired").length).toBeGreaterThan(0);
  });
});

describe("the status filter", () => {
  it.each([
    ["Expiring soon", ["prod"]],
    ["Expired", ["testing"]],
    ["Active", ["far"]],
    ["Revoked", ["old laptop"]],
  ])("narrows to %s", async (option, labels) => {
    const user = userEvent.setup();
    show();
    await screen.findByText("testing");
    await user.click(screen.getByRole("button", { name: "Status" }));
    await user.click(within(screen.getByRole("listbox")).getByRole("option", { name: option }));
    const shown = ["testing", "prod", "far", "old laptop"].filter(
      (l) => screen.queryByText(l, { selector: "p" }));
    expect(shown).toEqual(labels);
  });
});

describe("advance warning and overlapping rotation", () => {
  it("warns while a usable key is inside its last 7 days, and offers a replacement", async () => {
    show();
    const banner = await screen.findByTestId("key-expiry-banner");
    expect(banner).toHaveAttribute("role", "status");
    expect(banner).toHaveTextContent(
      "1 API key expires within 7 days. Create a replacement key and update your integration before it expires.");
    expect(within(banner).getByRole("button", { name: /create replacement key/i })).toBeInTheDocument();
  });

  it("creates a NEW key alongside the old one — the old key is not touched, extended or revealed", async () => {
    const user = userEvent.setup();
    show();
    const banner = await screen.findByTestId("key-expiry-banner");
    await user.click(within(banner).getByRole("button", { name: /create replacement key/i }));
    const dialog = await screen.findByRole("dialog", { name: "Create a replacement key" });
    expect(within(dialog).getByLabelText("Label")).toHaveValue("prod (replacement)");
    expect(within(dialog).getByRole("list", { name: "Rotation steps" })).toHaveTextContent(/Revoke the old key/);
    expect(dialog).toHaveTextContent(/stays as it is until you revoke it or it expires/);

    await user.click(within(dialog).getByRole("button", { name: "Create replacement" }));
    await waitFor(() => expect(api.post).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.post).mock.calls[0]).toEqual([
      "/organization/developer/api-keys", { label: "prod (replacement)", expires_in_days: null }]);
    expect(api.delete).not.toHaveBeenCalled();                       // revoking is the developer's step 4
    expect(api.patch).not.toHaveBeenCalled();                        // nothing extends the old key
    expect(await screen.findByText("zk_live_NEW_SECRET_shown_once")).toBeInTheDocument();
  });

  it("offers a replacement on expiring and expired rows only", async () => {
    show();
    await screen.findByText("testing");
    expect(screen.getByRole("button", { name: "Create a replacement for prod" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create a replacement for testing" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Create a replacement for far" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Create a replacement for old laptop" })).toBeNull();
  });

  it("says nothing when no usable key is expiring", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload(
      [KEYS[0], KEYS[2], KEYS[3]],
      { total: 3, active: 1, expiring_30d: 0, expiring_7d: 0, expired: 1, revoked: 1, non_expiring: 0 }) });
    show();
    await screen.findByText("testing");
    expect(screen.queryByTestId("key-expiry-banner")).toBeNull();
  });

  it("uses the 30-day wording when nothing is inside 7 days", async () => {
    vi.mocked(api.get).mockResolvedValue({ data: payload(
      [key("s1", "soon", "expiring_soon", 20 * DAY, "notice")],
      { total: 1, active: 1, expiring_30d: 1, expiring_7d: 0, expired: 0, revoked: 0, non_expiring: 0 }) });
    show();
    expect(await screen.findByTestId("key-expiry-banner")).toHaveTextContent("1 API key expires within 30 days.");
    expect(within(rowOf("soon")).getByText("Expiring soon")).toBeInTheDocument();
  });
});

describe.each([["light", false], ["dark", true]])("%s mode", (theme, dark) => {
  it("renders the warning states", async () => {
    if (dark) localStorage.setItem("theme", "dark");
    show();
    const banner = await screen.findByTestId("key-expiry-banner");
    expect(document.documentElement.classList.contains("dark")).toBe(dark);
    expect(banner.className).toMatch(/bg-amber-50/);
    expect(banner.className).toMatch(/dark:bg-amber-500/);
    expect(within(rowOf("testing")).getByText("Expired 21 days ago").className).toMatch(/text-rose-600 dark:text-rose-400/);
  });
});
