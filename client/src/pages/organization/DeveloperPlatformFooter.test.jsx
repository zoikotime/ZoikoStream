// The Developer Platform footer: Feedback, Accessibility and Terms are plain text (no
// destination exists yet); Security and Status are links.
//
// In the Android app the 44px touch-target floor (index.css) made each link 44px tall with its
// text pinned to the top, 13px above the plain text on the same row: the defect the Support &
// Status footer had. jsdom cannot lay that out; the geometry was measured in Chromium against
// the mobile build (320–768px: text level on every row, links still 44px; web unchanged).
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api", () => ({
  default: { get: vi.fn() },
  errMsg: (e) => e?.message ?? "Something went wrong.",
}));

import api from "../../api";
import { ThemeProvider } from "../../theme/ThemeContext";
import DeveloperPlatform from "./DeveloperPlatform";

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.get).mockResolvedValue({ data: {} });
});

function renderPage() {
  return render(
    <ThemeProvider>
      <MemoryRouter initialEntries={["/organization/developers"]}>
        <Routes>
          <Route path="/organization/developers" element={<DeveloperPlatform />} />
          <Route path="/organization/support" element={<h1>Support &amp; Status</h1>} />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>
  );
}

const footer = async () => (await screen.findByText(/All rights reserved/)).closest("footer");

describe("the Developer Platform footer", () => {
  it("keeps its destinations, and its plain-text items as text", async () => {
    renderPage();
    const f = await footer();
    const links = within(f).getAllByRole("link");
    expect(links.map((a) => [a.textContent.trim(), a.getAttribute("href")])).toEqual([
      ["Security", "/organization/settings?tab=security"],
      ["Status", "/organization/support"],
    ]);
    for (const label of ["Feedback", "Accessibility", "Terms"]) {
      expect(within(f).getByText(label).tagName).toBe("SPAN");
    }
  });

  it("centres every link's text in its touch target", async () => {
    renderPage();
    for (const a of within(await footer()).getAllByRole("link")) {
      expect(a.className.split(/\s+/)).toEqual(expect.arrayContaining(["inline-flex", "items-center"]));
      expect(a.className).toMatch(/focus-visible:/);
    }
  });

  it("still navigates from the keyboard", async () => {
    const user = userEvent.setup();
    renderPage();
    within(await footer()).getByRole("link", { name: "Status" }).focus();
    await user.keyboard("{Enter}");
    expect(await screen.findByRole("heading", { name: "Support & Status" })).toBeInTheDocument();
  });
});
