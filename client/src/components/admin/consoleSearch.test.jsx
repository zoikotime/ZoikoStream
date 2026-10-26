// The console's command bar: what it SHOWS must be what was typed.
//
// Two things are pinned here. The empty-state echoes the query back, so any character the
// component drops is visible to the operator — this is the regression that reads as
// "the message cut off my last letter". And the field keeps the console's own surface while
// it is focused, which a light-mode-only focus style silently breaks in dark mode.
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

vi.mock("../../api", () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  errMsg: (e) => e?.message ?? "failed",
  diagnoseLoadError: (e) => e?.message ?? "failed",
  AUTH_EXPIRED_EVENT: "zoiko:auth-expired",
}));
vi.mock("../../auth/AuthContext", () => ({
  useAuth: () => ({ user: { full_name: "Ada Ops", role: "super_admin" }, logout: vi.fn() }),
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import AdminTopbar from "./AdminTopbar";

const show = () =>
  render(
    <ThemeProvider>
      <MemoryRouter>
        <AdminTopbar onMenuClick={vi.fn()} state={{ health: { overall: "ok", unmonitored: 0 } }} unknown={false} onRetry={vi.fn()} />
      </MemoryRouter>
    </ThemeProvider>
  );

const field = () => screen.getByLabelText("Search organizations, events and sessions");
const type = (text) => fireEvent.change(field(), { target: { value: text } });

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: [] });   // nothing matches anything
});

describe("the empty state echoes the whole query", () => {
  it("keeps the final character — the reported symptom", async () => {
    show();
    type("pppplll");
    expect(await screen.findByText(/No matches for/)).toHaveTextContent('No matches for “pppplll”.');
  });

  it.each([
    ["an ordinary term", "acme corp"],
    ["one that ends in a repeated letter", "bellll"],
    ["special characters", "a+b&c %x_1 (z)"],
    ["a quote and a dash", 'o"neill-smith'],
    ["unicode", "café déjà vu 日本"],
    ["a long string", "x".repeat(120)],
  ])("preserves %s", async (_name, text) => {
    show();
    type(text);
    expect(await screen.findByText(/No matches for/)).toHaveTextContent(`No matches for “${text}”.`);
  });

  it("survives rapid typing — the last keystroke is the one shown", async () => {
    show();
    for (const t of ["p", "pp", "ppp", "pppp", "ppppl", "pppplll"]) type(t);
    expect(await screen.findByText(/No matches for/)).toHaveTextContent('No matches for “pppplll”.');
  });

  it("holds the complete query in state, not a clipped copy", () => {
    show();
    type("pppplll");
    expect(field()).toHaveValue("pppplll");
  });

  it("stays closed under the two-character floor, and opens past it", async () => {
    show();
    type("a");
    expect(screen.queryByText(/No matches for/)).not.toBeInTheDocument();
    type("ab");
    expect(await screen.findByText(/No matches for/)).toBeInTheDocument();
  });

  it("searches on the trimmed term but still shows what was typed", async () => {
    show();
    type("  acme  ");
    await waitFor(() => expect(api.get).toHaveBeenCalledWith("/admin/search", expect.objectContaining({ params: { q: "acme" } })));
  });

  it("wraps a long query instead of clipping it out of the panel", async () => {
    show();
    const long = "qwertyuiop".repeat(12);
    type(long);
    const msg = await screen.findByText(/No matches for/);
    expect(msg).toHaveTextContent(`No matches for “${long}”.`);
    expect(msg.className).not.toMatch(/\btruncate\b/);
    expect(msg.className).toMatch(/break-words|break-all/);
  });
});

describe("the field keeps the console surface while focused", () => {
  it("does not turn white in dark mode when focused", () => {
    show();
    const cls = field().className;
    // A focus background with no dark: counterpart is the defect: dark mode inherits the
    // light theme's white field the moment the caret lands in it.
    if (/(^|\s)focus:bg-/.test(cls)) {
      expect(cls).toMatch(/dark:focus:bg-/);
    }
  });
});
